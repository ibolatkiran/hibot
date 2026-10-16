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
{"action":"final_report","summary":..,"classification":"code_defect|master_data|access|data_correction|integration|config|user_error|unknown",
 "root_cause":..,"confidence":0.0-1.0,"evidence":[{"type":"query|code|similar_case|card","ref":..,"finding":..}],
 "code_locations":[{"repo":..,"path":..,"line":..,"why":..}],"fix_steps":[..],"verification_query_ids":[..],"suggested_card":{..}}
```
Prompts: `hibot/prompts/system.md` (role, strategy, evidence rules, Turkish output) + `actions.md` (the contracts above).
The first user message (`prompting.py`) is the case file: ticket, entities, similar cases, candidate cards with concrete verification
queries, automatic code context, the compact template catalog (`TemplateRegistry.catalog_text`), mode and budget.
Each later turn carries `OBSERVATION (step n)` plus the budget status.

### Known-issue card (`knowledge/cards/<id>.json`, `cards.py`)
`id, title, symptom_signature{problem_type, app, keywords[], entity_requirements[]}, root_cause, verification_queries[{query_id,
params_from_entities}], fix_steps[], code_locations[], confidence, status draft|confirmed|rejected, evidence_tickets[], created_by,
created_at, history[]`. `params_from_entities`: `"entities.sku_codes[0]"`, `"$tenant"` (resolve with `tenant_lookup`) or `{"const": v}`.
Match score = 0.25 problem_type + 0.15 app + 0.45 keyword share + 0.15 entity requirements met (unmet requirements halve it,
confirmed +0.05, rejected never). Two clearly marked EXAMPLE draft cards ship in `knowledge/cards/`.

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
