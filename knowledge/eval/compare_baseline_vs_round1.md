# HiBot baseline vs round 1 (test split, 40 tickets, live ArGPT + EU prod)

Left = `baseline_20261016`, right = `round1_20261016`. Both scored by the same `score.py`: axes of the baseline are DERIVED from its legacy label (`label_axes_rules.md`), so its resolution_action / both-axes numbers are pessimistic by construction (a legacy `config` cannot say `data_fix`); root_cause_type is the fair axis. Silver labels, reliable = high+medium confidence (n=30).

## Headline

| metric | baseline | round 1 |
|---|---|---|
| **confidently wrong, legacy label** (conf >= 0.7, reliable) | 23% | 3% |
| **confidently wrong, root_cause_type** | 17% | 7% |
| classification exact (legacy), reliable | 40% | 53% |
| classification lenient (legacy), reliable | 50% | 53% |
| root_cause_type accuracy, reliable | 50% | 57% |
| resolution_action accuracy, reliable | 47% | 53% |
| both axes correct, reliable | 37% | 47% |
| root_cause_type accuracy, all labels | 45% | 52% |
| honest unknown (unknown, conf < 0.5) | 35% | 38% |
| expected_findings hit rate | 92% | 90% |
| work-order resolution | 95% | 95% |
| tenant resolution | 100% | 100% |
| card match rate | 70% | 70% |
| avg elapsed s | 23.684 | 24.048 |
| avg queries prepared | 3.775 | 3.95 |
| avg queries executed | 3.725 | 3.925 |
| avg LLM calls | 3.025 | 3.125 |

## Calibration (reliable labels): accuracy per confidence bucket

| bucket | legacy label: baseline (n) | legacy label: round 1 (n) | root_cause_type: baseline (n) | root_cause_type: round 1 (n) |
|---|---|---|---|---|
| 0.0-0.4 | 0% (10) | 0% (8) | 10% (10) | 25% (8) |
| 0.4-0.7 | 83% (6) | 67% (15) | 83% (6) | 67% (15) |
| 0.7-1.0 | 50% (14) | 86% (7) | 64% (14) | 71% (7) |

## Per stratum (reliable labels)

| stratum | n | exact: base -> r1 | lenient: base -> r1 | root cause: base -> r1 | both axes: base -> r1 | conf. wrong legacy: base -> r1 | conf. wrong rct: base -> r1 | findings: base -> r1 | avg q: base -> r1 |
|---|---:|---|---|---|---|---|---|---|---|
| access_request | 4 | 25% -> 100% | 25% -> 100% | 25% -> 100% | 25% -> 100% | 0 -> 0 | 0 -> 0 | 50% -> 50% | 0.0 -> 0.0 |
| functional_defect | 8 | 60% -> 60% | 60% -> 60% | 60% -> 60% | 60% -> 60% | 1 -> 0 | 1 -> 0 | 100% -> 100% | 2.5 -> 2.25 |
| integration_failure | 6 | 25% -> 50% | 25% -> 50% | 25% -> 50% | 25% -> 25% | 0 -> 0 | 0 -> 0 | 82% -> 82% | 4.0 -> 5.0 |
| master_data_request | 6 | 80% -> 40% | 80% -> 40% | 80% -> 40% | 80% -> 40% | 0 -> 0 | 0 -> 0 | 100% -> 100% | 1.667 -> 1.833 |
| notification_failure | 3 | 0% -> 0% | 0% -> 0% | 0% -> 0% | 0% -> 0% | 1 -> 0 | 1 -> 0 | 100% -> 100% | 5.333 -> 6.333 |
| performance_report | 3 | 0% -> 0% | 0% -> 0% | 0% -> 0% | 0% -> 0% | 0 -> 0 | 0 -> 0 | 100% -> 50% | 5.0 -> 5.0 |
| status_footprint | 5 | 40% -> 80% | 100% -> 80% | 100% -> 80% | 40% -> 80% | 3 -> 0 | 0 -> 0 | 100% -> 100% | 6.6 -> 6.6 |
| status_nochange | 5 | 25% -> 25% | 25% -> 25% | 25% -> 50% | 0% -> 0% | 2 -> 1 | 3 -> 2 | 100% -> 100% | 6.6 -> 6.4 |

## Per ticket changes (root_cause_type; reliable labels only, differences only)

| key | stratum | expected rct | baseline (conf) | round 1 (conf, raw) |
|---|---|---|---|---|
| SM-2143299 | status_footprint | config | config 0.86 | config 0.78 (0.78) |
| SM-2132761 | status_footprint | config | config 0.90 | config 0.78 (0.78) |
| SM-2100037 | status_footprint | data_issue | data_issue 0.70 | user_error 0.60 (0.60) |
| SM-1967154 | status_footprint | config | config 0.86 | config 0.80 (0.80) |
| SM-2070070 | status_footprint | user_error | user_error 0.90 | user_error 0.85 (0.85) |
| SM-2122464 | status_nochange | unknown | user_error 0.86 **CW** | unknown 0.00 (0.00) |
| SM-2147977 | status_nochange | unknown | data_issue 0.82 **CW** | config 0.78 (0.78) **CW** |
| SM-2143981 | functional_defect | master_data | master_data 0.70 | master_data 0.60 (0.60) |
| SM-2128512 | functional_defect | user_error | access 0.78 **CW** | unknown 0.30 (0.30) |
| SM-2070960 | functional_defect | master_data | master_data 0.78 | master_data 0.55 (0.55) |
| SM-2092603 | functional_defect | user_error | user_error 0.55 | user_error 0.45 (0.45) |
| SM-2146596 | integration_failure | integration | unknown 0.25 | unknown 0.35 (0.35) |
| SM-2144810 | integration_failure | integration | integration 0.45 | integration 0.55 (0.55) |
| SM-2122065 | integration_failure | integration | unknown 0.35 | integration 0.65 (0.65) |
| SM-2100070 | integration_failure | data_issue | unknown 0.35 | unknown 0.38 (0.38) |
| SM-2133110 | master_data_request | master_data | master_data 0.62 | master_data 0.55 (0.55) |
| SM-2092729 | master_data_request | master_data | master_data 0.40 | unknown 0.39 (0.39) |
| SM-2102064 | master_data_request | master_data | master_data 0.70 | user_error 0.50 (0.50) |
| SM-2123794 | master_data_request | master_data | master_data 0.86 | master_data 0.78 (0.78) |
| SM-2143363 | access_request | access | access 0.55 | access 0.50 (0.60) |
| SM-2122261 | access_request | access | unknown 0.25 | access 0.50 (0.50) |
| SM-2132602 | access_request | access | unknown 0.25 | access 0.50 (0.50) |
| SM-2142046 | access_request | access | unknown 0.30 | access 0.50 (0.50) |
| SM-2001523 | notification_failure | config | user_error 0.86 **CW** | user_error 0.55 (0.55) |
| SM-2091967 | notification_failure | user_error | unknown 0.38 | unknown 0.45 (0.45) |
| SM-2094146 | performance_report | user_error | unknown 0.30 | unknown 0.38 (0.38) |
