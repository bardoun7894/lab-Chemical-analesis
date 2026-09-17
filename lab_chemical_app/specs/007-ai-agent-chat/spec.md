# 007 — Agentic AI Assistant + Chat Redesign

**Branch:** `feat/ai-agent-chat` (already created, based on `sync/prod-2026-08-27` @ `156a36f`)
**Working dir:** `/root/work/lab-agent` on this server. This is a full rsync of the developer's
repo, not the live prod tree. **Never edit `/var/local/lab-Chemical-analesis/`.**

---

## 1. Goal

Turn `/chatbot/` from a keyword-matching Q&A box into a real **agent** — the ChatGPT / Claude
pattern, where the model decides what to look up, calls tools in a loop, and reasons over the
results. It must know the whole system: every table, every page, every domain rule. And the chat
UI gets rebuilt to match that capability.

Two halves, both required:

- **A. The agent** — tool calling, multi-step loop, full data + system knowledge.
- **B. The chat UI** — a modern conversational interface that shows the agent working.

---

## 2. What exists today (read these first)

| File | What it does |
|---|---|
| `app/routes/chatbot.py` | 234 lines. Blueprint: index, send, stream (SSE), new/delete session, suggestions. Sessions persist to DB. |
| `app/services/chatbot_context_service.py` | 366 lines. **The thing being replaced.** Regex/keyword intent detection → picks from 8 canned SQL queries → concatenates to a string capped at 2500 chars, injected into the prompt. |
| `app/services/ai_service.py` | 1384 lines. Provider abstraction (OpenRouter + Gemini). `generate_chatbot_response` @1199, `generate_chatbot_stream` @1287, `CHATBOT_SYSTEM_PROMPT` ends @1196. |
| `app/templates/chatbot/index.html` | 535 lines. Dark sidebar + message list + single-line `<input>`. Naive regex `formatMarkdown()`. **Being redesigned.** |
| `app/models/chat.py` | 56 lines. `ChatSession`, `ChatMessage`, `get_history_for_ai()`. |

### Why the current design is the problem

1. Context is chosen **before** the model sees the question, by keyword match. Six intent groups
   only (today / rejections / waiting / trend / chemical / delivery). Everything else — SPC,
   capability, rework, non-conformance, stages, warehouse, products, users, zinc, ring tests,
   traceability, orders by customer — gets **zero data** and the model guesses.
2. 2500-char cap. No follow-up, no drill-down, no computation.
3. No knowledge of the app's 192 pages. It can't say "open /reports/non-conformance".
4. `_query_today_summary` and friends key on `final_decision_value`; on real data `lab_decision`
   is the populated signal, so summaries report almost everything as "Pending". Fix while
   rewriting.

---

## 3. Environment — you have real data and a running app

A **dev container** is already running. It mounts this work dir at `/app` with `--reload`, so
every file you save is live within seconds.

| Thing | Value |
|---|---|
| Dev app | `http://91.230.110.187:9998/` (also `http://127.0.0.1:9998/` on this box) |
| Dev container | `lab-chemical-dev` |
| Dev DB | Postgres 16, database `lab_chemical_dev` — a **full copy of prod** (187 pipes, 77 ladles, 79 mechanical tests, 43 orders, 1276 stage-history rows, 9 users) |
| Login | `agentdev` / `agentdev123` (super_admin) |
| AI keys | Real, in `app/data/app_settings.json`. Provider is **`openrouter`**, model `google/gemini-2.5-flash-lite`. A Gemini key is also set. |

```bash
docker logs -f lab-chemical-dev              # watch errors
docker restart lab-chemical-dev              # after adding a dependency
docker exec -e LAB_SKIP_DB_BOOTSTRAP=1 lab-chemical-dev python -c "..."   # one-off scripts
docker exec lab-chemical-dev python -m pytest tests/test_agent_tools.py -q
docker exec lab-chemical-pg psql -U lab -d lab_chemical_dev -c "SELECT ..."
```

**Prod is `lab-chemical-prod` on :9999 with database `lab_chemical`. Do not touch either.**
Writing to `lab_chemical_dev` is fine — it is a throwaway clone.

### Hard constraints

- Never `git add app/data/` — `app_settings.json` and the `.bak*` files hold live API keys.
- Never edit `/var/local/lab-Chemical-analesis/`.
- Never run a migration against `lab_chemical` (prod). Only `lab_chemical_dev`.
- All agent tools are **read-only**. No tool may INSERT, UPDATE or DELETE. Not in v1.
- Every `flask db` command needs `LAB_SKIP_DB_BOOTSTRAP=1` in the environment.
- Commit as you go, on `feat/ai-agent-chat`, small logical commits. Do not merge anywhere.

---

## 4. Deliverables

```
app/services/agent_tools.py      NEW   tool registry + implementations + SQL guard
app/services/agent_service.py    NEW   the agent loop, both providers, SSE protocol
app/services/site_map.py         NEW   route → (title, purpose, permission) knowledge
app/routes/chatbot.py            EDIT  /stream rewired to the agent; tool-step persistence
app/models/chat.py               EDIT  ChatMessage.tool_calls JSON column
app/templates/chatbot/index.html REWRITE  the new UI
app/static/css/chat.css          NEW   chat styles, built on existing style.css tokens
app/static/js/chat.js            NEW   chat client (SSE, markdown, tool chips)
app/static/vendor/marked.min.js  NEW   vendored markdown renderer
app/static/vendor/purify.min.js  NEW   vendored HTML sanitizer
migrations/versions/<rev>_*.py   NEW   alembic: chat_messages.tool_calls + new permissions
tests/test_agent_tools.py        NEW
tests/test_agent_service.py      NEW
tests/test_chatbot_agent_routes.py NEW
```

`chatbot_context_service.py` stays on disk as the **fallback** path (see §6.5), but is no longer
the primary. Fix its `final_decision_value` bug rather than leaving it wrong.

---

## 5. Part A — the agent

### 5.1 Tool registry — `app/services/agent_tools.py`

One module-level registry. Each tool is a dataclass:

```python
@dataclass
class Tool:
    name: str
    description: str          # written FOR the model — say when to use it, and when not to
    parameters: dict          # JSON Schema (type object, properties, required)
    permission: tuple | None  # (module, screen) checked via has_permission, or None for open
    fn: Callable              # (**kwargs) -> dict   JSON-serialisable
```

Expose `all_tools()`, `tools_for_user(user)` (permission-filtered), `execute(name, args, user)`.

`execute` must **never raise** — catch everything and return `{"error": "..."}` so the model can
recover and tell the user. Log the exception.

Implement these eleven:

| # | Tool | Args | Returns | Permission |
|---|---|---|---|---|
| 1 | `get_context` | — | today's date, ISO week, current shift, user name + role, DB row counts, list of production stages in flow order, list of decision states | none |
| 2 | `describe_schema` | `tables?: string[]` | table → columns (name, type, nullable) + FK targets + a one-line purpose for each table. No args = every table, names + purposes only. | none |
| 3 | `run_sql` | `sql: string`, `limit?: int=200` | `{columns, rows, row_count, truncated}` | `chatbot.sql` |
| 4 | `get_ladle` | `ladle_id: string` | analysis, all elements + equivalents, spec verdict per element, furnace, decision, linked pipes with their decisions, mechanical tests | `chemical.list` |
| 5 | `get_pipe` | `code: string` (no_code or pipe_code or warehouse_barcode) | pipe, product, order, customer, ladle, every stage with decision/date/operator/defect, lab + final decision, history count | `stages.list` |
| 6 | `get_order` | `order_number: string` | order, product spec, target vs produced, accept/reject/hold split, per-stage progress, customer | `orders.list` |
| 7 | `search_pipes` | `date_from?`, `date_to?`, `dn?`, `pipe_class?`, `lab_decision?`, `final_decision?`, `ladle_id?`, `order_number?`, `customer?`, `shift?`, `machine?`, `stage?`, `stage_decision?`, `has_defect?`, `limit?=50` | matching pipes, plus a totals block | `stages.list` |
| 8 | `production_summary` | `date_from`, `date_to`, `group_by: day\|week\|month\|shift\|dn\|pipe_class\|machine\|order\|customer\|ladle\|stage\|engineer` | per-group counts + accept/reject/hold/waiting + accept rate | `reports.production_summary` |
| 9 | `defect_analysis` | `date_from`, `date_to`, `group_by: defect_type\|defect_reason\|stage\|dn\|shift\|machine` | counts, share of total, top contributors | `reports.defect_analysis` |
| 10 | `quality_stats` | `metric: element\|mechanical\|dimension`, `name` (e.g. `carbon`, `tensile`), `date_from?`, `date_to?` | n, mean, sd, min, max, spec limits, Cp, Cpk, out-of-spec count and which ladles/pipes | `reports.spc` |
| 11 | `find_page` | `query: string` | up to 8 matching pages from the site map: URL, title (en+ar), purpose — permission-filtered for this user | none |

Notes on specific tools:

- **`get_context`** is cheap and should be described as "call this first if the question involves
  today, this week, the current shift, or how much data exists". Models are bad at dates.
- **`describe_schema`** is what makes `run_sql` usable. Give every table a real one-line purpose,
  hand-written. Get the column list from SQLAlchemy `inspect(db.engine)`, not a hardcoded copy.
- **`quality_stats`** should wrap the existing `app/services/spc_service.py` and
  `capability_service.py` rather than reimplementing Cp/Cpk. Read them first. Same for
  `defect_analysis` → check `nonconformance_service.py`.
- **`get_ladle`** must evaluate each element against `app/data/element_rules.json` via the
  existing `decision_service`, and say which elements are out of spec. That domain logic is not
  expressible in SQL and is exactly what the agent needs from a curated tool.
- **Decision fields:** report `lab_decision` **and** `final_decision_value`, and label them
  distinctly. `lab_decision` is the populated signal on real data. Never present a pipe as
  "pending" solely because `final_decision_value` is null.
- Decision values are **case-inconsistent** in the data. Compare case-insensitively everywhere.
- The DN parameter code (e.g. `P80`) is a product-code token, **not** the diameter. Use
  `Product.dn_value` / `dn_label` for a real DN. `pipes.diameter` is the numeric DN.

### 5.2 The SQL guard — get this right

`run_sql` is the tool that makes the agent able to answer things nobody pre-canned. It is also
the one that can hurt. Layer the defences; do not rely on the regex alone.

1. **Shape:** must match `^\s*(SELECT|WITH)\b` (case-insensitive). Reject anything else.
2. **Single statement:** strip a trailing `;`, then reject if any `;` remains.
3. **Keyword blacklist**, word-boundary matched: `INSERT UPDATE DELETE DROP ALTER CREATE TRUNCATE
   GRANT REVOKE COPY VACUUM REINDEX CALL DO EXECUTE MERGE LOCK` and the functions `pg_read_file`,
   `pg_read_binary_file`, `pg_ls_dir`, `pg_sleep`, `dblink`, `lo_import`, `lo_export`,
   `pg_terminate_backend`, `pg_reload_conf`, `set_config`, `current_setting`.
4. **Column blacklist:** reject any query whose text mentions `password`. Never expose
   `users.password_hash`.
5. **Read-only transaction:** open a transaction, `SET TRANSACTION READ ONLY`, and
   `SET LOCAL statement_timeout = '8s'`. Roll back at the end always. On SQLite (tests) the
   read-only pragma differs — branch on dialect, keep the regex guard as the common floor.
6. **Row cap:** wrap as `SELECT * FROM (<sql>) AS _agent_q LIMIT <limit+1>`; if you get
   `limit+1` rows, return `limit` and set `truncated: true`. Cap `limit` at 500.
7. **Result size:** if the JSON payload exceeds ~40 KB, truncate rows further and say so.
8. Return the executed SQL in the result so the UI can show it.

Every one of these needs a test. See §8.

### 5.3 The agent loop — `app/services/agent_service.py`

```python
def run_agent(message, history, user, session_id, max_steps=8) -> Iterator[dict]
```

Yields plain dicts; the route turns them into SSE frames. Loop:

1. Build the system prompt (§5.4) and the message list from `history` (last 20) + `message`.
2. Call the provider with the tool declarations for this user.
3. If the reply contains tool calls: emit `tool_call` for each, execute them (in order), emit
   `tool_result` for each, append both to the conversation, `step += 1`, go to 2.
4. If it contains text: stream it out as `chunk` events, then `done`.
5. If `step` hits `max_steps`: append a system note telling the model to answer now with what it
   has, and make one final no-tools call.

**Providers.** Prod is on OpenRouter, so that path is the priority — but implement both.

- **OpenRouter** — OpenAI-compatible. `tools: [{type: "function", function: {...}}]`,
  `tool_choice: "auto"`. The reply carries `message.tool_calls[]` with `id`, `function.name`,
  `function.arguments` (a JSON **string** — parse defensively; models emit malformed JSON, and on
  a parse failure you return an error tool-result rather than crashing). Append the assistant
  message verbatim, then one `{"role": "tool", "tool_call_id": ..., "content": ...}` per call.
- **Gemini native** — `tools: [{function_declarations: [...]}]`; reply parts carry `functionCall
  {name, args}`; respond with a `functionResponse` part. Note Gemini rejects some JSON-Schema
  keywords (`additionalProperties`, `$schema`, `format` on strings) — strip them when converting.
- **Fallback:** if the configured model returns a 4xx indicating tools are unsupported, or the
  provider is neither of the above, fall back to the **old** path: `gather_context(message)` +
  `generate_chatbot_stream`. Emit a `notice` event so the UI can say the agent is degraded. The
  free fallback models in settings (`nemotron`, `qwen3`) are unreliable at tool calling — do not
  use them for the agent path.

**Streaming.** Use the streaming endpoint for every round. Accumulate the parts; if tool calls
appear, suppress text output for that round and execute; if text appears, emit it as it arrives.
OpenRouter streams `tool_calls` in deltas that must be merged by `index` — handle that.

**Event protocol** (superset of today's, so the old client would still work):

```
{"session_id": 12}
{"notice": "..."}                                         optional, degraded mode
{"tool_call":   {"id":"c1","step":1,"name":"get_pipe","args":{...}}}
{"tool_result": {"id":"c1","ok":true,"name":"get_pipe","summary":"...","preview":{"columns":[...],"rows":[...]},"ms":34}}
{"chunk": "text..."}
{"done": true, "steps": 2, "tools_used": ["get_pipe"]}
{"error": "..."}
```

`summary` is a short human string the UI shows on the collapsed chip ("Pipe N1234 — DN300,
ACCEPT"). `preview` is optional and only for row-shaped results, capped at 10 rows / 8 columns.

**Guards:** total wall clock ≤ 90 s; per-tool ≤ 10 s; if the model asks for an unknown tool,
return an error result naming the valid tools; if it repeats the identical call with identical
args twice, return a cached result and add a note telling it to stop.

### 5.4 System prompt — this is where "knows the whole system" lives

Build it dynamically per request. Sections:

1. **Identity.** QC assistant for **GCP QC Trace**, the quality-control and traceability system of
   a ductile-iron pressure-pipe plant working to ISO 2531 / EN 545.
2. **User.** name, role, and the plain-language list of what they may see. Say explicitly that a
   tool refused for permissions means *the user isn't allowed*, not that data is missing.
3. **Time.** today's date, ISO week, current shift. Tell it to call `get_context` rather than
   assume.
4. **Domain glossary.** Written once, carefully — this is the most valuable part of the prompt:
   - **Ladle / heat** — one melt, `chemical_analyses`, identified by `ladle_id`. Carries the
     chemical result and its decision. Pipes are cast from a ladle.
   - **Pipe** — `pipes`. Two identifiers: `no_code` (short serial, what operators say) and
     `pipe_code` (the long composite code). Plus `warehouse_barcode`.
   - **Stages** — a pipe moves through the production stages in `production_stages` (DB-driven,
     read them, do not hardcode). Each visit is a `pipe_stages` row with a decision; re-deciding
     overwrites it and pushes the old value into `pipe_stage_history`. **So rework analysis must
     union both tables.**
   - **Decision states** — `WAITING`, `ACCEPT`, `HOLD`, `REJECT`, `BLOCKED`. `lab_decision` is
     the lab's verdict on the ladle's pipes; `final_decision_value` is the closing verdict.
     Values are stored case-inconsistently; always compare case-insensitively.
   - **§4.2 mechanical cascade** — a mechanical FAIL on a sample-tested ladle is **always HOLD**,
     never an automatic scrap. A pipe tested in an individual (non-sample) role never cascades to
     its ladle mates.
   - **Lab Approval** — a single gate stage between Annealing and Zinc.
   - **Mechanical test roles** — which pipe of a ladle was sampled; drives whether a result
     cascades.
   - Anything else you find in `app/services/decision_service.py`,
     `mechanical_decision_service.py` and `stage_flow_service.py` that a user would ask about.
5. **Site map** — from `site_map.py` (§5.5), filtered to pages this user may open, grouped by
   area, as `URL — Title — purpose`. Instruct: when the answer implies an action the user should
   take in the app, link the page as a markdown link.
6. **Tool policy.**
   - Never state a number you did not get from a tool this turn. No estimates, no memory.
   - Prefer a curated tool over `run_sql` when one fits; `run_sql` is for what the others can't do.
   - Before writing SQL, call `describe_schema` for the tables involved. Do not guess columns.
   - Chain freely: look something up, then drill in. Several tools per turn is normal and good.
   - A tool returning nothing means no matching data. Say that plainly. Never invent a plausible
     row.
   - If the question is ambiguous in a way that changes the query (which date range? which DN?),
     ask one short clarifying question instead of guessing.
7. **Output style.** Answer in the user's language — Arabic, English or French, matching the
   question. Markdown. Tables for tabular results. Lead with the answer, then the evidence. Short.
   No filler, no restating the question. When you list pipes or ladles, include their identifiers
   so the user can act on them.

### 5.5 Site map — `app/services/site_map.py`

`build_site_map(user)` → list of `{url, endpoint, title_en, title_ar, purpose, area, permission}`.

Enumerate `current_app.url_map` for GET rules with no path parameters and not under `/static`,
`/api`. That is ~110 real pages. Hand-write `purpose` for the ones that matter (all of
`/reports/*`, the list pages, the console, warehouse, stickers, the admin settings screens); for
the rest, humanise the endpoint name. Map each to its permission via the same `(module, screen)`
pair the route's own `@requires_permission` uses — read the routes to get these right, do not
guess. Filter by `has_permission` before it reaches the prompt.

Cache the built map on the app object; invalidate on nothing (routes don't change at runtime).

### 5.6 Permissions

- New permission rows, seeded in the migration: `chatbot.sql` (raw SQL tool) and
  `chatbot.agent` (agent mode at all). Grant `chatbot.agent` to every role that already has
  `chatbot.view`; grant `chatbot.sql` to `super_admin` and `admin` only.
- `tools_for_user` filters the declarations, so a model without permission never even sees the
  tool. Belt and braces: `execute()` re-checks before running.
- Log every tool call to the audit trail via `audit_service` — user, tool, args, ms, ok. This is
  a QC system; an assistant that reads production data should leave a trail.

---

## 6. Part B — the chat UI redesign

Rebuild `app/templates/chatbot/index.html`, with styles in `app/static/css/chat.css` and
behaviour in `app/static/js/chat.js`. No inline `<style>` blocks, no inline `onclick`.

### 6.1 Design language

Use the existing design tokens in `app/static/css/style.css` (54 custom properties are already
defined — read them and use them; do not invent a second palette, do not hardcode hex). Bootstrap
5.3.2 and Bootstrap Icons 1.13.1 are already loaded in `base.html`. Fonts: Inter (latin) and IBM
Plex Sans Arabic — already loaded.

Target look: Claude / ChatGPT. Calm, high-contrast, generous whitespace, one accent colour.

### 6.2 Layout

- Full-height app shell inside the existing `base.html` content area.
- **Left rail** — conversation list. Collapsible to icons; off-canvas below 992px. Search box when
  there are more than 10. New-chat button pinned top. Per-item: title, relative time, message
  count, hover-delete.
- **Centre** — the conversation, in a column capped at ~780px, centred, scrolling. Messages are
  not bubbles-on-both-sides: user messages sit in a subtle tinted block, assistant messages are
  plain text on the page background, the way Claude does it. Avatar/label per turn.
- **Composer** — pinned to the bottom of the centre column. An auto-growing `<textarea>` (1→8
  rows), not the current single-line `<input>`. Enter sends, Shift+Enter newlines. Send button
  becomes a **Stop** button while streaming, and stopping must actually abort the fetch.
- **Empty state** — a short greeting plus 6 suggested prompts as clickable cards. Make them
  *data-aware*: generate them server-side from what actually exists (e.g. the newest ladle id,
  today's pipe count), not the current hardcoded list.

### 6.3 Showing the agent work — the part that matters

When a `tool_call` event arrives, render a **step chip** above the answer area:

- collapsed: a spinner, an icon, and the tool's human name — "Looking up ladle 4713012026…"
- on `tool_result`: spinner → tick (or a warning for `ok:false`), text swaps to the `summary`,
  with the elapsed ms.
- clicking a chip expands it to show the arguments, and — when `preview` is present — a compact
  table of rows. For `run_sql`, show the executed SQL in a `<code>` block.
- multiple chips stack; after the answer streams in they collapse into a single line,
  "3 steps · get_context, run_sql, get_ladle", clickable to re-expand.

This is what makes the assistant trustworthy in a QC context: the user can see the query.

### 6.4 Message rendering

- Real markdown via vendored `marked.min.js` → sanitised through `DOMPurify.sanitize()` before
  `innerHTML`. **Never** put model output into `innerHTML` unsanitised. Vendor both into
  `app/static/vendor/` (download them; do not add a CDN dependency for this).
- Support: headings, bold/italic, lists, links, inline code, fenced code, and **tables** — the
  agent will return tables often, so style them properly (sticky header, horizontal scroll).
- Streaming: append text as it arrives, re-rendering markdown on a small debounce. A blinking
  caret while streaming.
- Per-message actions on hover: **Copy**, and **Retry** on the last assistant message.
- Links to app pages open in the same tab; make them visually distinct.

### 6.5 Bilingual + responsive

- The app is Arabic/English (`current_locale`). Full RTL: the whole layout mirrors, chips and
  chevrons flip. Test with `?lang=ar` — do not assume, actually load it.
- Dark and light must both work, following whatever mechanism `base.html` already uses.
- Mobile: sidebar off-canvas, composer fixed, chips wrap, tables scroll. Test at 390px.

### 6.6 Session persistence

Persist the tool steps so reloading a conversation shows them. `ChatMessage.tool_calls` — a JSON
column, nullable, holding the list of `{name, args, summary, ok, ms}` for that assistant turn.
Render them collapsed on load.

---

## 7. Migration

One Alembic revision. `LAB_SKIP_DB_BOOTSTRAP=1` is required for every `flask db` command.

```bash
docker exec -e LAB_SKIP_DB_BOOTSTRAP=1 lab-chemical-dev flask db revision -m "agent chat: tool_calls + permissions"
docker exec -e LAB_SKIP_DB_BOOTSTRAP=1 lab-chemical-dev flask db upgrade
```

Contents:
- `chat_messages.tool_calls` — JSON, nullable.
- Insert the two `chatbot.sql` / `chatbot.agent` permission rows and their `role_permissions`
  grants, **idempotently** (the boot seeds run once per gunicorn worker — 4 in prod — so anything
  that inserts must tolerate already existing; wrap in a check, and the seed path must
  try/rollback).
- Downgrade must actually reverse both.

Verify `flask db upgrade` then `downgrade` then `upgrade` runs clean on `lab_chemical_dev`.

---

## 8. Tests — required, not optional

`tests/test_agent_tools.py`
- SQL guard: rejects `INSERT`/`UPDATE`/`DELETE`/`DROP`/`ALTER`/`CREATE`/`TRUNCATE`/`COPY`;
  rejects a second statement after `;`; rejects `pg_sleep`, `pg_read_file`, `dblink`; rejects any
  query mentioning `password`; accepts a plain `SELECT` and a `WITH … SELECT`.
- Limit enforcement: a query returning more than `limit` sets `truncated: true` and returns
  exactly `limit` rows. `limit` above 500 is clamped.
- Each tool returns its documented shape against seeded fixture data, and a not-found id returns
  a clean `{"error": ...}` rather than raising.
- Permission filtering: `tools_for_user` for an `operator` excludes `run_sql`; `execute` refuses
  it even if called directly.

`tests/test_agent_service.py` — with the HTTP transport mocked, no network:
- one tool call → result → final answer produces the right event sequence;
- two chained tool calls work;
- `max_steps` is enforced and still yields a final answer;
- an unknown tool name yields an error tool-result, not a crash;
- malformed `function.arguments` JSON yields an error tool-result, not a crash;
- a provider 4xx on tools falls back to the legacy path and emits `notice`;
- both the OpenRouter and the Gemini response shapes are covered.

`tests/test_chatbot_agent_routes.py`
- `/chatbot/stream` as a logged-in user returns `text/event-stream` and a well-formed frame
  sequence (mock the agent);
- an anonymous request redirects to login;
- a user lacking `chatbot.view` is refused;
- tool steps are persisted onto the assistant `ChatMessage` and re-render on GET.

Match the existing suite's conventions — read a few of the 68 files in `tests/` first.

Run the whole suite before you claim done; you must not regress the existing 68 files:

```bash
docker exec lab-chemical-dev python -m pytest tests/ -q
```

---

## 9. Live verification — evidence, not assertion

Against the dev app on :9998 with the real key and real data. Log in as `agentdev`, and actually
run these. Capture the answers.

1. `"How many pipes were produced this month, broken down by DN?"` → must call a tool, must
   return real numbers that match a `psql` count you run yourself.
2. `"Show me ladle <a real ladle_id from the dev DB> and tell me if any element is out of spec"`
   → element-by-element verdict.
3. `"Which pipes are on HOLD right now and why?"`
4. `"What's the accept rate by shift over the last 30 days?"` → aggregate, likely `run_sql`.
5. `"Where do I record the zinc coating thickness?"` → must answer with the page URL from the
   site map.
6. `"ما هي أسباب الرفض الأكثر تكرارا هذا الشهر؟"` → answers **in Arabic**, with real data.
7. A deliberately unanswerable one: `"How many pipes did we ship to Mars?"` → says it has no such
   data. Must not invent.
8. `"DELETE all pipes"` / `"run: DROP TABLE pipes"` → refused; verify with a row count before and
   after that nothing changed.
9. Multi-turn: ask a question, then `"and the week before?"` → follow-up resolves from history.

Then verify the UI yourself: load `:9998/chatbot/` in both `en` and `ar`, at desktop and 390px
width, in dark and light. Screenshot or describe concretely what you saw. Check the browser
console is clean.

---

## 10. Definition of done

- [ ] Agent answers all nine §9 checks correctly against real data, evidence captured.
- [ ] Numbers cross-checked against `psql` — they match.
- [ ] SQL guard blocks every case in §8, verified by test output.
- [ ] `pytest tests/ -q` green, including the 68 pre-existing files.
- [ ] Migration up → down → up clean on `lab_chemical_dev`.
- [ ] New UI works in en + ar, light + dark, desktop + 390px, console clean.
- [ ] Tool chips show and expand; SQL is visible for `run_sql`.
- [ ] No secrets committed (`git show --stat` on every commit, check `app/data/`).
- [ ] Work committed on `feat/ai-agent-chat` in logical commits. **Not merged, not deployed.**
- [ ] A short `specs/007-ai-agent-chat/RESULTS.md`: what you built, the §9 evidence, what you'd
      do next, and anything you found wrong in the existing code along the way.

---

## 11. How to work

Read before you write — `ai_service.py`, `decision_service.py`, `spc_service.py`,
`stage_flow_service.py`, the existing tests, and `style.css`'s tokens. This codebase has real
domain logic in it; the agent's value comes from reusing that logic, not reimplementing it.

Build in this order, committing at each step:

1. `site_map.py` + `get_context` + `describe_schema` + `find_page` — cheap, and they prove the
   registry shape.
2. `run_sql` with the full guard **and its tests** before anything depends on it.
3. The remaining curated tools, wrapping existing services.
4. `agent_service.py` — OpenRouter path first (that's what prod runs), then Gemini, then fallback.
5. Wire `/chatbot/stream`, keeping the old event names working.
6. Migration + permissions.
7. The UI.
8. Full test pass + §9 live verification + `RESULTS.md`.

If something in the spec turns out to be wrong when it meets the code — a service that doesn't
work the way I described, a column that isn't there — **do the right thing and write it down in
RESULTS.md.** Don't cargo-cult a spec that contradicts the codebase.
