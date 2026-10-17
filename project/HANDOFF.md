# HANDOFF: 2026-10-17 (local Opus coordinator → next session)

Read this first, then CLAUDE.md → project/PLAN.md → DECISIONS.md → FINDINGS.md (§11–§23 are the recent ones) → WORKLOG.md.

## Where we are (one paragraph)
HiBot analyses Sirius incidents with read-only PROD DB evidence. Layers:
- knowledge (schema, codemap, graph, incidents, DB footprints, cards)
- tools layer + MCP (`hibot/tools`)
- skills (`knowledge/skills`)
- loops: v1 = deterministic case file + ArGPT JSON loop; v2 = same + open tool use

It is delivered as a module in **DbChangeTrackingSystem** (UI → API → HiBot HTTP service), working end to end locally. Learning works: UI feedback promoted 3 confirmed cards. Service default is **v1 + minimum-evidence guard** (D26), because v2 had better accuracy but bad calibration (FINDINGS §23). With native tool calling, Claude Haiku reached coordinator level on hard tickets (FINDINGS §20). The ArGPT gateway has no function calling; IT answers are expected next week (D25).

## IN FLIGHT: round 3 (making v2 good enough to become default)
- Spec: WORKLOG #47 + FINDINGS §23.
  1. Freeze a new holdout `knowledge/eval/holdout2.json` (sha256 in `knowledge/eval/README.md`).
  2. Deterministic calibration caps (`calibration.py`).
  3. Skills as checklists: the YAML front matter `required_evidence` in `knowledge/skills/*.md` + `hibot/agent/checklist.py`; v2 refuses `final_report` while required evidence is missing.
  4. Plan pass and critic pass in v2.
  5. Iterate on dev only (old test split / dev split / SM-1808060, SM-2172385, SM-2169006 twice each).
  6. FINAL: run v1 and v2 once each on holdout2 (`--eval-set knowledge/eval/holdout2.json`, tags `holdout2_v1`, `holdout2_v2`) → `knowledge/eval/compare_holdout2.md` → decide the default.
- State at handoff: a local Sonnet subagent was mid-work. Its changes are committed as a **WIP checkpoint** (commit message starts with "WIP round 3"); tests may not all pass.
- Next session:
  - run `python -m unittest discover tests`;
  - verify `holdout2.json` was frozen before any tuning (compare its sha256 with the README);
  - finish steps 3–6. Do NOT look at HiBot outputs on holdout2 tickets before the final run (D19).
- ArGPT: `chat-completion` hit its **daily usage limit** (429). Use the text transport (`HIBOT_LLM_FORMAT=argpt` / `HIBOT_LLM_FORMAT_V2=argpt`). If text also hits its limit, stop (runs are resumable with `--resume`) and never write wait loops.

## Environment facts (important for a cloud session)
- **Live work needs the corporate network and local secrets**: read-only PROD Sirius DB (EU `HIBOT_DB_EU`), ArGPT (`HIBOT_LLM_*`), the HiBot service key (`HIBOT_SERVICE_KEY`), all in the git-ignored `HiBot/.env`. A cloud session can do code, tests (no network/DB needed) and docs. It **cannot** run live analyses, evals, the service against PROD, or DbChangeTracking. Live steps (round 3 step 4–6) must run on the user's machine.
- HiBot has **no git remote** yet. To use a cloud session the user must push it (e.g. to Azure DevOps or GitHub). Raw data (`docs/*.json`, `docs/jira_export/raw/`, `runs/`, `knowledge/**/*.json` except cards/eval) is git-ignored. Regenerate knowledge with the commands in CLAUDE.md (needs the sibling repos under `d:\repos`).
- Subagents: always `model: "sonnet"` (user's token budget). Document everything in `project/*` and commit after each batch.

## Local demo processes (user's machine; stop/restart as needed)
| What | Start | Notes |
|---|---|---|
| HiBot service | `python -m hibot.service --port 8077` (`--loop v1` default) | Restart to pick up code changes (the min-evidence guard came after the last start). |
| DbChangeTracking API | `python tools/dev/run_dbct_api.py` | Reads secrets from files and sets the **safe local-run switches** (`Hangfire__ServerQueues__0=hibot`, `Monitoring__Scheduling__SyncOnStartup=false`, `Monitoring__Bootstrap__Enabled=false`) against the shared **preprod** gfsmpreprod DB. Never start the API against that DB without them (it would process and re-sync preprod's Hangfire jobs). |
| DbChangeTracking UI | `cd d:\repos\DbChangeTrackingSystemUI; npm start` | http://localhost:3000 → HiBot menu (Okta login) |

## Open items / waiting on the user
1. Commit the DbChangeTracking changes (branch `feature/hibot-module` in both repos; uncommitted on purpose). Before that, move `HiBot:ServiceKey` out of the API's `appsettings.json` (tracked!) into user secrets. Don't touch the user's own `docs/CURRENT_PROJECT_STATE.md` / `TASK_22_…` changes.
2. Security notes for the user/team: the API `appsettings.json` contains a committed plaintext MongoDB connection string; SiriusApi `appsettings.json`, the frontends' `translate.js` and `.env-cmdrc.json` contain secrets (never index or print them).
3. IT (D25): ArGPT function calling, embeddings body format, corporate Claude access, daily quota sizes (chat vs text).
4. After round 3: switch the service default to v2 only if it wins on holdout2 (`python -m hibot.service --loop v2`).
5. Later (PLAN): APAC DB access, automatic ticket intake (Jira notification e-mails), hosting the HiBot service on a server, local embeddings (D21).
