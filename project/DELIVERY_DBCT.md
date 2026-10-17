# HiBot in DbChangeTrackingSystem (D24): design

Status: **approved, being implemented** (2026-10-17). User decisions: the HiBot service runs **locally** for now (127.0.0.1:8077); **all Okta-authenticated users** may use it (no new role). Branch `feature/hibot-module` in both DbChangeTracking repos; no commit or push without user review. Source: Explore report on `d:\repos\DbChangeTrackingSystemAPI` and `d:\repos\DbChangeTrackingSystemUI`.

## Context
- DbChangeTrackingSystem has two parts. The legacy part does DB change tracking and release. The new **Sirius Monitoring Platform** handles read-only data sources, rules, incidents and Teams/email notifications. Its own docs say "No AI in Phase 1; architecture must remain AI-ready". HiBot is that AI step.
- API: .NET 8 controllers with EF Core. The Monitoring module is self-contained under `Monitoring/` (DbContext `MonitoringDbContext`, schema "Monitoring"). Background jobs use Hangfire (SQL storage). Users sign in through Okta; custom middleware sets `Auth-UserId`, and app-level roles live in `PlatformRoleAssignment`. DI registration is manual in `Program.cs`. Config comes from Key Vault outside Development.
- UI: CRA, React 19, MUI 6, Redux Toolkit, axios (`src/interceptors/axios.js`), Okta. Feature folders follow `src/features/monitoring/{api,pages,components,routes,store}`. Routes are spread into `src/routes/Routes.js`; the NavBar is hard-coded in `src/layouts/Dashboard/NavBar/index.js`.
- Git: both repos are on `master`. The API has the user's **uncommitted changes** (`docs/CURRENT_PROJECT_STATE.md` modified, `docs/implementation/TASK_22_…` untracked). We must not touch or commit them: work on a new branch and stage only our own files.

## Architecture (option a + API-side persistence)

```
UI (React)  --Okta-->  DbChangeTracking API (.NET 8)  --HTTP + shared key-->  HiBot service (Python)
  HiBot pages             HiBotController                                   POST /analyses -> run (worker)
  poll 3-5 s              HiBotAnalysis table (Monitoring schema)           GET  /analyses/{id}
                          Hangfire job polls HiBot, stores the report       POST /analyses/{id}/feedback
                          feedback stored + forwarded                       GET  /cards
```

- **HiBot service** runs wherever it can reach the read-only PROD Sirius DB and ArGPT, and keeps its secrets in its own `.env`. The DbChangeTracking API never touches the Sirius PROD DB or ArGPT, and never spawns Python.
- **API** owns history (who asked, when, report copy, verdict) and authorization, using a new permission or role in the existing authorization service.
- **Learning loop:** feedback from the UI → API stores it → forwards it to HiBot `POST /analyses/{id}/feedback` → existing card promotion (`hibot feedback`).

## HiBot service API contract (v1)

Auth: header `X-HiBot-Key: <shared secret>` (HiBot env `HIBOT_SERVICE_KEY`; API config `HiBot:ServiceKey`, from Key Vault). Base URL in API config `HiBot:BaseUrl`.

| Method | Path | Body / query | Response |
|---|---|---|---|
| POST | `/analyses` | `{"ticket_key": "SM-2172385"?, "text": "..."?, "region": "EU"?, "requested_by": "user id"}` (key and/or text required) | `202 {"run_id": "...", "status": "queued"}` |
| GET | `/analyses/{run_id}` | – | `{"run_id", "status": "queued|running|done|failed", "ticket_key", "created_at", "finished_at", "error"?, "report": <report.json>?, "report_markdown": "..."?}` |
| POST | `/analyses/{run_id}/feedback` | `{"verdict": "correct|wrong|partial", "note": "..."?, "by": "user id"}` | `200 {"ok": true, "card_effect": "..."}` |
| GET | `/cards` | `?status=confirmed|draft|rejected` | `[{"id","status","title","root_cause_type","resolution_action","confidence","support","updated_at"}]` |
| GET | `/cards/{id}` | – | full card JSON |
| GET | `/health` | – | `{"ok": true, "llm": "argpt_chat", "db_regions": ["EU"], "version": "…"}` |

- Runs execute in a worker with a concurrency cap (default 2) and a per-run timeout (default 240 s). The default loop is the best measured one (v1 or v2; configurable).
- `report` = the existing `report.json`: `summary`, `root_cause_type`, `resolution_action`, `classification`, `confidence`, `root_cause`, `evidence[]`, `code_locations[]`, `fix_steps[]`, `matched_card`, `ticket_window_changes`, `preflight`, `suggested_card`. `report_markdown` = the Turkish `report.md`.
- PII: same as the CLI (masked results, scrubbed ticket text).

## API module (DbChangeTrackingSystemAPI)
- `Monitoring/Domain/HiBotAnalysis.cs`: Id, TicketKey, InputText (nullable, capped), Region, RequestedBy, RunId, Status, CreatedAt, FinishedAt, Error, ReportJson (nvarchar(max)), ReportMarkdown, RootCauseType, ResolutionAction, Confidence, Verdict, VerdictNote, VerdictBy, VerdictAt.
- EF configuration + migration in the Monitoring schema.
- `Monitoring/Application/HiBot/HiBotService.cs` + `IHiBotClient` (typed HttpClient, `HiBot:BaseUrl`, `HiBot:ServiceKey`, timeout).
- `Monitoring/Controllers/HiBotController.cs` (`api/hibot`), based on `MonitoringApiControllerBase`:
  - `POST analyses` (create + enqueue Hangfire poll job)
  - `GET analyses` (paged list, filters: status/ticket/requester)
  - `GET analyses/{id}`
  - `POST analyses/{id}/feedback`
  - `GET cards`, `GET cards/{id}` (proxied)
- Hangfire job `HiBotAnalysisPollJob`: polls every 5 s until done/failed or 6 min, then stores the report. Idempotent; must not double-submit on retry.
- Authorization: new permission/role (e.g. `HiBotUser`) via the existing authorization service; PlatformAdmin always allowed.
- Task doc: `docs/implementation/TASK_23_HIBOT_MODULE.md` (repo convention).
- Tests in `DbChangeTrackingSystem.Monitoring.Tests` (service + controller with a fake client).

## UI module (DbChangeTrackingSystemUI)
- `src/features/hibot/api/hibotService.js` (same `request()` wrapper pattern).
- Pages:
  - **HiBot Analizleri** (list, filter, "Yeni analiz" button)
  - **Yeni analiz** (ticket key or pasted text, region)
  - **Analiz detayı** (status polling; when done: header chips for kök neden türü / yapılması gereken / güven / eşleşen kart; Markdown report; evidence and SQL blocks; **feedback buttons Doğru / Yanlış / Kısmen + note**)
  - **Bilinen Sorun Kartları** (list + detail)
- `src/features/hibot/routes/index.js` + spread in `Routes.js`; NavBar section "HiBot".
- Reuse the monitoring components (PageHeader, FilterBar, StatusChip, EmptyState). Markdown rendering via an existing dependency if present, else a small safe renderer (no raw HTML).
- Jest tests for the service and the pages (mock API), following existing patterns.

## HiBot side
- `hibot/service/` (FastAPI + uvicorn, optional dependency): contract above, worker pool, run store reuse (`runs/`), feedback → existing feedback logic, cards from `knowledge/cards`.
- `python -m hibot.service --host 0.0.0.0 --port 8077`; `HIBOT_SERVICE_KEY` required; health endpoint.
- Tests with FastAPI TestClient and a fake pipeline.

## Open questions for the user
1. Where will the HiBot service run? It needs read-only PROD Sirius DB + ArGPT reachability. Dev: localhost:8077.
2. Who may use it? Use a new role `HiBotUser`, or all authenticated monitoring users?
3. Later: an "Analyze with HiBot" button on Monitoring incidents (incidents are not Jira tickets → free-text mode).
