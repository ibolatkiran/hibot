# HiBot baseline vs round 1 vs round 2 vs v2 (test split)

Columns: baseline, round1, round2, v2. All scored by the same (axis-aware) `score.py` on the `test` split (40 tickets, live ArGPT + EU prod). Axes of the baseline are DERIVED from its legacy label, so its resolution_action / both-axes numbers are pessimistic by construction; root_cause_type is the fair axis. Silver labels, reliable = high+medium (n=30). 1 ticket = 3.3 points on the reliable set; LLM variance is a few tickets.

## Headline

| metric | baseline | round1 | round2 | v2 |
|---|---|---|---|---|
| **confidently wrong, legacy label** (conf >= 0.7, reliable) | 23% | 3% | 3% | 23% |
| **confidently wrong, root_cause_type** | 17% | 7% | 7% | 17% |
| classification exact (legacy), reliable | 40% | 53% | 53% | 50% |
| classification lenient (legacy), reliable | 50% | 53% | 53% | 60% |
| root_cause_type accuracy, reliable | 50% | 57% | 53% | 60% |
| resolution_action accuracy, reliable | 47% | 53% | 63% | 63% |
| both axes correct, reliable | 37% | 47% | 50% | 53% |
| root_cause_type accuracy, all labels | 45% | 52% | 50% | 55% |
| honest unknown (unknown, conf < 0.5) | 35% | 38% | 38% | 35% |
| expected_findings hit rate | 92% | 90% | 91% | 91% |
| work-order resolution | 95% | 95% | 95% | 95% |
| tenant resolution | 100% | 100% | 100% | 100% |
| card match rate | 70% | 70% | 70% | 62% |
| avg elapsed s | 23.684 | 24.048 | 26.814 | 30.818 |
| avg queries prepared | 3.775 | 3.95 | 4.25 | 4.15 |
| avg queries executed | 3.725 | 3.925 | 4.225 | 4.05 |
| avg LLM calls | 3.025 | 3.125 | 3.1 | 3.525 |
| avg steps (LLM turns) | n/a | n/a | n/a | 3.525 |
| avg tool calls (v2 only) | n/a | n/a | n/a | 2.475 |

## Calibration (reliable labels): accuracy (n) per confidence bucket

| bucket | legacy: baseline | legacy: round1 | legacy: round2 | legacy: v2 | root_cause_type: baseline | root_cause_type: round1 | root_cause_type: round2 | root_cause_type: v2 |
|---|---|---|---|---|---|---|---|---|
| 0.0-0.4 | 0% (10) | 0% (8) | 0% (4) | 0% (5) | 10% (10) | 25% (8) | 0% (4) | 0% (5) |
| 0.4-0.7 | 83% (6) | 67% (15) | 44% (16) | 62% (8) | 83% (6) | 67% (15) | 50% (16) | 75% (8) |
| 0.7-1.0 | 50% (14) | 86% (7) | 90% (10) | 59% (17) | 64% (14) | 71% (7) | 80% (10) | 71% (17) |

## Per stratum (reliable labels; baseline -> round1 -> round2 -> v2)

| stratum | n | exact | root cause | both axes | conf. wrong legacy / rct | findings | avg q |
|---|---:|---|---|---|---|---|---|
| access_request | 4 | 25% -> 100% -> 100% -> 100% | 25% -> 100% -> 100% -> 100% | 25% -> 100% -> 100% -> 100% | 0 -> 0 -> 0 -> 0 / 0 -> 0 -> 0 -> 0 | 50% -> 50% -> 50% -> 50% | 0.0 -> 0.0 -> 0.0 -> 0.0 |
| functional_defect | 8 | 60% -> 60% -> 60% -> 40% | 60% -> 60% -> 60% -> 40% | 60% -> 60% -> 60% -> 40% | 1 -> 0 -> 0 -> 0 / 1 -> 0 -> 0 -> 0 | 100% -> 100% -> 100% -> 100% | 2.5 -> 2.25 -> 2.875 -> 2.5 |
| integration_failure | 6 | 25% -> 50% -> 0% -> 25% | 25% -> 50% -> 0% -> 25% | 25% -> 25% -> 0% -> 0% | 0 -> 0 -> 0 -> 0 / 0 -> 0 -> 0 -> 0 | 82% -> 82% -> 82% -> 82% | 4.0 -> 5.0 -> 5.5 -> 4.833 |
| master_data_request | 6 | 80% -> 40% -> 80% -> 100% | 80% -> 40% -> 80% -> 100% | 80% -> 40% -> 80% -> 100% | 0 -> 0 -> 0 -> 0 / 0 -> 0 -> 0 -> 0 | 100% -> 100% -> 100% -> 100% | 1.667 -> 1.833 -> 1.667 -> 1.833 |
| notification_failure | 3 | 0% -> 0% -> 0% -> 0% | 0% -> 0% -> 0% -> 0% | 0% -> 0% -> 0% -> 0% | 1 -> 0 -> 0 -> 1 / 1 -> 0 -> 0 -> 1 | 100% -> 100% -> 100% -> 100% | 5.333 -> 6.333 -> 6.667 -> 7.333 |
| performance_report | 3 | 0% -> 0% -> 0% -> 100% | 0% -> 0% -> 0% -> 100% | 0% -> 0% -> 0% -> 100% | 0 -> 0 -> 0 -> 0 / 0 -> 0 -> 0 -> 0 | 100% -> 50% -> 75% -> 75% | 5.0 -> 5.0 -> 5.0 -> 5.667 |
| status_footprint | 5 | 40% -> 80% -> 80% -> 40% | 100% -> 80% -> 80% -> 80% | 40% -> 80% -> 80% -> 80% | 3 -> 0 -> 0 -> 3 / 0 -> 0 -> 0 -> 1 | 100% -> 100% -> 100% -> 100% | 6.6 -> 6.6 -> 6.6 -> 6.6 |
| status_nochange | 5 | 25% -> 25% -> 25% -> 0% | 25% -> 50% -> 25% -> 25% | 0% -> 0% -> 0% -> 0% | 2 -> 1 -> 1 -> 3 / 3 -> 2 -> 2 -> 3 | 100% -> 100% -> 100% -> 100% | 6.6 -> 6.4 -> 7.2 -> 6.8 |

## Per ticket (root_cause_type, reliable labels; only tickets where an answer or its correctness changed between the last two columns)

| key | stratum | expected rct | baseline | round1 | round2 | v2 |
|---|---|---|---|---|---|---|
| SM-2143299 | status_footprint | config | config 0.86 ok | config 0.78 ok | config 0.78 ok | user_error 0.70 **CW** |
| SM-2132761 | status_footprint | config | config 0.90 ok | config 0.78 ok | config 0.78 ok | config 0.86 ok |
| SM-2100037 | status_footprint | data_issue | data_issue 0.70 ok | user_error 0.60 | user_error 0.60 | data_issue 0.80 ok |
| SM-1967154 | status_footprint | config | config 0.86 ok | config 0.80 ok | config 0.78 ok | config 0.86 ok |
| SM-2070070 | status_footprint | user_error | user_error 0.90 ok | user_error 0.85 ok | user_error 0.85 ok | user_error 0.86 ok |
| SM-2122464 | status_nochange | unknown | user_error 0.86 **CW** | unknown 0.00 ok | user_error 0.60 | user_error 0.70 **CW** |
| SM-2147977 | status_nochange | unknown | data_issue 0.82 **CW** | config 0.78 **CW** | config 0.78 **CW** | config 0.86 **CW** |
| SM-2143981 | functional_defect | master_data | master_data 0.70 ok | master_data 0.60 ok | master_data 0.70 ok | master_data 0.78 ok |
| SM-2112749 | functional_defect | master_data | unknown 0.30 | unknown 0.30 | unknown 0.35 | unknown 0.40 |
| SM-2128512 | functional_defect | user_error | access 0.78 **CW** | unknown 0.30 | unknown 0.45 | unknown 0.35 |
| SM-2070960 | functional_defect | master_data | master_data 0.78 ok | master_data 0.55 ok | master_data 0.78 ok | master_data 0.70 ok |
| SM-2092603 | functional_defect | user_error | user_error 0.55 ok | user_error 0.45 ok | user_error 0.50 ok | unknown 0.35 |
| SM-2144810 | integration_failure | integration | integration 0.45 ok | integration 0.55 ok | unknown 0.45 | unknown 0.30 |
| SM-2122065 | integration_failure | integration | unknown 0.35 | integration 0.65 ok | unknown 0.50 | integration 0.80 ok |
| SM-2100070 | integration_failure | data_issue | unknown 0.35 | unknown 0.38 | unknown 0.38 | unknown 0.30 |
| SM-2133110 | master_data_request | master_data | master_data 0.62 ok | master_data 0.55 ok | master_data 0.60 ok | master_data 0.80 ok |
| SM-2092729 | master_data_request | master_data | master_data 0.40 ok | unknown 0.39 | master_data 0.60 ok | master_data 0.78 ok |
| SM-2102064 | master_data_request | master_data | master_data 0.70 ok | user_error 0.50 | master_data 0.70 ok | master_data 0.78 ok |
| SM-2123794 | master_data_request | master_data | master_data 0.86 ok | master_data 0.78 ok | master_data 0.70 ok | master_data 0.80 ok |
| SM-2070909 | master_data_request | master_data | user_error 0.55 | user_error 0.55 | unknown 0.40 | master_data 0.72 ok |
| SM-2001523 | notification_failure | config | user_error 0.86 **CW** | user_error 0.55 | user_error 0.55 | user_error 0.78 **CW** |
| SM-2094146 | performance_report | user_error | unknown 0.30 | unknown 0.38 | unknown 0.35 | user_error 0.55 ok |

## Same tickets as the Claude Haiku run (5 tickets: SM-1967154, SM-2001523, SM-2010200, SM-2061644, SM-2070070)

| metric | Claude Haiku | baseline | round1 | round2 | v2 |
|---|---|---|---|---|---|
| root_cause_type correct (all labels) | 2/5 | 3/5 | 3/5 | 3/5 | 3/5 |
| both axes correct | 0/5 | 2/5 | 3/5 | 2/5 | 2/5 |
| legacy label exact | 2/5 | 2/5 | 3/5 | 3/5 | 2/5 |
| confidently wrong (conf >= 0.7), legacy / root_cause_type | 2 / 2 | 3 / 2 | 1 / 1 | 1 / 1 | 3 / 2 |
| honest unknown | 1/5 | 1/5 | 1/5 | 1/5 | 1/5 |
| avg elapsed s | 74.96 | 25.994 | 26.152 | 25.668 | 24.084 |
| avg LLM calls | 9.6 | 2.6 | 3.0 | 2.2 | 2.0 |
| avg tool calls (v2 only) | n/a | n/a | n/a | n/a | 1.0 |

| key | expected rct | Claude Haiku | baseline | round1 | round2 | v2 |
|---|---|---|---|---|---|---|
| SM-1967154 | config | config 0.80 ok | config 0.86 ok | config 0.80 ok | config 0.78 ok | config 0.86 ok |
| SM-2001523 | config | integration 0.60 | user_error 0.86 **CW** | user_error 0.55 | user_error 0.55 | user_error 0.78 **CW** |
| SM-2010200 | unknown | data_issue 0.80 **CW** | user_error 0.85 **CW** | user_error 0.85 **CW** | user_error 0.85 **CW** | user_error 0.85 **CW** |
| SM-2061644 | unknown | unknown 0.20 ok | unknown 0.48 ok | unknown 0.48 ok | unknown 0.40 ok | unknown 0.40 ok |
| SM-2070070 | user_error | config 0.80 **CW** | user_error 0.90 ok | user_error 0.85 ok | user_error 0.85 ok | user_error 0.86 ok |
