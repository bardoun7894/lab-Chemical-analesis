# 007 — Round 2: review fixes

The feature is built and the agent answers real questions end-to-end. An independent review
plus live testing then found the defects below. Work through them in order, committing per
group. Same rules as before: read the code, evidence over assertion, no prod, no secrets.

Verify each one before fixing it — two findings in the first review round turned out to be
false positives because the reviewer lacked the full tree. If a claim below does not reproduce,
say so in RESULTS.md and move on rather than "fixing" working code.

Already fixed and committed by the developer, do not redo:
- The `DetachedInstanceError` crash. A `UserSnapshot` dataclass plus `snapshot_user()` /
  `as_snapshot()` now live in `agent_tools.py`; the route snapshots before streaming, and
  permission checks read the pre-resolved `granted_keys` instead of hitting the DB inside the
  generator. **Add a regression test for this** — it made the agent 100% non-functional and
  nothing in the suite caught it.
- `SELECT *` password redaction (this also closed a `pg_authid` / `rolpassword` leak).

---

## P0 — wrong answers reaching the user

**F1. Date arguments default to "today", so whole-dataset questions return nothing.**
Reproduced live: "Break down all pipes by DN diameter with counts" → the model called
`production_summary(group_by='dn', date_from='2026-08-30', date_to='2026-08-30')` and answered
"no pipes broken down by DN diameter", with 187 pipes in the database. `date_from`/`date_to`
are required-ish in the schema and the model fills them with today when the user gave no range.
Make both genuinely optional across `production_summary`, `defect_analysis` and `quality_stats`:
omitted = all time. Say so in each tool description ("omit both for all time"), and add a line
to the system prompt's tool policy: do not invent a date range the user did not give.

**F2. `quality_stats` never returns Cp, Cpk or spec limits.** `capability_service.spec_limits_for`
returns a **dict** (`{"lsl","usl","one_sided","source"}`) and `capability_indices` returns a
**dict**, but both are unpacked as tuples (`lsl, usl = limits`), raising `ValueError` inside a
bare `except Exception: pass`. The tool silently degrades to n/mean/sd/min/max forever. Unpack by
key and narrow the except so it can never swallow this again.

**F3. `get_ladle` calls in-spec ladles out-of-spec.** `decision_service.get_element_decision`
sets `in_spec = (decision == 'فحص أخيرة فقط')` — that flag means *the optimal band*, not
*acceptable*. `_get_ladle` treats everything else as `out_of_spec`, so a ladle in the accepted
`فحص أولى وأخيرة` band is reported to a lab engineer as out of spec, contradicting the app's own
screen. Distinguish "optimal band" from "out of spec" (= `تالف` / no matching range) and label
them distinctly in the payload.

**F4. `search_pipes` emits invalid SQL when both `order_number` and `customer` are given.**
Both branches `query.join(ProductionOrder)` unaliased → `table name "production_orders" specified
more than once` (Postgres 42712). Build the join once and AND the conditions;
`spc_service._mechanical_points` already has this pattern with a comment about it.

**F5. `production_summary(group_by='stage')` silently returns one bucket.** `'stage'` is in
`valid_groups` and in the description, but the if/elif chain has no branch for it, so it falls
through to `key = 'all'` and reports the entire period as a single stage. Implement it or remove
it from both lists. Same class: `group_by='machine'` (here and in `defect_analysis`) reads
`Pipe.machine`, but the machine is recorded **per stage** — `Pipe.machine_id` is null on real
data, so every row groups to 'unknown'. Join through `pipe_stages` or drop the option.

**F6. BLOCKED and FROZEN pipes are counted as "waiting", and `get_order` can report negative
waiting.** `_production_summary` maps only ACCEPT/REJECT/HOLD and dumps the rest into `waiting`,
but `lab_decision` also carries `BLOCKED` (terminal scrap) and `FROZEN`; a shift with 60 BLOCKED
pipes is reported as a 60-deep pending backlog and the accept rate is wrong. Separately
`_get_order` ORs each bucket across `lab_decision` **and** `final_decision_value`, so one pipe
lands in two buckets and `waiting = total - accept - reject - hold` goes negative — a pipe with
`lab_decision='REJECT'` and `final_decision_value='ACCEPT'` (a normal override) is enough.
Use one case-insensitive classifier over the effective decision, reusing `pipe_decision_service`.

**F7. `defect_analysis` reads only current `pipe_stages`.** Re-deciding a stage overwrites the row
and pushes the old value to `pipe_stage_history`; `/reports/rework-report` unions both, and the
system prompt tells the model history must be unioned — the tool does not honour its own
contract, so re-decided defects vanish from the Pareto and the agent contradicts
`/reports/defect-summary`. Union both tables, and include `ChemicalAnalysis.has_defect` the way
the defect-summary route does.

**F8. `/chatbot/send` raises `NameError` on every call.** `generate_chatbot_response` is used but
no longer imported; the broad `except` turns it into a 200 with an error bubble and nothing in
the logs. Re-add the import or delete the route — decide which, don't leave it half-wired.

## P1 — robustness and responsiveness

**F9. Nothing actually streams.** `_openrouter_stream_round` accumulates every delta and returns
one joined string, which the caller emits as a single `chunk` after generation completes — a
700-token answer is ~28 s of empty bubble then a wall of text. The Gemini path is worse: it calls
`:generateContent`, the non-streaming endpoint, so it can never stream. Make the round a
generator that yields text deltas as they arrive while still accumulating tool-call fragments.
This is the difference between "feels like ChatGPT" and "feels broken", so it matters more than
its severity suggests.

**F10. Error paths end the stream with no `done` frame.** A provider 500 on step 3 yields an
`error` and returns; a client keyed on `done` spins forever, and the partial answer is never
persisted, so the next turn's history has a question with no reply. Also, if the user closes the
tab mid-stream, `GeneratorExit` skips the whole save block and a completed answer is lost. Emit
`done` on every terminal path and save in a `finally`.

**F11. The user's message is sent to the model twice.** `_save_message` commits it, then
`_get_session_memory` re-reads it as the last history entry, then the agent appends it again.
Read history before saving, or drop the trailing user turn from history.

**F12. No effective timeout, and the worker can be killed mid-answer.** `TOOL_TIMEOUT = 10` is
declared and never used; `MAX_WALL_CLOCK` is only checked at the top of the loop; `timeout=` on a
streamed `requests.post` is per-read, so a dribbling provider keeps a round alive indefinitely.
Combined with F13 this exceeds gunicorn's `--timeout 120`, the worker is killed, the SSE
connection dies with no error frame and nothing is saved. With 4 sync workers, four concurrent
chats can block the whole plant's app. Enforce the wall clock between rounds *and* around each
tool call, and keep the total under the gunicorn timeout with margin.

**F13. N+1 query storms in the aggregate tools.** `_production_summary` materialises every pipe in
range then dereferences `p.production_order` / `p.machine` per pipe; `_get_order` re-runs the
dynamic `p.stages` query once per pipe *per stage name* (500 pipes × 11 stages ≈ 5,500 queries in
one tool call); `_defect_analysis` fetches each `Pipe` by id in a loop. Use `joinedload` or push
the grouping into SQL.

**F14. The "model does not support tools" fallback can never fire on OpenRouter.**
`str(requests.HTTPError)` is just `"400 Client Error: Bad Request for url: ..."` — the body is not
in it, so `'tool' in str(e).lower()` is always False and the user gets a raw 400 instead of the
intended degrade to keyword mode. Inspect `e.response.text`. Mirror-image bug on the Gemini side:
*any* 400 (bad key, oversized context) silently degrades to basic mode, hiding real failures.

## P2 — hardening

**F15. `run_sql` has no table allowlist.** The word-blacklist approach keeps needing patches (the
`users` hole, then `pg_authid`). Switch to an allowlist of the business tables and reject
`pg_catalog` / `information_schema` outright. Keep the existing text guard and the column
redaction as defence in depth.

**F16. The LIMIT wrapper breaks any query ending in a line comment.** `SELECT ... -- today only`
becomes `... -- today only) AS _agent_q LIMIT 201`, putting the closing paren inside the comment
→ syntax error on SQL the model did not write, which it cannot self-correct. Insert a newline
before the closing paren. Also floor `limit` at 1 — `limit=-1` currently yields `LIMIT 0`, and
Postgres rejects a negative limit.

**F17. `site_map` fails open for unmapped endpoints.** `_PERMISSIONS.get(endpoint)` returns None
and `_user_can_see` returns True, so any route missing from the hand-written dict is advertised to
every role. URL disclosure only — the routes still enforce their own decorator — but the default
must be deny, and the map should be derived from the route decorators rather than duplicated by
hand.

**F18. `find_page` ignores the `user` passed to `execute()`** and reads ambient `current_user`;
`search_pages(query, user=None)` returns the **unfiltered** map, so a non-request caller gets the
full admin URL list. It is the only tool whose authorisation differs from the one `execute()`
re-checks. Thread the `user` argument through — `execute` already has it.

**F19. Tool-call delta hardening (not a confirmed bug).** Per-index accumulation is correct for
spec-compliant deltas. Two cheap guards: append rather than assign `function.name` in case a
provider chunks it; and start a new accumulator when a non-empty `id` arrives that differs from
the stored one, so a provider that omits `index` for a second call cannot collapse two calls into
one and produce `{"a":1}{"b":2}`.

---

## Done means

- Every P0 fixed, with a test that fails before and passes after.
- A regression test for the `DetachedInstanceError` (assert the agent runs with a detached /
  snapshot user and never touches a live ORM instance inside the generator).
- `docker exec lab-chemical-dev python -m pytest tests/ -q` green, no regression in the 68
  pre-existing files.
- The nine §9 live checks from `spec.md` re-run against :9998 with output pasted — including the
  "all pipes by DN" case from F1, which must now return real per-DN counts you cross-check
  against `psql`.
- `RESULTS.md` updated: what you fixed, what you found to be a false positive, what is left.
