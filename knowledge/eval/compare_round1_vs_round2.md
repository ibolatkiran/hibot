# HiBot baseline vs round 1 vs round 2

Columns: baseline, round 1, round 2. All scored by the same (axis-aware) `score.py` on the `test` split (40 tickets, live ArGPT + EU prod). Axes of the baseline are DERIVED from its legacy label, so its resolution_action / both-axes numbers are pessimistic by construction; root_cause_type is the fair axis. Silver labels, reliable = high+medium (n=30). 1 ticket = 3.3 points on the reliable set; LLM variance is a few tickets.

## Headline

| metric | baseline | round 1 | round 2 |
|---|---|---|---|
| **confidently wrong, legacy label** (conf >= 0.7, reliable) | 23% | 3% | 3% |
| **confidently wrong, root_cause_type** | 17% | 7% | 7% |
| classification exact (legacy), reliable | 40% | 53% | 53% |
| classification lenient (legacy), reliable | 50% | 53% | 53% |
| root_cause_type accuracy, reliable | 50% | 57% | 53% |
| resolution_action accuracy, reliable | 47% | 53% | 63% |
| both axes correct, reliable | 37% | 47% | 50% |
| root_cause_type accuracy, all labels | 45% | 52% | 50% |
| honest unknown (unknown, conf < 0.5) | 35% | 38% | 38% |
| expected_findings hit rate | 92% | 90% | 91% |
| work-order resolution | 95% | 95% | 95% |
| tenant resolution | 100% | 100% | 100% |
| card match rate | 70% | 70% | 70% |
| avg elapsed s | 23.684 | 24.048 | 26.814 |
| avg queries prepared | 3.775 | 3.95 | 4.25 |
| avg queries executed | 3.725 | 3.925 | 4.225 |
| avg LLM calls | 3.025 | 3.125 | 3.1 |

## Calibration (reliable labels): accuracy (n) per confidence bucket

| bucket | legacy: baseline | legacy: round 1 | legacy: round 2 | root_cause_type: baseline | root_cause_type: round 1 | root_cause_type: round 2 |
|---|---|---|---|---|---|---|
| 0.0-0.4 | 0% (10) | 0% (8) | 0% (4) | 10% (10) | 25% (8) | 0% (4) |
| 0.4-0.7 | 83% (6) | 67% (15) | 44% (16) | 83% (6) | 67% (15) | 50% (16) |
| 0.7-1.0 | 50% (14) | 86% (7) | 90% (10) | 64% (14) | 71% (7) | 80% (10) |

## Per stratum (reliable labels; baseline -> round 1 -> round 2)

| stratum | n | exact | root cause | both axes | conf. wrong legacy / rct | findings | avg q |
|---|---:|---|---|---|---|---|---|
| access_request | 4 | 25% -> 100% -> 100% | 25% -> 100% -> 100% | 25% -> 100% -> 100% | 0 -> 0 -> 0 / 0 -> 0 -> 0 | 50% -> 50% -> 50% | 0.0 -> 0.0 -> 0.0 |
| functional_defect | 8 | 60% -> 60% -> 60% | 60% -> 60% -> 60% | 60% -> 60% -> 60% | 1 -> 0 -> 0 / 1 -> 0 -> 0 | 100% -> 100% -> 100% | 2.5 -> 2.25 -> 2.875 |
| integration_failure | 6 | 25% -> 50% -> 0% | 25% -> 50% -> 0% | 25% -> 25% -> 0% | 0 -> 0 -> 0 / 0 -> 0 -> 0 | 82% -> 82% -> 82% | 4.0 -> 5.0 -> 5.5 |
| master_data_request | 6 | 80% -> 40% -> 80% | 80% -> 40% -> 80% | 80% -> 40% -> 80% | 0 -> 0 -> 0 / 0 -> 0 -> 0 | 100% -> 100% -> 100% | 1.667 -> 1.833 -> 1.667 |
| notification_failure | 3 | 0% -> 0% -> 0% | 0% -> 0% -> 0% | 0% -> 0% -> 0% | 1 -> 0 -> 0 / 1 -> 0 -> 0 | 100% -> 100% -> 100% | 5.333 -> 6.333 -> 6.667 |
| performance_report | 3 | 0% -> 0% -> 0% | 0% -> 0% -> 0% | 0% -> 0% -> 0% | 0 -> 0 -> 0 / 0 -> 0 -> 0 | 100% -> 50% -> 75% | 5.0 -> 5.0 -> 5.0 |
| status_footprint | 5 | 40% -> 80% -> 80% | 100% -> 80% -> 80% | 40% -> 80% -> 80% | 3 -> 0 -> 0 / 0 -> 0 -> 0 | 100% -> 100% -> 100% | 6.6 -> 6.6 -> 6.6 |
| status_nochange | 5 | 25% -> 25% -> 25% | 25% -> 50% -> 25% | 0% -> 0% -> 0% | 2 -> 1 -> 1 / 3 -> 2 -> 2 | 100% -> 100% -> 100% | 6.6 -> 6.4 -> 7.2 |

## Per ticket (root_cause_type, reliable labels; only tickets where an answer or its correctness changed between the last two columns)

| key | stratum | expected rct | baseline | round 1 | round 2 |
|---|---|---|---|---|---|
| SM-1967154 | status_footprint | config | config 0.86 ok | config 0.80 ok | config 0.78 ok |
| SM-2122464 | status_nochange | unknown | user_error 0.86 **CW** | unknown 0.00 ok | user_error 0.60 |
| SM-2137438 | status_nochange | unknown | unknown 0.38 ok | unknown 0.38 ok | unknown 0.45 ok |
| SM-2143981 | functional_defect | master_data | master_data 0.70 ok | master_data 0.60 ok | master_data 0.70 ok |
| SM-2112749 | functional_defect | master_data | unknown 0.30 | unknown 0.30 | unknown 0.35 |
| SM-2128512 | functional_defect | user_error | access 0.78 **CW** | unknown 0.30 | unknown 0.45 |
| SM-2070960 | functional_defect | master_data | master_data 0.78 ok | master_data 0.55 ok | master_data 0.78 ok |
| SM-2092603 | functional_defect | user_error | user_error 0.55 ok | user_error 0.45 ok | user_error 0.50 ok |
| SM-2144810 | integration_failure | integration | integration 0.45 ok | integration 0.55 ok | unknown 0.45 |
| SM-2122065 | integration_failure | integration | unknown 0.35 | integration 0.65 ok | unknown 0.50 |
| SM-2133110 | master_data_request | master_data | master_data 0.62 ok | master_data 0.55 ok | master_data 0.60 ok |
| SM-2092729 | master_data_request | master_data | master_data 0.40 ok | unknown 0.39 | master_data 0.60 ok |
| SM-2102064 | master_data_request | master_data | master_data 0.70 ok | user_error 0.50 | master_data 0.70 ok |
| SM-2123794 | master_data_request | master_data | master_data 0.86 ok | master_data 0.78 ok | master_data 0.70 ok |
| SM-2070909 | master_data_request | master_data | user_error 0.55 | user_error 0.55 | unknown 0.40 |
| SM-2091967 | notification_failure | user_error | unknown 0.38 | unknown 0.45 | unknown 0.48 |
| SM-2094146 | performance_report | user_error | unknown 0.30 | unknown 0.38 | unknown 0.35 |
