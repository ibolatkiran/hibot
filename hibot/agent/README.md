# hibot.agent - MVP orchestrator

Takes a Jira key or free text and produces a Turkish Markdown root-cause report backed by (read-only) query evidence.
The LLM never calls tools: it answers **one JSON action per turn**, this code validates and executes it (DECISIONS D8).

```
 Jira key / free text
        │
 [1] intake.py        key -> knowledge/incidents/tickets_enriched.json (scrubbed text, entities, similar, group)
        │             text -> tools/incidents/extract.py (same entity extraction, imported not copied)
 [2] similar cases    key -> precomputed `similar`/`group_id`;  text -> BM25 (tools/incidents/similarity.py)
        │
 [3] cards.py         knowledge/cards/<id>.json  ── match (score, reasons) ──► candidate cards + concrete verification queries
        │
 [4] code_context.py  hibot.knowledge.graph.CodeGraph (fallback: knowledge/codemap/trace_index.json)
        │
        ▼
 [5] investigate.py   case file ─► LLM ─► action JSON ─► validate (repair loop, 2 retries) ─► executor.py ─► observation ─┐
        │                                     ▲                                                                           │
        │                                     └───────────────────────────────────────────────────────────────────────────┘
        │             stops on final_report or budget (then one forced final_report, else fallback report)
        ▼
 [6] report.py        grounding (unexecuted queries are not evidence, confidence capped) ─► Turkish Markdown
        │             store.py: runs/<run_id>/{input,incident,matches,queries}.json steps.jsonl report.json report.md
        ▼
 [7] feedback.py      python -m hibot feedback <run_id> --verdict correct|wrong|partial  ─► knowledge/feedback/feedback.jsonl + cards
```

## Run

```
set PYTHONIOENCODING=utf-8
python -m hibot analyze SM-1777386 --llm fake                 # scripted demo, no LLM, no DB (dry-run)
python -m hibot analyze --text "Work order 12345678 history shows 1 record" --llm fake
python -m hibot analyze SM-1777386 --llm http                 # company endpoint (HIBOT_LLM_*, see hibot/llm/README.md)
python -m hibot analyze SM-1777386 --llm http --live --region EU --max-steps 8 --max-queries 6
python -m hibot feedback <run_id> --verdict correct --note "root cause confirmed"
python -m unittest discover tests
```
`--llm fake` is a fixed script (inspect endpoint -> `wo_history_function` -> `wo_status_timeline` -> final report for history
tickets; a trivial "unknown" report otherwise). It uses placeholder ids (`tenant_id=1`, work order 12345678 unless the ticket has one).
Demo runs are flagged `demo`; feedback on them never creates or changes cards.

DB modes: `dry-run` (default): template parameters are coerced, SQL validated and rendered, **nothing is sent**; the report says so
on top and caps confidence at 0.6. `live` (`--live`): needs `HIBOT_DB_EU` / `HIBOT_DB_APAC` (else the CLI exits with an error) and
runs through `hibot.db.runner.QueryRunner` (read-only, masked, audited). Region = `--region`, else guessed from the ticket country
(APAC countries / `AH*` company codes), else EU.

## JSON contracts (`actions.py`)

```
{"action":"run_query","query_id":"wo_status_timeline","params":{"tenant_id":5,"work_order_id":123},"region":"EU","why":".."}
{"action":"run_sql","sql":"SELECT TOP 20 ... WHERE TenantId = 5","region":"EU","why":".."}       # ad-hoc, hibot.db.safe_sql, allow_pii=False
{"action":"inspect_code","target":{"kind":"endpoint|screen|db_object|error_text|text","value":"..","app":"crm"},"why":".."}
{"action":"read_source","path":"SiriusApi/Sirius/Controllers/X.cs | dbo.GetSiriusWorkOrderHistory | <absolute>","start_line":1,"end_line":80,"why":".."}
{"action":"final_report","summary":..,"root_cause_type":"config|code_defect|master_data|integration|user_error|access|data_issue|change_request|unknown",
 "resolution_action":"data_fix|config_change|code_change|master_data_change|explain_to_user|grant_access|escalate_other_system|route_to_backlog|none|unknown",
 "root_cause":..,"confidence":0.0-1.0,"evidence":[{"type":"query|code|similar_case|card","ref":..,"finding":..}],
 "code_locations":[{"repo":..,"path":..,"line":..,"why":..}],"fix_steps":[..],"verification_query_ids":[..],"suggested_card":{..}}
```
Prompts: `hibot/prompts/system.md` (role, strategy, evidence rules, Turkish output) + `actions.md` (the contracts above).
The first user message (`prompting.py`) is the case file: ticket, entities, similar cases, candidate cards with concrete verification
queries, automatic code context, the compact template catalog (`TemplateRegistry.catalog_text`), mode and budget.
Each later turn carries `OBSERVATION (step n)` plus the budget status.

### Two-axis classification (`axes.py`, round 1)
The LLM answers `root_cause_type` (WHY) and `resolution_action` (WHAT to do); the legacy `classification` is DERIVED (never chosen):
root_cause_type in {master_data, access, integration, code_defect} -> same name; else resolution_action `data_fix` -> `data_correction`;
else config -> config, user_error -> user_error, data_issue -> data_correction, unknown -> unknown. Reports/cards/runs that only carry the
legacy label get axes derived back (`LEGACY_TO_AXES`). A legacy-only `final_report` is still accepted (fake demo, old tests). The rendered
report shows "Kök neden türü" and "Yapılması gereken". Cards have optional `root_cause_type` / `resolution_action`
(`python tools/learning/add_card_axes.py` migrates: confirmed Egypt card user_error/explain_to_user, footprint drafts config/data_fix, or
data_issue/data_fix when the transition is configured).

### Round 2 additions
* **Taxonomy:** `root_cause_type` `change_request` + `resolution_action` `route_to_backlog` (legacy `change_request`, derived). Use: the system works as coded and the reporter wants
  different behaviour (typically a hard-coded permission list). Permission-scope guidance is in `axes.py` and the system prompt.
* **UI gating** (`ui_gating.py`, deterministic): for capability / permission tickets ("cannot edit X", "please update field Y") the endpoints of the screen hints whose name shares
  >= 2 content tokens with the ticket are traced in the code graph; the CALLING screens (`callers`) are opened through the guarded reader and compact gating snippets are extracted
  (feature token + `includes(`, UserType, RoleId, TenantId, SYSTEM_CONFIG, REACT_APP_, TODO; file:line; hard-coded id lists labelled Live/Test). Injected into the case file as
  "UI GATING"; `inspect_code` on an endpoint appends the same section to the observation. The playbook then runs `tenant_lookup` (ticket country) + `user_type_lookup` (Ids from the
  Live list + TenantId) so the ids resolve to tenant + role name.
* **Column provenance** (`provenance.py`, deterministic): value problems (0 / wrong / missing / not showing) -> column of `Operation.WorkOrder|WorkOrderDetail|WorkCase` whose CamelCase tokens
  all occur in the ticket (labor->labour, charge->cost ...) -> writer procedures from `knowledge/schema/objects.json` `written_by` filtered by a source grep for `<Column> =` -> compact window of the
  best one, its input tables/filters and the templates that touch them: "COLUMN PROVENANCE" section (e.g. LabourCost <- dbo.PricingWorkOrder = SUM(Price) of Material.Type = 1 lines).
* **Playbooks:** labour / price / cost tickets with SKU or WO -> `wo_detail_material_types` (<= 2 WOs) + `product_labour_setup`; notification tickets -> `job_health` +
  `outbound_queue_backlog` around the ticket time; performance tickets -> `db_errors_recent`, `job_health`, `outbound_queue_backlog` (ticket time +- 24 h); master-data "add" requests
  whose SKU exists NOW get a SNAPSHOT CAVEAT (tickets are analysed after handling: the object exists because the fix was applied -> master_data, not user_error).
* **Source guard** (`Investigation._source_unread`): `unknown` / "cannot tell code from configuration" while a stored procedure or screen behind the symptom is known
  (`Executor.known_sources`: case-file gating/provenance files + inspected objects) and no `read_source` ran is refused once (merged into the early-stop refusal when both apply).

### Confidence calibration (`calibration.py`, round 1)
The LLM confidence is post-processed deterministically (in `Investigation._ground` and again in `pipeline` after a card was applied).
`confidence_raw` = LLM value, `confidence_caps_applied` = [{cap, reason, from, to}], `supporting_evidence` = what counted.
Caps: 0.5 when no query was executed; 0.6 unless some evidence item points to an EXECUTED query with a non-empty relevant result (not
`wo_locate`/`tenant_lookup`/`wo_header`; 0 rows count for `user_access_lookup`/`product_sku_lookup`), a supported card, or a ticket-window
manual-fix finding; 0.69 (round 3, was 0.8) unless a supported CONFIRMED card or >= 2 independent supporting evidences (distinct query ids / distinct
ad-hoc queries / cited source files really read; the ticket-window finding counts as `wo_field_changes`, a supported card as one more);
0.85 unless a supported CONFIRMED card or >= 3 independent evidences; 0.55 for `user_error` / `config` on a status or data-correction
ticket whose ticket window shows NO status change (unless a supported card or a `status_step_check` that proves the transition missing);
0.6 while required checklist evidence of the selected skill is missing (v2).

### v2 loop additions (round 3)
`checklist.py`: every symptom skill has a machine-readable header (`required_evidence`); the program selects up to 2 skills from the ticket,
injects their text + checklist into the case file and refuses a `final_report` (max 2 times) while required evidence is missing
(waivers with a concrete reason are recorded). `loop_v2.py`: turn 1 is a validated PLAN (step 0), one CRITIC call after the first accepted
draft (`report.critic`; may send the model back for one more round; only lowers the confidence). Kill switches: `HIBOT_V2_PLAN=0`,
`HIBOT_V2_CRITIC=0`, `HIBOT_V2_CHECKLIST=0`.

### Known-issue card (`knowledge/cards/<id>.json`, `cards.py`)
`id, title, symptom_signature{problem_type, app, keywords[], entity_requirements[]}, root_cause, verification_queries[{query_id,
params_from_entities}], fix_steps[], code_locations[], confidence, status draft|confirmed|rejected, evidence_tickets[], created_by,
created_at, history[]`. `params_from_entities`: `"entities.sku_codes[0]"`, `"$tenant"` (resolve with `tenant_lookup`) or `{"const": v}`.
Match score = 0.25 problem_type + 0.15 app + 0.45 keyword share + 0.15 entity requirements met (unmet requirements halve it,
confirmed +0.05, rejected never). Two clearly marked EXAMPLE draft cards ship in `knowledge/cards/`.

### Card review CLI (`cardcli.py`)
```
python -m hibot cards list [--status draft|confirmed|rejected]      # id, status, confidence, support (evidence tickets), title
python -m hibot cards show <id>                                      # signature, root cause, verification queries, fix steps, evidence, history
python -m hibot cards confirm <id> [--note ".."] [--root-cause ".."] [--fix-step ".." ...] [--classification user_error] [--title ".."]
python -m hibot cards reject <id> --note ".."                        # rejected cards never match and are never revived by feedback
python -m hibot cards annotate <id> --note ".."                      # history note only, status unchanged
```
`confirm` sets `status=confirmed`, `confidence=max(current, 0.85)`, keeps the evidence, replaces the overridden fields (`--fix-step` repeats and
replaces ALL fix steps) and appends a history entry `{ts, event, who:"user", note, overrides{field:{old,new}}}`. Cards are re-validated on save.
Card schema additions: optional `classification` (same values as `final_report.classification`) and optional `state_signature`
`{tenant_id, from_status_id, to_status_id, from_status_name, to_status_name, target_words[], transition_configured}`.
`tools/learning/mine_patterns.py` emits it for new footprint drafts; `python tools/learning/add_state_signature.py` migrates existing cards.

### Known-issue shortcut (`known_issue.py`)
After preflight (tenant, current status) and the baseline playbook, cards with a `state_signature` are matched on STATE:
`0.40 tenant == card tenant` (mandatory) `+ 0.35 status of the WO AS OF TICKET CREATION == from_status` (current status minus the status changes of
`wo_field_changes` newer than `created`; no log / no created time -> current status) `+ 0.25 ticket text mentions a target_word` (to-status name,
`cancel` for "Cancelled", ...) `+ 0.05 confirmed`; one of the last two signals is required; threshold 0.65; confirmed ranks above draft; rejected never.
The best card's `verification_queries` run before the LLM (budget-counted, `wo_locate` skipped, identical queries already run by the
playbook are reused, not repeated). `status_step_check` is evaluated against the card's `transition_configured` claim: supports / contradicts /
inconclusive. The case file gets a "BİLİNEN SORUN KARTI" section (pinned, never truncated); the report shows
`Eşleşen kart: <id> (status), doğrulama: destekliyor|çelişiyor|doğrulanamadı`, cites the card as `evidence` (type `card`, so
`hibot feedback --verdict correct` adds the ticket to the card's evidence) and, for a CONFIRMED card that is supported, reuses the card's
root_cause_type / resolution_action (legacy classification derived), root cause and fix steps verbatim, but ONLY when the ticket text mentions the card's target action (a state-only match, tenant + status, stays a hypothesis: verdict `doğrulanamadı`)  (LLM extras are appended and prefixed `[LLM ek önerisi]`; a historical manual force of the same
transition is added as "SÜREÇ İHLALİ"). A contradicting card changes nothing in the report except the explicit `çelişiyor` line. Live mode only.

## Budgets (`config.Budget`, CLI `--max-steps`, `--max-queries`)
| budget | default | on exhaustion |
|---|---|---|
| actions (steps) | 8 | one forced `final_report` request; invalid -> fallback report (`unknown`, confidence 0) |
| queries (`run_query` + `run_sql` that pass validation; preflight + baseline playbook + LLM) | 10 | further queries are REJECTED observations |
| ad-hoc SQL | 2 | REJECTED observation |
| prompt chars | 60 000 | older observations shortened (600 -> 250 -> 100 chars); newest two kept |
| observation | 4 000 chars, 15 rows, 160 chars/cell, 120 source lines | truncated with a marker |
| JSON repair | 2 retries per LLM call (do not consume steps) | `JsonRepairFailed` -> fallback report |
Identical repeated actions are not re-executed ("DUPLICATE"). Rejected actions cost a step but no query budget.

## Preflight and early-stop guard
* `preflight.py` (runs in `pipeline.analyze` before the LLM): for up to 3 work-order-like numbers of the incident entities the
  `wo_locate` template (param `Number`) runs through the Executor (live: executed; dry-run: rendered, `executed=false`). The
  ticket country is the reporter's country, so the real TenantId comes from here. Result goes into the case file as
  "RESOLVED WORK ORDERS" and the queries are recorded in queries.json and count in the query budget. 0 rows in the selected
  region and the other region configured (`HIBOT_DB_EU`/`HIBOT_DB_APAC`) -> the other region is tried; found only there ->
  the run's region switches. `run.json`/report.json gets a `preflight` block.
* Early-stop guard (`Investigation._premature`): a `final_report` with classification `unknown` or confidence < 0.4 while
  fewer than half of `max_queries` were used and steps remain is refused ONCE per run; the LLM gets an observation with
  concrete suggestions (queries that returned 0 rows -> other identifier/region/template). It costs one step.
  Weak = unknown / confidence < 0.4 with at least 3 queries of budget left.
* Shallow-status guard (same once-per-run allowance): the ticket mentions a status / cancel / error, a work order is known, and no executed
  query (baseline included) touched `wo_status_timeline` / `wo_field_changes` / `wo_status_transition_check` / `status_step_check`
  (or ad-hoc SQL on WorkOrderGpsInfo / WorkOrderJsonLog / StatusStep) -> even a confident final_report is refused once.

## Baseline playbooks (`playbooks.py`, live mode only)
After preflight, deterministic queries run before the LLM (counted in the query budget; `RESERVE`=2 queries always stay for the LLM):
* status / cancel / reject / close / "cannot change status" keywords (any problem_type) + resolved WO: `wo_header`, `wo_status_timeline`,
  `wo_field_changes`, `wo_status_transition_check`, then `status_step_check` for the last status change found in the ticket window;
* `integration_failure`: `wo_header`, `wo_erp_sap`, `sap_rfc_rest_calls`; `notification_failure`: `wo_header`, `notifications_for_wo`;
* `master_data_request` with a SKU and no WO: `tenant_lookup` (ticket country) then `product_sku_lookup` if exactly one tenant matches.
* access requests (problem_type `access_request` or access words): user codes (code-like tokens only: letter+6-8 digits, labelled "user id / kullanıcı kodu ..."
  tokens containing a digit, code-like e-mail local parts; never names or e-mail addresses, see `access.py`) go to `entities.user_codes` (intake);
  `user_access_lookup` per code (max 2) and `tenant_lookup` of the ticket country when the user exists. No code in an `access_request` -> a hint
  ("kullanıcı kodu ticket'ta yok") tells the LLM to answer access / grant_access with confidence <= 0.6 instead of unknown (also in dry-run);
* `integration_failure` WITHOUT a resolved work order: `job_health`, `outbound_queue_backlog`, `db_errors_recent` with `FromDate`/`ToDate` = ticket
  `created` -/+ 24 h (UTC; the first two got these optional params). These logs are not tenant scoped. Compact summaries (failing / never-run / unfinished jobs only).
Each observation is compacted (row caps, newest rows kept, `OBS_CAP` 1500 chars) into the "BASELINE EVIDENCE" case-file section, which is
never truncated. SM-2139009 case prompt: ~23.6k chars (system 9.8k + case file); catalog purposes are shortened to ~190 chars.

## Ticket-window footprint (`ticket_window.py`, D13)
From `wo_field_changes` + `wo_status_timeline` and the ticket `created`/`resolved` times: what changed on the WO between ticket creation
and resolution (window -5 / +15 min). Time zones: ticket `created` = Jira local UTC+03:00 (verified against the raw XML, 3790/3790),
`resolved` carries its offset, DB datetimes are UTC. A status change in `Log.WorkOrderJsonLog` without a matching
`Operation.WorkOrderGpsInfo` row (+-5 min) is a suspected MANUAL DB FIX (`high` when `UpdateUserId` also did not change, `medium` otherwise).
Injected into the case file ("Ticket süresince iş emrinde olan değişiklikler"), added as a deterministic first evidence item, rendered in
report.md and stored in report.json as `report.ticket_window_changes` (raw material for cards later). Recomputed at report time from all
executed queries, so it also works when the LLM ran the queries itself.

## Safety rules
* DB only through `hibot.db` (templates preferred; ad-hoc SQL via `safe_sql.validate`, PII columns refused, `TOP` injected). The agent has
  no other DB code path. Rows are masked before they reach the model, the report or `runs/`.
* `read_source` (`safety.py`): only the allowed repos of CLAUDE.md (`config.DEFAULT_REPO_ROOTS`, override `HIBOT_REPO_ROOTS`), `..` segments
  refused, symlinks resolved before the containment check, secret deny-list (`appsettings*.json`, `.env*`, `.env-cmdrc.json`, `translate.js`,
  `web.config`, `*.pfx`, plus `*.pem/*.key/*.p12/secrets.json`), `.git/node_modules/bin/obj` refused, text suffixes only, 120 lines,
  credential assignments scrubbed + `masking.mask_text`.
* Grounding: evidence of type `query` is marked `executed | not_executed | not_run`; with 0 executed queries confidence is capped at 0.6.
  The report lists every query with the exact SQL (parameter values are inlined **for display only**; execution always binds parameters).
* Jira comments and similar cases are labelled unverified in the prompt (D13).
* Learning: only developer feedback changes cards; wrong verdicts lower confidence and add `contradicted`; rejected cards are never revived;
  suggested-card queries must exist in the template registry; card ids are validated (`[a-z0-9_-]`, no path traversal); demo runs never teach.

## Files
`intake.py` `cards.py` `code_context.py` `prompting.py` `actions.py` `executor.py` `safety.py` `investigate.py` `report.py` `store.py`
`feedback.py` `playbooks.py` `ticket_window.py` `pipeline.py` (glue) `demo.py` (FakeLLM scenario) `config.py`. CLI: `hibot/__main__.py`.

## Not done / next
* Real endpoint adapter and prompt tuning (needs the endpoint docs); embeddings for similarity (D10).
* `runs/`, `knowledge/cards/*.json`, `knowledge/feedback/` need `.gitignore` decisions (`knowledge/**/*.json` is ignored: add
  `!knowledge/cards/*.json` to track cards; add `runs/`).
* Evaluation set (30-50 historical cases), implicit feedback signals, Jira write-back (phase 3/4).

## Unified loop "v2" (`loop_v2.py`, `python -m hibot analyze <KEY> --loop v2`)
Default stays v1 until v2 is measured (`knowledge/eval/compare_v2.md`). v2 = the deterministic case file above + an open-ended multi-turn chat with
the 19 generic tools (`hibot/tools`, incl. `read_skill`) driven by ArGPT **chat-completion** (`HIBOT_LLM_FORMAT=argpt` in `.env` is upgraded to
`argpt_chat` for v2; any other explicit format is respected; `HIBOT_LLM_FORMAT_V2` overrides; `loop_v2.v2_format`).
```
preflight / playbooks / known-issue card / ticket window / UI gating / provenance  (pipeline.py, unchanged)  ──► CASE FILE = first user message
system message = role + environment + investigation protocol + skills index + tool catalog + classification rules + output protocol (prompts/v2_system.md)
assistant JSON:  {"thought","tool","args"}  |  {"thought","tools":[<=3 independent calls]}  |  {"final_report":{...existing schema...}}
user message  :  OBSERVATION (step n) RESULT i/k <tool> <args> <compact result, truncated with a marker> + budget status line
```
* **Protocol / repair:** `parse_turn` + `jsonio.complete_json` (fences, prose, trailing commas, 2 repair retries that do not consume steps). The final report is
  normalised (clipped strings, evidence type aliases, `fix_steps` as string) before schema validation; flat v1-style reports are accepted too. Unknown tool or bad
  arguments are ERROR observations (with the tool list / usage), count as a tool call; an identical repeated call is a DUPLICATE observation and costs no budget.
* **DB access:** `run_template` and `sql` are routed through the same `Executor` as v1 (hibot.db templates / safe_sql / masking / audit), so they are budgeted,
  dry-run safe, appear in `queries.json` in the scorer's shape and feed calibration and the Turkish report. Other tools go through `registry.call_tool`
  (`read_file` marks a source as read, `graph_*` results feed the source guard, `graph_trace_endpoint` appends the UI-gating text of the callers).
* **Budgets** (`Budget.for_loop("v2")`): 18 steps (LLM turns), 30 tool calls, 25 queries (12 ad-hoc), context 90 000 chars, one tool observation 6 000 chars
  (SQL: 25 rows). Exhausted -> ONE forced `final_report` turn (merged into the last user message); invalid -> fallback report.
* **Context control:** system + case file + all assistant turns (the model's notebook: "write the key facts in every thought") are never trimmed. Over budget the OLDER
  observations are shortened progressively (1500 -> 600 -> 250 -> 100 -> omitted), the newest 2 turns stay whole. The run record keeps the full texts.
* **Guards, each at most once:** early stop (unknown / confidence < 0.4 with queries left), shallow status (status ticket, no status query ran), source not read
  (unknown while a known SP / screen was never opened; v1 logic reused through subclassing), and the v2-only **self-check** (final report after < 2 real investigation
  calls, `read_skill` does not count, unless a confirmed supported card decides: asks for mechanism read / falsification / how the field is set today).
* **After the loop:** unchanged: grounding against executed queries, deterministic calibration, axes normalisation, known-issue card application and evidence linking,
  ticket-window evidence, Turkish report, `report.json` with `preflight`, `ticket_window_changes`, `loop`, `tool_calls`. `steps.jsonl` has one line per turn with its `calls`.
* **Shared rules:** `hibot/prompts/classification.md` (two-axis definitions, decision rules, snapshot caveat, known-card rule) is used by v2 and by
  `hibot/brains/claude_agent.py` (`build_system()`).
* `python tools/eval/run_eval.py --tag T --loop v2 --llm http --live ...`: LLM outages (HTTP 429 daily limit / 5xx) are recorded as errors (not as answers), so `--resume`
  redoes them, and the batch aborts after 3 such tickets in a row.
