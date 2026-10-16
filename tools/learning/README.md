# tools/learning: learning from DB footprints (D13)

Developers rarely write the real diagnosis into Jira, but their fixes leave footprints in the database. This tool reads those
footprints for all historical tickets, deterministically (no LLM), and turns recurring ones into **draft** known-issue cards.

## Pipeline

```
knowledge/incidents/tickets_enriched.json
        |  batch_footprint.py   (PROD read-only, EU only, sequential, resumable)
        v
knowledge/learning/ticket_windows.jsonl   (git-ignored, derived from PROD)  + checkpoint.json (git-ignored)
        |  mine_patterns.py     (offline)
        v
knowledge/learning/summary.md (tracked)  +  knowledge/cards/draft_footprint_<slug>.json (status draft)
```

## Run

```
cd d:\repos\HiBot
set PYTHONIOENCODING=utf-8
python tools/learning/batch_footprint.py --limit 20          # dry pass (newest eligible tickets first)
python tools/learning/batch_footprint.py                     # everything not yet done; Ctrl+C is safe, re-run resumes
python tools/learning/batch_footprint.py --since 2026-06-01  # only tickets created on/after the date
python tools/learning/batch_footprint.py --reset             # start over (truncates the jsonl, drops the checkpoint)
python tools/learning/mine_patterns.py                       # summary.md + draft cards (--no-cards for the summary only)
python -m unittest tests.test_learning
```
`HIBOT_DB_EU` comes from the git-ignored `.env` (loaded by `hibot.envfile`). Options: `--delay` (seconds between tickets, default 0.3).
Tickets that errored are recorded (status `error`) and counted as done; delete their lines from the jsonl to retry them.

## What is recorded per ticket (no free text, no person names)
`key, created, resolved, resolution, problem_type, app_guess, country, group_id, numbers`, then per located work order
(`wo_locate`, up to 3 numbers x 2 rows): `work_order_id, tenant_id, tenant_name, current_status_id` and a `window` block computed by
`hibot.agent.ticket_window` (window = created-5 min .. resolved+15 min; created is UTC+03:00, DB is UTC):
`changes[]` (status from/to with names, other changed fields, `history_row_found`, `update_user_unchanged`, `transition_allowed`
from `status_step_check`, `steps_into_target`), `manual_level` (none/low/medium/high), `no_change_in_window`, and an `outside` baseline
(status changes of the same work order outside the window and how many lack a history row). Ticket `status`: `ok`, `not_found_eu`, `error`.

## Draft cards
Patterns (tenant, from -> to status, transition allowed) that occur in >= 2 tickets become `draft_footprint_t<tenant>_<from>_to_<to>.json`
(`created_by: batch_footprint`, confidence <= 0.7, `symptom_signature` from keywords common to the tickets' scrubbed summaries,
verification queries `wo_locate`, `wo_status_timeline`, `wo_field_changes`, `status_step_check`). Existing cards that are not
batch_footprint drafts (e.g. confirmed ones) are never overwritten; re-running updates the draft and appends an `evidence_added` history entry.

## Caveats
Heuristic: a status change without a history row can also come from a job or trigger. See `knowledge/learning/summary.md`
(baseline section and limitations) before trusting a draft. Drafts need a developer's confirmation.
