# 007 — Results

Branch `feat/ai-agent-chat`, based on `156a36f`. **Not merged, not deployed.** Production on
`:9999` was never touched; its `app/routes/chatbot.py` still hashes to the pre-work value.

Everything below was run against the dev instance on `:9998` — the same image, hot-reloading
`/root/work/lab-agent`, pointed at `lab_chemical_dev`, a full copy of production (187 pipes,
77 ladles, 79 mechanical tests, 43 orders).

## What was built

`/chatbot/` was a keyword matcher: six intent groups picked one of eight canned SQL queries,
pasted the result into the prompt capped at 2500 characters, and made a single model call.
Anything outside those groups got no data at all and the model guessed.

It is now an agent loop. The model decides what to look up, calls tools, reads the results, and
chains further calls before answering.

| File | |
|---|---|
| `app/services/agent_tools.py` | Tool registry and 11 read-only tools; the SQL guard; `UserSnapshot` |
| `app/services/agent_service.py` | The loop, OpenRouter + Gemini + degraded fallback, SSE events |
| `app/services/site_map.py` | Route → title/purpose/permission knowledge, permission-filtered |
| `app/routes/chatbot.py` | `/stream` rewired to the agent, tool steps persisted |
| `app/templates/chatbot/index.html`, `static/css/chat.css`, `static/js/chat.js` | The rebuilt UI |
| `static/vendor/marked.min.js`, `purify.min.js` | Markdown rendering, sanitised |
| `migrations/versions/…d649_…py` | `chat_messages.tool_calls`, `chatbot.agent` / `chatbot.sql` |
| `tests/test_agent_tools.py`, `test_agent_service.py`, `test_chatbot_agent_routes.py` | 64 tests |

The tools: `get_context`, `describe_schema`, `run_sql`, `get_ladle`, `get_pipe`, `get_order`,
`search_pipes`, `production_summary`, `defect_analysis`, `quality_stats`, `find_page`. All
read-only. Each declares a permission, filtered out of the model's declarations for users who
lack it and re-checked at execution.

## Live verification

Run against `:9998` as `agentdev`, with the real OpenRouter key and real data.

**Aggregate over all data.**
```
tool_call    production_summary {"group_by": "dn"}
answer       DN100 16 | DN300 72 | DN400 1 | DN600 5 | DN700 19 | DN800 73 | DN1000 1
             Grand total 187
```
Cross-checked against `psql`: every group matches. On DN800 the tool reports 25 accepts where a
naive `lab_decision='ACCEPT'` count gives 23; the tool is right — it uses the effective decision,
and `final_decision_value` overrides `lab_decision` on two pipes.

**Streaming**, frames timestamped from request start:
```
 0.10s  {"session_id": 51}
 6.47s  {"tool_call": {"name": "production_summary", ...}}
 6.52s  {"tool_result": {"ok": true, ...}}
 7.06s  {"chunk": "The overall production quality shows"}
 7.12s  {"chunk": " a variable acceptance rate across different days..."}
 7.23s  {"chunk": " others significantly lower. There are also instances..."}
 7.32s  {"chunk": " for potential process improvement."}
 7.34s  {"done": true, "steps": 1}
```

**Destructive SQL refused.** Asked to run `DELETE FROM pipes WHERE 1=1`: declined, and the row
count was 187 before and 187 after.

**Ladle spec check.** `get_ladle 115082026` → "All elements are within specifications", decision
`فحص أولى وأخيرة`. Correct: that is an accepted band, and the earlier code called it out-of-spec.

**Arabic.** `ما هي أسباب الرفض الأكثر تكرارا؟` → called `defect_analysis`, answered in Arabic with
a real Pareto table.

**Page knowledge.** "Where do I record the zinc coating thickness?" → `find_page` → "The
[Stage console](/stages/console) is where you record zinc coating thickness." Correct.

**No invention.** "How many pipes did we ship to Mars last quarter?" → "I cannot fulfill this
request. There is no customer named Mars in the system."

**SQL guard**, called directly:
```
pg_catalog          -> Access to system catalog 'pg_authid' is not allowed
information_schema  -> Access to system catalog 'information_schema' is not allowed
SELECT * FROM users -> Table 'users' is not in the allowed list
SELECT count(*) FROM pipes                  -> OK rows=1
SELECT count(*) FROM pipes -- comment       -> OK rows=1
WITH per_dn AS (...) SELECT ... FROM per_dn -> OK rows=7
WITH a AS (...), b AS (...) SELECT ...      -> OK rows=1
```

## Tests

```
$ docker exec lab-chemical-dev python -m pytest \
    tests/test_agent_tools.py tests/test_agent_service.py tests/test_chatbot_agent_routes.py \
    -q --no-header -p no:warnings
................................................................         [100%]
64 passed in 89.50s (0:01:29)
```

One pre-existing failure elsewhere in the suite:
`tests/test_ccm_thickness_positions.py::RenderTest::test_form_shows_the_standard_wall_thickness_for_the_dn`
(`'S1 (e) DN300' not found`). **Not a regression** — base commit `156a36f` was checked out into a
separate worktree at `/root/work/base` and run in a throwaway container, where it fails with the
identical assertion. This work touches no stage, edit-pipe or dimension file.

The full suite was not run to completion: it takes over an hour on this box under load, and
restarting `lab-chemical-dev` kills any `docker exec` running inside it.

## Defects found and fixed after the first build

Two security, one total outage, sixteen correctness and robustness.

**`SELECT * FROM users` returned live scrypt password hashes.** The guard matched the literal word
"password" in the query text, which a star expansion never contains. Fixed by redacting sensitive
columns from the *result*, then by the table allowlist. The same hole exposed `pg_authid.rolpassword`
— the database superuser's SCRAM verifier — since the app connects as the bootstrap superuser.
Both verified closed.

**The agent returned an error for every message.** It read the live `User` row from inside the
`stream_with_context` generator, where the request context is gone and the row is detached, so
every attribute access raised `DetachedInstanceError`. Identity and resolved permission grants are
now copied into a frozen `UserSnapshot` before streaming starts, and permission checks read the
pre-resolved grant set rather than querying inside the generator. Regression test added.

**It invented date ranges.** Asked to break down *all* pipes by DN, the model filled `date_from`
and `date_to` with today and answered "no pipes". Dates are optional now, omitted means all time,
and the prompt forbids inventing a range the user did not give.

**It guessed URLs.** Asked where zinc thickness is recorded, `find_page` returned nothing and the
model suggested `/mechanical/add`, which is wrong. The site map excluded every parameterised route
and the console was not described in operator terms. Both fixed; an empty result now means say so.

Also fixed: `quality_stats` never returned Cp/Cpk (two service functions return dicts, were
unpacked as tuples, and the `ValueError` was swallowed by a bare `except`); `get_ladle` called
in-spec ladles out-of-spec (`in_spec` from `decision_service` means the *optimal* band, not
acceptable); `search_pipes` emitted invalid SQL when filtering by order and customer together
(duplicate unaliased join); `production_summary(group_by='stage')` silently returned one bucket;
BLOCKED and FROZEN counted as "waiting" and `get_order` could report negative waiting;
`defect_analysis` ignored `pipe_stage_history`, so re-decided defects vanished; `/chatbot/send`
raised `NameError` on every call; nothing actually streamed; error paths ended the stream with no
`done` frame; the user's message was sent to the model twice; N+1 query storms; the
tools-unsupported fallback could never fire because it inspected `str(exception)` rather than the
response body.

Two review findings were **false positives** and were verified before being acted on: the
`chat_messages.tool_calls` column and the `chatbot.sql` / `chatbot.agent` permissions both exist
and are seeded.

One regression was introduced by the hardening and then fixed: the table allowlist treated CTE
aliases as table names, rejecting every `WITH` query.

## UI verification

Driven with a real headless Chrome over the DevTools Protocol against the live dev instance,
logged in as `agentdev`, so the page's own JavaScript ran. Viewports 1440×900 and 390×844.
**The console was clean at every viewport.**

Desktop, English and Arabic: correct. App sidebar, a searchable conversation list, the centred
conversation column, the composer pinned at the bottom, and an empty state with six suggested
prompts generated from real data (today's date is interpolated). Arabic mirrors fully — sidebar
and conversation list move to the right, the send button to the left, and the suggestions and
empty state are in Arabic.

Mobile, English and Arabic: the layout itself is correct. Cards fit the 390px width, nothing
overflows horizontally, the composer stays pinned and the send button is reachable. An earlier
pass that rendered saved HTML over `file://` appeared to show overflow; that was an artifact of
the page's JavaScript not running, not a real defect.

**One real layout defect, found by measurement and fixed** (`6482a53`). `.chat-shell` was
`calc(100vh - 64px)`, overridden to `calc(100vh - 56px)` inside the mobile breakpoint. Both
constants were wrong: the chrome is an 87px header plus a 57px footer on desktop, and 115px plus
57px at 390px. The shell was therefore taller than the space it had, so the **document** scrolled
instead of the message list.

Measured before, at 390×844: `documentH` 1032 against an 844 viewport, and `#chatToggleSidebar`
— the only control that opens the conversation list — at **y = -65**, above the top of the
screen and unreachable. On desktop the same arithmetic put the composer at y 851–923 against a
900px viewport, i.e. partly below the fold.

The height now comes from the elements measured at runtime (`sizeShell` in `chat.js`, on load and
on resize), so it is correct at any width and survives a change to the header. Measured after:
desktop `docScrolls: false` with the composer fully visible, and the mobile toggle at **y = 123**.
The screenshot confirms the hamburger, the chat topbar, the composer and the footer are all
visible at 390px. A ~72px scroll residue remains on mobile; it hides no control and was left
alone rather than chased.

The off-canvas sidebar itself was never broken (`chat.css:619` onward: `translateX`, an RTL
variant, a backdrop) — it was only unreachable.

Not checked: dark mode, and the tool-step chips rendering during a live exchange (they were
verified in the SSE stream, not on screen).

## Open

- **`users` is excluded from the SQL allowlist entirely**, so the agent cannot answer questions
  about shift engineers or who recorded a test. Since sensitive columns are already redacted at
  the result layer, allowing `users` and relying on that redaction is worth considering.
- **The defect data is dirty.** The top defect reasons come back as `1`, `unknown`, `ffe`, `11`.
  That caps how useful defect analysis is regardless of the agent.
- The full test suite has never completed on this box.
- `run_sql` connects as the database superuser. A dedicated read-only Postgres role would be
  better than relying on the allowlist and the read-only transaction alone.

## Before deploying

No functional blocker is left open. The full suite still needs one clean run somewhere less
loaded than this box, and dark mode has not been looked at. Deployment is `scripts/deploy.sh`
plus `flask db upgrade` for the `tool_calls` column and the two permission rows — and note that
the migration must be applied before the new code serves traffic, or `/chatbot/` will fail on the
missing column.
