# holdout2: v1 vs v2 (final run, each loop run once, no tuning afterwards)

Set: `holdout2.json`, 40 Sirius EU tickets, silver labels, 38 reliable (high+medium). 1 ticket = 2.6 points on the reliable set.
Sources: `results_holdout2_v1.md`, `results_holdout2_v2.md`. Decision criterion (fixed BEFORE the run, 2026-10-17): v2 becomes the service default only if
(a) confidently wrong on root_cause_type is not higher than v1 AND (b) both-axes accuracy is at least 5 points above v1.

| metric (reliable labels) | v1 | v2 |
|---|---|---|
| confidently wrong, root_cause_type (conf >= 0.7) | 11% (4) | 5% (2) |
| confidently wrong, legacy label | 16% (6) | 11% (4) |
| root_cause_type accuracy | **68%** | 45% |
| resolution_action accuracy | **63%** | 47% |
| both axes correct | **58%** | 42% |
| classification exact / lenient | **58% / 68%** | 39% / 58% |
| honest unknown (all) | 15% | 18% |
| expected_findings hit rate | 95% | 94% |
| runs with errors | 0 | **4** |
| avg elapsed s / executed queries / LLM calls | **24.6** / 5.0 / 2.6 | 66.0 / 7.3 / 6.9 |

## Verdict: v1 stays the default
- (a) holds for v2 (5% vs 11%), (b) fails: v2 is 16 points BELOW v1 on both axes.
- v2 had 4 runs with errors. Worst case for v2 (all 4 would have been correct, all 4 reliable): 16 + 4 = 20/38 = 53% < 58%. So the verdict does not depend on the errors, which are still to be identified (see "Open").
- v2 is under-confident rather than careful: 27/38 tickets sit in the 0.4-0.7 band, only 5 reach 0.7+. Its low confidently-wrong rate comes with fewer committed answers (more `unknown` and `config` guesses), not a more reliable 0.7+ band (accuracy 20% legacy / 60% rct, n=5).
- v1 calibration is not good either: its 0.7+ band (n=9) is 33% legacy / 56% rct accurate. The 4 rct confidently-wrong cases are SM-2080016, SM-1957195, SM-1924609, SM-1960355.

## Per stratum, root cause (reliable): v1 vs v2
| stratum | n | v1 | v2 |
|---|---:|---:|---:|
| access_request | 4 | 100% | 100% |
| master_data_request | 6 | **100%** | 83% |
| functional_defect | 7 | **71%** | 57% |
| status_footprint | 6 | **83%** | 67% |
| status_nochange | 5 | **60%** | 0% |
| notification_failure | 4 | **50%** | 0% |
| integration_failure | 5 | **25%** | 0% |
| performance_report | 3 | 0% (honest unknown 100%) | 0% |

v2 is not better in any stratum. Both loops are weak on `integration_failure` (v1 sends 2 of 5 to `master_data` with 0.78), `status_nochange` (both axes 0% for both: the data_fix action is never reached) and `performance_report` (no query evidence; v1 says unknown, which is the honest answer).

## Silver-label artifacts (do not read as defects)
SM-2103087 and SM-2139680 are marked CW by the legacy label (data_correction vs config) in both runs; the axes match (config/data_fix). SM-1957195 is `unknown` in the silver table; both loops answer config/data_fix.

## Consequences
- D27: service default stays `--loop v1` (+ minimum-evidence guard, D26). Round 3 did not turn v2 into a default.
- holdout2 is now spent (D19): no tuning on these tickets. A new blind set is needed for the next comparison.
- Dev results (`results_r3_dev_v2.md`: rct 5/5, both axes 5/5) did not transfer to holdout2 (45% / 42%). Dev is leaky (all dev tickets have worked examples in skills), so dev numbers say nothing about generalisation.
- Highest-leverage next step stays native tool calling (D25) rather than more JSON-protocol tuning: v2 spent 2.7x the LLM calls and 2.7x the time for lower accuracy.

## The 4 v2 errors (confirmed from `runs/eval_holdout2_v2/results.jsonl`, 2026-10-17)
SM-1957195, SM-1934122, SM-2080016, SM-1833067: all `LLM transport error: LLM request failed after 3 attempts: HTTP 500` (ArGPT gateway, 19:21-19:53). These are gateway outages, not v2 answers; their reports are fallbacks (conf 0.00 / unknown) and count as wrong or unknown in the v2 numbers above. The verdict holds even if all four were correct (53% < 58%, see Verdict). v1 had no such errors, so v2's longer runs (about 7 LLM calls per ticket vs 2.6) were also more exposed to gateway failures.
