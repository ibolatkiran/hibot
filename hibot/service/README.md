# HiBot HTTP service

FastAPI wrapper around the analysis pipeline, called by the DbChangeTracking API (contract: `project/DELIVERY_DBCT.md`).
Runs the same code path as `python -m hibot analyze <key> --live --llm http` (no subprocess).

## Run
```bash
pip install fastapi uvicorn          # optional "service" extra (httpx is needed for the tests only)
python -m hibot.service [--host 127.0.0.1] [--port 8077] [--workers 2] [--timeout 240] [--loop v1|v2]
```
Settings come from the git-ignored `.env` (loaded by `hibot.envfile`) or the real environment:

| Variable | Purpose |
|---|---|
| `HIBOT_SERVICE_KEY` | shared secret; **required**, the service refuses to start without it |
| `HIBOT_DB_EU` / `HIBOT_DB_APAC` | read-only PROD connection strings (live analysis) |
| `HIBOT_LLM_*` | ArGPT settings |

Every endpoint requires header `X-HiBot-Key` (constant-time compare, never logged); otherwise `401`.

## Endpoints
| Method | Path | Notes |
|---|---|---|
| POST | `/analyses` | `{"ticket_key"?, "text"?, "region"?: EU/APAC, "requested_by"}` -> `202 {run_id, status:"queued"}`. Key format `ABC-123`, text <= 8000 chars |
| GET | `/analyses/{run_id}` | `status` queued/running/done/failed, `error`, and when done `report` (the report object) + `report_markdown` |
| POST | `/analyses/{run_id}/feedback` | `{"verdict": correct/wrong/partial, "note"?, "by"}` -> `{ok, card_effect}`; 409 if not done or feedback already given |
| GET | `/cards` | `?status=confirmed/draft/rejected`; compact list |
| GET | `/cards/{id}` | full card |
| GET | `/health` | `{ok, llm, db_regions, version}` |

Errors are JSON `{"error": "..."}` with 400/401/404/409/500; no stack traces.

## Behaviour
- Bounded worker pool (`--workers`), per-run timeout (`--timeout`) -> `failed` ("analysis timed out ..."). A timed-out analysis thread cannot be killed; it keeps its slot until it ends.
- State: in-memory index plus `runs/<run_id>/service_state.json`. After a restart finished runs are still served; queued/running ones become `failed` ("service restarted"). The pipeline's own run folder (`report.json`, `report.md`) is referenced by `pipeline_run_id`; feedback uses it.
- `--loop v2` selects the v2 investigation loop (`pipeline.analyze(loop="v2")`); default v1.

## curl
```bash
KEY=change-me
curl -s -X POST localhost:8077/analyses -H "X-HiBot-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"ticket_key":"SM-2169006","requested_by":"user1"}'
curl -s localhost:8077/analyses/<run_id> -H "X-HiBot-Key: $KEY"
curl -s -X POST localhost:8077/analyses/<run_id>/feedback -H "X-HiBot-Key: $KEY" -H 'Content-Type: application/json' \
     -d '{"verdict":"correct","note":"ok","by":"user1"}'
curl -s "localhost:8077/cards?status=confirmed" -H "X-HiBot-Key: $KEY"
```
