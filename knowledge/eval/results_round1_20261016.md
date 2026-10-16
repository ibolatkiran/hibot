# HiBot evaluation results: round1_20261016

Split: **test**, tickets scored: **40** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 30).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **3%** (1 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 38% |
| classification exact, reliable labels | 53% |
| classification lenient, reliable labels | 53% |
| classification exact / lenient, all labels | 50% / 52% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **7%** (2 tickets) |
| root_cause_type accuracy, reliable / all labels | 57% / 52% |
| resolution_action accuracy, reliable labels | 53% |
| both axes correct, reliable / all labels | 47% / 45% |
| work-order resolution (n=21) | 95% |
| tenant resolution (n=20) | 100% |
| expected_findings hit rate (78 checks) | 90% |
| card match rate | 70% |
| avg elapsed s / queries (executed) / LLM calls | 24.048 / 3.95 (3.925) / 3.125 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 8 | 0% | 0.31 |
| 0.4-0.7 | 15 | 67% | 0.533 |
| 0.7-1.0 | 7 | 86% | 0.803 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 8 | 25% | 0.31 |
| 0.4-0.7 | 15 | 67% | 0.533 |
| 0.7-1.0 | 7 | 71% | 0.803 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| access_request | 4 | 100% | 100% | 100% | 100% | n/a | 50% | 0 / 0 | 0% | 9.115 | 0.0 |
| functional_defect | 8 | 60% | 60% | 60% | 60% | 100% | 100% | 0 / 0 | 50% | 25.762 | 2.25 |
| integration_failure | 6 | 50% | 50% | 50% | 25% | 100% | 82% | 0 / 0 | 50% | 27.927 | 5.0 |
| master_data_request | 6 | 40% | 40% | 40% | 40% | 100% | 100% | 0 / 0 | 33% | 19.933 | 1.833 |
| notification_failure | 3 | 0% | 0% | 0% | 0% | 100% | 100% | 0 / 0 | 33% | 27.813 | 6.333 |
| performance_report | 3 | 0% | 0% | 0% | 0% | 100% | 50% | 0 / 0 | 100% | 41.703 | 5.0 |
| status_footprint | 5 | 80% | 80% | 80% | 80% | 100% | 100% | 0 / 0 | 0% | 19.242 | 6.6 |
| status_nochange | 5 | 25% | 25% | 50% | 0% | 100% | 100% | 1 / 2 | 40% | 25.49 | 6.4 |

## Confusion (reliable labels; expected -> got)

- access: access=4
- config: user_error=1
- data_correction: data_correction=4, unknown=3, user_error=2
- integration: integration=2, unknown=1
- master_data: master_data=4, unknown=2, user_error=2
- user_error: unknown=3, user_error=2

## Confusion on root_cause_type (reliable labels; expected -> got)

- access: access=4
- config: config=3, user_error=1
- data_issue: unknown=1, user_error=1
- integration: integration=2, unknown=1
- master_data: master_data=4, unknown=2, user_error=2
- unknown: config=1, unknown=2, user_error=1
- user_error: unknown=3, user_error=2

## Per ticket

| key | stratum | label | expected | got | axes expected -> got | conf (raw) | findings | tenant | s | q |
|---|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-2143299 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 19.18 | 7 |
| SM-2132761 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 18.0 | 7 |
| SM-2100037 | status_footprint | medium | data_correction | user_error | data_issue/data_fix -> user_error/explain_to_user | 0.60 (0.60) | 2/2 | ok | 16.52 | 6 |
| SM-1967154 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.80 (0.80) | 3/3 | ok | 18.98 | 7 |
| SM-2070070 | status_footprint | high | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.85 (0.85) | 3/3 | ok | 23.53 | 6 |
| SM-2142402 | status_nochange | low | data_correction | user_error | unknown/data_fix -> user_error/explain_to_user | 0.55 (0.55) | 2/2 | ok | 17.36 | 5 |
| SM-2122464 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.00 (0.00) | 2/2 | ok | 18.31 | 5 |
| SM-2010200 | status_nochange | medium | data_correction | user_error **CW** | unknown/data_fix -> user_error/explain_to_user **CW-rct** | 0.85 (0.85) | 2/2 | ok | 20.39 | 8 |
| SM-2147977 | status_nochange | medium | data_correction | data_correction | unknown/data_fix -> config/data_fix **CW-rct** | 0.78 (0.78) | 2/2 | ok | 22.29 | 8 |
| SM-2137438 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.38 (0.38) | 2/2 | ok | 49.1 | 6 |
| SM-2143981 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.60 (0.60) | 3/3 | ok | 37.61 | 2 |
| SM-2112749 | functional_defect | medium | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.30 (0.30) | 1/1 | - | 23.64 | 2 |
| SM-2128512 | functional_defect | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.30 (0.30) | 1/1 | - | 21.75 | 0 |
| SM-2070960 | functional_defect | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.55 (0.55) | 2/2 | ok | 14.31 | 2 |
| SM-2092603 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.45 (0.45) | 1/1 | - | 8.84 | 0 |
| SM-2104030 | functional_defect | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.35 (0.35) | 1/1 | - | 23.57 | 0 |
| SM-2088913 | functional_defect | low | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.35 (0.35) | 2/2 | ok | 35.63 | 7 |
| SM-2082092 | functional_defect | low | data_correction | config | data_issue/data_fix -> config/config_change | 0.62 (0.62) | 2/2 | ok | 40.75 | 5 |
| SM-2146596 | integration_failure | medium | integration | unknown | integration/unknown -> unknown/unknown | 0.35 (0.35) | 1/2 | - | 29.33 | 3 |
| SM-2144810 | integration_failure | medium | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.55 (0.55) | 1/2 | - | 15.56 | 3 |
| SM-2122065 | integration_failure | medium | integration | integration | integration/unknown -> integration/escalate_other_system | 0.65 (0.65) | 3/3 | ok | 22.81 | 8 |
| SM-2118792 | integration_failure | low | integration | unknown | integration/escalate_other_system -> unknown/unknown | 0.39 (0.39) | 1/1 | - | 36.4 | 3 |
| SM-2100070 | integration_failure | medium | data_correction | unknown | data_issue/data_fix -> unknown/escalate_other_system | 0.38 (0.38) | 1/1 | - | 35.39 | 3 |
| SM-2120545 | integration_failure | low | data_correction | master_data | data_issue/data_fix -> master_data/master_data_change | 0.55 (0.55) | 2/2 | ok | 28.07 | 10 |
| SM-2133110 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.55 (0.55) | 2/2 | - | 9.91 | 2 |
| SM-2092729 | master_data_request | high | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.39 (0.39) | 2/2 | - | 18.2 | 2 |
| SM-2102064 | master_data_request | high | master_data | user_error | master_data/master_data_change -> user_error/explain_to_user | 0.50 (0.50) | 2/2 | - | 8.6 | 2 |
| SM-2123794 | master_data_request | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.78 (0.78) | 2/2 | ok | 13.64 | 2 |
| SM-2070909 | master_data_request | medium | master_data | user_error | master_data/master_data_change -> user_error/explain_to_user | 0.55 (0.55) | 1/1 | - | 30.44 | 3 |
| SM-2135453 | master_data_request | low | config | unknown | config/config_change -> unknown/unknown | 0.35 (0.35) | 1/1 | - | 38.81 | 0 |
| SM-2143363 | access_request | high | access | access | access/grant_access -> access/grant_access | 0.50 (0.60) | 1/2 | - | 11.13 | 0 |
| SM-2122261 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 9.16 | 0 |
| SM-2132602 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 9.15 | 0 |
| SM-2142046 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 7.02 | 0 |
| SM-2001523 | notification_failure | high | config | user_error | config/config_change -> user_error/explain_to_user | 0.55 (0.55) | 3/3 | ok | 23.97 | 9 |
| SM-2091967 | notification_failure | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.45 (0.45) | 2/2 | - | 25.0 | 2 |
| SM-2127733 | notification_failure | low | config | config | config/config_change -> config/config_change | 0.55 (0.55) | 3/3 | ok | 34.47 | 8 |
| SM-2147237 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.48 (0.48) | 1/1 | ok | 47.22 | 7 |
| SM-2094146 | performance_report | high | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.38 (0.38) | 1/2 | - | 34.0 | 4 |
| SM-2061644 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.48 (0.48) | 0/1 | - | 43.89 | 4 |
