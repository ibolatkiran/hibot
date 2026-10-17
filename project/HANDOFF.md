# HANDOFF: 2026-10-17 (end of day; cloud Claude session -> next agent, e.g. Copilot)

Read this first, then CLAUDE.md -> project/PLAN.md -> DECISIONS.md (D26, D27, D28 are new) -> FINDINGS.md (§23-§25 are the newest) -> WORKLOG.md (bottom).

## Where we are
HiBot analyses Sirius incidents with read-only PROD DB evidence (knowledge, tools layer + MCP, skills, v1 loop with deterministic case file, v2 loop with open tool use, delivered as a module of DbChangeTrackingSystem). The service default is **v1 + minimum-evidence guard** (D26, confirmed by D27).

## Round 3 is DONE (result: v1 stays the default)
- holdout2 (40 blind tickets, sha256 in `knowledge/eval/README.md`; the hash is over CRLF bytes, the check normalises line endings) was run once per loop, no tuning afterwards.
- Pre-registered criterion for v2 as default: confidently-wrong(rct) <= v1 AND both axes >= v1 + 5 points. v2 failed it (both axes 42% vs 58%). Details: `knowledge/eval/compare_holdout2.md`, FINDINGS §24, D27.
- v2's 4 error runs (SM-1957195, SM-1934122, SM-2080016, SM-1833067) were ArGPT HTTP 500 outages; the verdict holds even if all four were correct (53% < 58%).
- **holdout2 is spent (D19).** Do not tune on it. Any new loop needs a NEW blind set (holdout3).

## Claude Haiku 5.5 with native tool calling (experiment, D28)
- The user explicitly allowed the Anthropic API for this experiment only (D20 "ArGPT mandatory" still holds for the service and for automation).
- `python tools/eval/run_eval_claude.py run|score --tag holdout2_claude_haiku --eval-set knowledge/eval/holdout2.json --n 40 ...` (the `--eval-set` flag was added today). Cost $0.705.
- Result (FINDINGS §25, `knowledge/eval/compare_holdout2_claude_haiku.md`): rct 55%, both axes 45%, confidently-wrong(rct) 8%, 87 s/ticket. Better than v2, **worse than v1** (68% / 58%). It loses where v1's deterministic pre-steps (tenant/SKU/WO resolution, base query sets, ticket window) matter. Native tool calling alone does not beat the case-file approach.
- NOT tested: Sonnet/Opus (about 20x Haiku price by the table in `hibot/brains/claude_agent.py`, roughly $14 for 40 tickets, an estimate), and **case file + native tool calling combined**.

## Next work (recommended order)
1. **Hybrid brain** (`hibot/brains/`): put the v1/v2 case file (deterministic pre-steps; the v2 loop already builds it as its first message, see `hibot/agent/loop_v2.py` and `tests/test_loop_v2.py`) into the Claude brain's first message, then let it use the tools. Can be written and unit-tested in the cloud (no DB needed).
2. **Leak check test:** HiBot's `similar_tickets` tool and card matching search the same 3,790-ticket index. Verify (with a test) that the ticket under evaluation is excluded from `similar_tickets`/cards. Not verified for holdout2.
3. **holdout3:** new blind set from the unused part of the 3,790 tickets (19 Jira XML chunks, already ingested; only ~87 of them used in eval sets). The bottleneck is hand labelling (the LABELS table in `tools/eval/build_holdout2.py`), not tickets. Same selection rules (not in skills/cards/project docs/eval sets). Freeze its sha256 BEFORE any loop touches it.
4. Compare v1 vs hybrid once on holdout3 with a criterion written down first.
5. **Attachments (digiport) support** (PLAN, user's idea): first measure how many eval tickets have attachments and whether those do worse (Jira XML `attachments`, no download needed), then download, OCR, vision; PII rules apply.
6. IT questions (D25): ArGPT function calling, corporate Claude access, vision support, embeddings format, quota sizes. If corporate Claude/ArGPT tool calling arrives, plug it into the hybrid brain.

## Environment facts (important)
- **Live work needs the corporate network and local secrets** (git-ignored `HiBot/.env`: `HIBOT_DB_EU`, `HIBOT_LLM_*`, `HIBOT_SERVICE_KEY`, now also `ANTHROPIC_API_KEY`). A cloud session can do code, tests and docs, not live analyses/evals. Agents never read or print `.env` (D16).
- Cloud sessions lack generated knowledge (`knowledge/schema/objects.json` etc.; sibling repos under `d:\repos` are missing), so about 65 tests fail there; this is environmental. The 3 `test_round3` calibration tests also need `objects.json` (ad-hoc SQL validation). Locally run the four `tools/*/build.py` first (commands in CLAUDE.md), then `python -m unittest discover tests`.
- **APAC DB is not available.** `run_eval.py --split dev --live` fails on SM-1777386 (APAC). Use `--keys` with the six EU dev tickets (SM-2139009, SM-2139002, SM-2126600, SM-2122480, SM-2172385, SM-2169006). `--keys` only accepts tickets from the chosen eval set; SM-1808060 is not in `eval_set.json`, run it with `python -m hibot analyze SM-1808060 --region EU --loop v2 --live --llm http`.
- Dev results are leaky (every dev ticket has a worked example in the skills): v2 scored 5/5 on dev and 45%/42% on holdout2.
- ArGPT: `chat-completion` hit its daily limit earlier; use the text transport (`HIBOT_LLM_FORMAT=argpt`, `HIBOT_LLM_FORMAT_V2=argpt`). On 429/500 storms stop and use `--resume`; never write wait loops.
- Git: branch `claude/happy-wright-d70yg4` on `ibolatkiran/hibot` (GitHub), no PR opened. Raw data (`docs/*.json`, `docs/jira_export/raw/`, `runs/`, `knowledge/**/*.json` except cards/eval) is git-ignored. Subagents: always `model: "sonnet"`; document everything in `project/*` and commit after each batch.

## Local demo processes (user's machine)
| What | Start | Notes |
|---|---|---|
| HiBot service | `python -m hibot.service --port 8077` (`--loop v1` default) | Restart to pick up code changes. |
| DbChangeTracking API | `python tools/dev/run_dbct_api.py` | Sets the safe local-run switches against shared **preprod**; never start the API against that DB without them. |
| DbChangeTracking UI | `cd d:\repos\DbChangeTrackingSystemUI; npm start` | http://localhost:3000 -> HiBot menu (Okta login) |

## Open items waiting on the user
1. Commit the DbChangeTracking changes (branch `feature/hibot-module` in both repos). Before that move `HiBot:ServiceKey` out of the API's tracked `appsettings.json` into user secrets. Do not touch the user's own `docs/CURRENT_PROJECT_STATE.md` / `TASK_22_...` changes.
2. Security notes for the team: plaintext MongoDB connection string committed in the API `appsettings.json`; SiriusApi `appsettings.json`, frontends' `translate.js` and `.env-cmdrc.json` contain secrets (never index or print them).
3. IT answers (D25, see Next work 6).
4. Policy: what company data may go to an external model provider (egress filter, KVKK/PII). The Anthropic experiment was approved by the user for testing only.
5. Later (PLAN): APAC DB access, automatic ticket intake (Jira notification e-mails), hosting the service on a server, local embeddings (D21).
