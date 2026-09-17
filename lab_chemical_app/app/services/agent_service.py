"""Agent loop — the core of the AI assistant.

run_agent() yields plain dicts; the route layer turns them into SSE frames.
Supports OpenRouter (OpenAI-compatible) and Gemini native providers,
with fallback to the legacy keyword-based path when tool calling is unavailable.
"""

import json
import logging
import time
from datetime import date, datetime
from typing import Iterator

import requests

from app.services.ai_service import (
    get_ai_provider,
    get_api_key,
    get_openrouter_model,
    get_gemini_model,
    load_app_settings,
)
from app.services.agent_tools import (
    tool_declarations_for_user,
    gemini_declarations_for_user,
    execute as execute_tool,
    tools_for_user,
)

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_WALL_CLOCK = 90
TOOL_TIMEOUT = 10

# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

_IDENTITY = """\
You are the QC assistant for **GCP QC Trace**, the quality-control and \
traceability system of a ductile-iron pressure-pipe plant working to \
ISO 2531 / EN 545.

Your job is to answer questions about production data, quality results, \
processes, and the application itself. You have tools that read from the \
live database. Use them — never guess numbers.

## What you can and cannot do

- You can READ any business table (pipes, ladles, stages, orders, tests, \
certificates, KPI alerts, audit log...) through your tools, and point the \
user to the right page in the app.
- You CANNOT create, edit, delete, approve or reject anything. You cannot \
send messages, print, export files, or change settings. If asked, say so \
in one sentence and link the page where the user can do it themselves.
- Do not claim abilities you do not have. If a question needs data you \
cannot reach with your tools, say exactly that."""

_DOMAIN_GLOSSARY = """\
## Domain glossary

- **Ladle / heat** — one melt, stored in `chemical_analyses`, identified by \
`ladle_id`. Carries the chemical composition and a decision. Pipes are cast \
from a ladle.
- **Pipe** — stored in `pipes`. Two identifiers: `no_code` (short serial, \
what operators say, e.g. N1234) and `pipe_code` (the long composite code). \
Plus `warehouse_barcode`.
- **Stages** — a pipe moves through production stages (DB-driven list from \
`production_stages`). Each visit is a `pipe_stages` row with a decision. \
Re-deciding overwrites and pushes the old value into `pipe_stage_history`. \
Rework analysis must check both tables. Stage measurements live on \
`pipe_stages` as JSON columns (dimension_profile on CCM, ovality_profile on \
Annealing, zinc_profile on Zinc, ring_profile on Cutting, thickness_profile \
on Coating, visual_profile on Finish) — describe_schema shows their shape.
- **Decision states** — WAITING, ACCEPT, HOLD, REJECT, BLOCKED. \
`lab_decision` is the lab verdict on the ladle's pipes (the populated signal \
on real data). `final_decision_value` is the closing verdict. Values are \
stored case-inconsistently; always compare case-insensitively.
- **Mechanical cascade** — a mechanical FAIL on a sample-tested ladle is \
always HOLD, never automatic scrap. An individual (non-sample) test never \
cascades.
- **Lab Approval** — a gate stage between Annealing and Zinc.
- **Chemical elements** — C, Si, Mn, P, S, Mg, Cu, Cr, Pb, Al with \
equivalents CE, MnE, MgE. Spec limits are in element_rules.json and checked \
by the get_ladle tool."""

_TOOL_POLICY = """\
## Tool policy

- Never state a number you did not get from a tool this turn. No estimates, \
no memory.
- Prefer a curated tool over run_sql when one fits; run_sql is for what the \
others cannot answer.
- Before writing SQL, call describe_schema for the tables involved. Do not \
guess column names.
- Chain freely: look something up, then drill in. Several tools per turn is \
normal and good.
- A tool returning an empty result means no matching data. Say that plainly. \
Never invent a plausible row.
- If a tool returns a permission error, that means the user's role does not \
allow that operation. Say so — do not claim data is missing.
- If the question is ambiguous in a way that changes the query (which date \
range? which DN?), ask one short clarifying question instead of guessing.
- Do not invent a date range the user did not give. If the user asks about \
"all pipes" or gives no time frame, omit date_from and date_to entirely — \
the tools will return all-time data.
- If find_page returns no results, say so. Never invent or guess a URL \
that was not returned by a tool."""

_OUTPUT_STYLE = """\
## Output style

- Answer in the user's language — Arabic, English, or French, matching the \
question.
- Use pure Markdown only. Never emit raw HTML tags (no <h1>, <h6>, <b>, \
<table>, <br>, etc.). Tables must use Markdown pipe syntax. Headings must \
use # prefix syntax. The client renders Markdown; raw HTML will display \
as literal angle brackets.
- Tables for tabular results. Lead with the answer, then the evidence.
- Keep it short. No filler, no restating the question.
- When you list pipes or ladles, include their identifiers so the user can \
act on them.
- When the answer implies an action the user should take in the app, link \
the page as a markdown link: [Title](/path)."""


# The editable part of the system prompt. Admins override it from
# /admin/settings/ai (key ``chatbot_system``); the dynamic sections below
# (current user, time, page map) are appended at request time and are not
# part of the override.
DEFAULT_CHATBOT_PROMPT = '\n\n'.join([
    _IDENTITY, _DOMAIN_GLOSSARY, _TOOL_POLICY, _OUTPUT_STYLE,
])


def get_chatbot_prompt_body() -> str:
    """The static prompt body: admin override if set, else the default."""
    from app.services.ai_service import get_prompt_template
    return get_prompt_template('chatbot_system', DEFAULT_CHATBOT_PROMPT)


def build_system_prompt(user) -> str:
    """Build the system prompt, tailored to this user."""
    from app.services.site_map import build_site_map
    from app.services.agent_tools import as_snapshot

    user = as_snapshot(user)
    parts = [get_chatbot_prompt_body()]

    # --- Dynamic sections (always appended, never part of the override) ---

    # User section
    parts.append(f"\n## Current user\n\nName: {user.full_name or user.username}\n"
                 f"Role: {user.role}\n"
                 f"Language preference: Arabic and English (bilingual system)")

    # Time section
    today = date.today()
    now = datetime.now()
    hour = now.hour
    shift = 1 if 6 <= hour < 14 else (2 if 14 <= hour < 22 else 3)
    parts.append(f"\n## Time\n\nToday: {today.isoformat()} ({today.strftime('%A')}), "
                 f"ISO week {today.isocalendar()[1]}, shift {shift}.\n"
                 f"Call `get_context` if you need exact counts or stage lists — do not assume.")

    # Site map — filtered to what this user may see
    try:
        site_map = build_site_map(user)
        areas = {}
        for page in site_map:
            area = page.get('area', 'Other')
            areas.setdefault(area, []).append(page)

        map_lines = ["\n## Application pages (filtered for your role)\n"]
        for area in sorted(areas.keys()):
            map_lines.append(f"\n### {area}\n")
            for p in areas[area]:
                map_lines.append(f"- [{p['title_en']}]({p['url']}) — {p['purpose']}")
        parts.append('\n'.join(map_lines))
    except Exception as e:
        logger.warning("Could not build site map for prompt: %s", e)

    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Tool-call result summariser
# ---------------------------------------------------------------------------

def _summarise_result(name: str, result: dict) -> str:
    """One-line human summary for the tool chip in the UI."""
    if 'error' in result:
        return f"Error: {result['error'][:100]}"

    if name == 'get_context':
        c = result.get('db_counts', {})
        return f"DB: {c.get('pipes', '?')} pipes, {c.get('chemical_analyses', '?')} ladles, shift {result.get('current_shift', '?')}"

    if name == 'describe_schema':
        tables = result.get('tables', {})
        n = len(tables)
        return f"{n} tables" if n > 5 else ', '.join(tables.keys())

    if name == 'run_sql':
        return f"{result.get('row_count', 0)} rows" + (" (truncated)" if result.get('truncated') else "")

    if name == 'get_ladle':
        oos = result.get('out_of_spec', [])
        d = result.get('decision', '?')
        return f"Ladle {result.get('ladle_id', '?')} — {d}" + (f", {len(oos)} out-of-spec" if oos else "")

    if name == 'get_pipe':
        return f"Pipe {result.get('no_code', '?')} — DN{result.get('diameter', '?')}, {result.get('lab_decision', '?')}"

    if name == 'get_order':
        return f"Order {result.get('order_number', '?')} — {result.get('produced', '?')}/{result.get('target_quantity', '?')} pipes"

    if name == 'search_pipes':
        return f"{result.get('total_matching', 0)} pipes found" + (" (truncated)" if result.get('truncated') else "")

    if name == 'production_summary':
        return f"{result.get('grand_total', 0)} pipes, {result.get('period', '')}"

    if name == 'defect_analysis':
        return f"{result.get('total_defects', 0)} defects, {result.get('period', '')}"

    if name == 'quality_stats':
        return f"{result.get('name', '?')}: n={result.get('n', 0)}, mean={result.get('mean', '?')}"

    if name == 'find_page':
        return f"{result.get('count', 0)} pages found"

    return json.dumps(result, default=str)[:100]


def _preview_result(name: str, result: dict) -> dict | None:
    """Optional tabular preview for the UI, capped at 10 rows / 8 columns."""
    if name == 'run_sql' and 'columns' in result and 'rows' in result:
        cols = result['columns'][:8]
        rows = [row[:8] for row in result['rows'][:10]]
        return {"columns": cols, "rows": rows}

    if name == 'search_pipes' and 'pipes' in result:
        pipes = result['pipes'][:10]
        if pipes:
            cols = list(pipes[0].keys())[:8]
            rows = [[p.get(c) for c in cols] for p in pipes]
            return {"columns": cols, "rows": rows}

    return None


# ---------------------------------------------------------------------------
# OpenRouter agent loop (OpenAI-compatible tool calling)
# ---------------------------------------------------------------------------

def _openrouter_stream_round(api_key, model, messages, tools, temperature=0.4):
    """Stream one call to OpenRouter. Yields text deltas as they arrive.

    Returns a collector object whose .tool_calls and .finish_reason are
    populated as side effects of iterating.  The caller must drain the
    iterator (for text deltas) and then read .tool_calls.
    """
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://gcpipes.com",
        "X-Title": "GCP QC Pipes Traceability",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 4096,
        "stream": True,
        "provider": {"allow_fallbacks": True},
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers, json=payload, timeout=(10, 80), stream=True,
    )
    resp.raise_for_status()

    return _StreamCollector(resp)


class _StreamCollector:
    """Iterate for text deltas; read .tool_calls / .content after draining."""

    def __init__(self, resp):
        self._resp = resp
        self.tool_calls = []
        self.content = ""
        self.finish_reason = None
        self._tool_calls_accum = {}

    def __iter__(self):
        content_parts = []
        for line in self._resp.iter_lines():
            if not line:
                continue
            decoded = line.decode("utf-8")
            if not decoded.startswith("data: "):
                continue
            data_str = decoded[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = data.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            fr = choice.get("finish_reason")
            if fr:
                self.finish_reason = fr

            text_chunk = delta.get("content")
            if text_chunk:
                content_parts.append(text_chunk)
                yield text_chunk

            # F19: tool-call delta accumulation with hardening
            tc_deltas = delta.get("tool_calls") or []
            for tcd in tc_deltas:
                idx = tcd.get("index", 0)
                new_id = tcd.get("id", "")
                if idx not in self._tool_calls_accum:
                    self._tool_calls_accum[idx] = {
                        "id": new_id,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                elif new_id and new_id != self._tool_calls_accum[idx]["id"]:
                    idx = max(self._tool_calls_accum.keys()) + 1
                    self._tool_calls_accum[idx] = {
                        "id": new_id,
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                acc = self._tool_calls_accum[idx]
                if new_id:
                    acc["id"] = new_id
                fn = tcd.get("function") or {}
                if fn.get("name"):
                    acc["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    acc["function"]["arguments"] += fn["arguments"]

        self.content = ''.join(content_parts)
        self.tool_calls = [self._tool_calls_accum[i]
                           for i in sorted(self._tool_calls_accum.keys())] if self._tool_calls_accum else []


def _run_agent_openrouter(message, history, user, session_id, max_steps=MAX_STEPS) -> Iterator[dict]:
    """OpenRouter / OpenAI-compatible agent loop."""
    api_key = get_api_key()
    model = get_openrouter_model()
    system_prompt = build_system_prompt(user)
    tools_decl = tool_declarations_for_user(user)

    messages = [{"role": "system", "content": system_prompt}]

    for h in (history or [])[-20:]:
        role = h.get("role", "user")
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": h.get("content", "")})
        elif role == "tool":
            messages.append(h)

    messages.append({"role": "user", "content": message})

    step = 0
    tools_used = []
    seen_calls = {}
    t0 = time.monotonic()

    while step < max_steps:
        if time.monotonic() - t0 > MAX_WALL_CLOCK:
            yield {"error": "Agent timed out (90s wall clock). Partial results may be above."}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return

        try:
            collector = _openrouter_stream_round(
                api_key, model, messages, tools_decl if step < max_steps - 1 else [],
            )
            # F9: stream text deltas to the client as they arrive
            for delta in collector:
                yield {"chunk": delta}
        except requests.exceptions.HTTPError as e:
            resp_body = ''
            if e.response is not None:
                try:
                    resp_body = e.response.text or ''
                except Exception:
                    pass
            status = e.response.status_code if e.response is not None else 0
            # F14: inspect e.response.text, not str(e)
            if status in (400, 422) and 'tool' in resp_body.lower():
                yield {"notice": "Model does not support tool calling — using basic mode."}
                yield from _fallback_stream(message, history, user)
                return
            yield {"error": f"AI provider error: {e}"}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return
        except Exception as e:
            yield {"error": f"AI provider error: {e}"}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return

        tool_calls = collector.tool_calls
        content = collector.content

        if tool_calls:
            assistant_msg = {"role": "assistant", "content": content or None, "tool_calls": tool_calls}
            messages.append(assistant_msg)

            for tc in tool_calls:
                tc_id = tc.get("id", "")
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_str = fn.get("arguments", "{}")

                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}
                    result = {"error": f"Malformed arguments: {args_str[:200]}"}
                    yield {"tool_call": {"id": tc_id, "step": step + 1, "name": name, "args": {}}}
                    yield {"tool_result": {"id": tc_id, "ok": False, "name": name, "summary": result["error"][:100], "ms": 0}}
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": json.dumps(result, default=str)})
                    continue

                yield {"tool_call": {"id": tc_id, "step": step + 1, "name": name, "args": args}}

                # F12: enforce per-tool timeout
                call_key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                if call_key in seen_calls:
                    result = seen_calls[call_key]
                    result_with_note = {**result, "_note": "Cached — you already called this with the same arguments."}
                    ms = 0
                else:
                    t_tool = time.monotonic()
                    result = execute_tool(name, args, user)
                    ms = int((time.monotonic() - t_tool) * 1000)
                    if ms > TOOL_TIMEOUT * 1000:
                        logger.warning("Tool %s took %dms (limit %ds)", name, ms, TOOL_TIMEOUT)
                    seen_calls[call_key] = result
                    result_with_note = result

                ok = 'error' not in result
                summary = _summarise_result(name, result)
                preview = _preview_result(name, result)
                if name not in tools_used:
                    tools_used.append(name)

                yield {
                    "tool_result": {
                        "id": tc_id,
                        "ok": ok,
                        "name": name,
                        "summary": summary,
                        "preview": preview,
                        "ms": ms,
                    }
                }

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": json.dumps(result_with_note, default=str),
                })

                # F12: check wall clock after each tool
                if time.monotonic() - t0 > MAX_WALL_CLOCK:
                    yield {"error": "Agent timed out after tool execution."}
                    yield {"done": True, "steps": step + 1, "tools_used": tools_used}
                    return

            step += 1
            continue

        # Text response — streaming already happened above
        yield {"done": True, "steps": step, "tools_used": tools_used}
        return

    # Hit max steps — force a final answer
    messages.append({
        "role": "system",
        "content": "You have reached the maximum number of tool calls. Answer now with what you have. Do not call any more tools.",
    })
    try:
        collector = _openrouter_stream_round(api_key, model, messages, [])
        for delta in collector:
            yield {"chunk": delta}
    except Exception as e:
        yield {"error": f"Final answer failed: {e}"}
    yield {"done": True, "steps": step, "tools_used": tools_used}


# ---------------------------------------------------------------------------
# Gemini native agent loop
# ---------------------------------------------------------------------------

def _gemini_stream_round(api_key, model, contents, tools_decl, temperature=0.4):
    """One call to Gemini's generateContent. Returns (text, function_calls)."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    body = {
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }
    if tools_decl:
        body["tools"] = [{"function_declarations": tools_decl}]

    resp = requests.post(url, headers={"Content-Type": "application/json"},
                         json=body, timeout=MAX_WALL_CLOCK)
    resp.raise_for_status()
    result = resp.json()

    candidates = result.get("candidates", [])
    if not candidates:
        return "", []

    parts = candidates[0].get("content", {}).get("parts", [])
    text_parts = []
    function_calls = []
    for part in parts:
        if "text" in part:
            text_parts.append(part["text"])
        if "functionCall" in part:
            fc = part["functionCall"]
            function_calls.append({"name": fc.get("name", ""), "args": fc.get("args", {})})

    return ''.join(text_parts), function_calls


def _run_agent_gemini(message, history, user, session_id, max_steps=MAX_STEPS) -> Iterator[dict]:
    """Gemini native agent loop."""
    api_key = get_api_key()
    model = get_gemini_model()
    system_prompt = build_system_prompt(user)
    tools_decl = gemini_declarations_for_user(user)

    contents = [
        {"role": "user", "parts": [{"text": f"[System]\n{system_prompt}\n\n[User]\nمرحباً"}]},
        {"role": "model", "parts": [{"text": "مرحباً! أنا مساعدك الذكي لنظام تحليل المعمل. كيف يمكنني مساعدتك اليوم؟"}]},
    ]

    for h in (history or [])[-20:]:
        role = "user" if h.get("role") == "user" else "model"
        content = h.get("content", "")
        if content:
            contents.append({"role": role, "parts": [{"text": content}]})

    contents.append({"role": "user", "parts": [{"text": message}]})

    step = 0
    tools_used = []
    seen_calls = {}
    t0 = time.monotonic()

    while step < max_steps:
        if time.monotonic() - t0 > MAX_WALL_CLOCK:
            yield {"error": "Agent timed out (90s wall clock)."}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return

        try:
            text, function_calls = _gemini_stream_round(
                api_key, model, contents,
                tools_decl if step < max_steps - 1 else [],
            )
        except requests.exceptions.HTTPError as e:
            resp_body = ''
            if e.response is not None:
                try:
                    resp_body = e.response.text or ''
                except Exception:
                    pass
            status = e.response.status_code if e.response is not None else 0
            # F14: only degrade on tool-specific errors, not all 400s
            if status in (400, 422) and any(kw in resp_body.lower()
                                            for kw in ('function', 'tool', 'declaration')):
                yield {"notice": "Model does not support tool calling — using basic mode."}
                yield from _fallback_stream(message, history, user)
                return
            yield {"error": f"AI provider error: {e}"}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return
        except Exception as e:
            yield {"error": f"AI provider error: {e}"}
            yield {"done": True, "steps": step, "tools_used": tools_used}
            return

        if function_calls:
            # Append the model's response with function calls
            model_parts = []
            if text:
                model_parts.append({"text": text})
            for fc in function_calls:
                model_parts.append({"functionCall": {"name": fc["name"], "args": fc["args"]}})
            contents.append({"role": "model", "parts": model_parts})

            # Execute each function call
            response_parts = []
            for fc in function_calls:
                name = fc["name"]
                args = fc.get("args", {})

                yield {"tool_call": {"id": name, "step": step + 1, "name": name, "args": args}}

                call_key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
                if call_key in seen_calls:
                    result = seen_calls[call_key]
                    result_with_note = {**result, "_note": "Cached."}
                    ms = 0
                else:
                    t_tool = time.monotonic()
                    result = execute_tool(name, args, user)
                    ms = int((time.monotonic() - t_tool) * 1000)
                    seen_calls[call_key] = result
                    result_with_note = result

                ok = 'error' not in result
                summary = _summarise_result(name, result)
                preview = _preview_result(name, result)
                if name not in tools_used:
                    tools_used.append(name)

                yield {"tool_result": {"id": name, "ok": ok, "name": name, "summary": summary, "preview": preview, "ms": ms}}

                response_parts.append({
                    "functionResponse": {
                        "name": name,
                        "response": result_with_note,
                    }
                })

                # F12: check wall clock after each tool
                if time.monotonic() - t0 > MAX_WALL_CLOCK:
                    yield {"error": "Agent timed out after tool execution."}
                    yield {"done": True, "steps": step + 1, "tools_used": tools_used}
                    return

            contents.append({"role": "user", "parts": response_parts})
            step += 1
            continue

        # Text response
        if text:
            yield {"chunk": text}
        yield {"done": True, "steps": step, "tools_used": tools_used}
        return

    # Max steps — force final answer
    contents.append({"role": "user", "parts": [{"text": "Answer now with what you have. Do not call any more tools."}]})
    try:
        text, _ = _gemini_stream_round(api_key, model, contents, [])
        if text:
            yield {"chunk": text}
    except Exception as e:
        yield {"error": f"Final answer failed: {e}"}
    yield {"done": True, "steps": step, "tools_used": tools_used}


# ---------------------------------------------------------------------------
# Fallback — legacy path (no tool calling)
# ---------------------------------------------------------------------------

def _fallback_stream(message, history, user) -> Iterator[dict]:
    """Fallback to the old keyword-based path when tool calling isn't available."""
    from app.services.chatbot_context_service import gather_context
    from app.services.ai_service import generate_chatbot_stream

    db_context = gather_context(message)
    username = user.full_name or user.username

    for frame in generate_chatbot_stream(message, history=history, username=username, db_context=db_context):
        # Translate the old SSE format to our dict protocol
        if isinstance(frame, str) and frame.startswith("data: "):
            try:
                data = json.loads(frame[6:].strip())
                yield data
            except json.JSONDecodeError:
                pass
        elif isinstance(frame, dict):
            yield frame


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_agent(message: str, history: list, user, session_id: int,
              max_steps: int = MAX_STEPS) -> Iterator[dict]:
    """Run the AI agent. Yields event dicts for the SSE stream.

    Event types:
        {"session_id": int}
        {"notice": str}
        {"tool_call": {"id", "step", "name", "args"}}
        {"tool_result": {"id", "ok", "name", "summary", "preview", "ms"}}
        {"chunk": str}
        {"done": true, "steps": int, "tools_used": [...]}
        {"error": str}
    """
    yield {"session_id": session_id}

    provider = get_ai_provider()

    # The caller should already have snapshotted; do it here too so a live User
    # never reaches the loop, where the request context is gone and reading one
    # raises DetachedInstanceError.
    from app.services.agent_tools import as_snapshot
    user = as_snapshot(user)

    try:
        if provider == "openrouter":
            yield from _run_agent_openrouter(message, history, user, session_id, max_steps)
        elif provider == "gemini":
            yield from _run_agent_gemini(message, history, user, session_id, max_steps)
        else:
            yield {"notice": f"Unknown provider '{provider}' — using basic mode."}
            yield from _fallback_stream(message, history, user)
    except Exception as e:
        logger.exception("Agent loop crashed: %s", e)
        yield {"error": f"Agent error: {str(e)}"}
        yield {"done": True, "steps": 0, "tools_used": []}
