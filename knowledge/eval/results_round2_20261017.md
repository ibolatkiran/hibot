# HiBot evaluation results: round2_20261017

Split: **test**, tickets scored: **40** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 30).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **3%** (1 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 38% |
| classification exact, reliable labels | 53% |
| classification lenient, reliable labels | 53% |
| classification exact / lenient, all labels | 48% / 52% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **7%** (2 tickets) |
| root_cause_type accuracy, reliable / all labels | 53% / 50% |
| resolution_action accuracy, reliable labels | 63% |
| both axes correct, reliable / all labels | 50% / 42% |
| work-order resolution (n=21) | 95% |
| tenant resolution (n=20) | 100% |
| expected_findings hit rate (78 checks) | 91% |
| card match rate | 70% |
| avg elapsed s / queries (executed) / LLM calls | 26.814 / 4.25 (4.225) / 3.1 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 4 | 0% | 0.357 |
| 0.4-0.7 | 16 | 44% | 0.511 |
| 0.7-1.0 | 10 | 90% | 0.77 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 4 | 0% | 0.357 |
| 0.4-0.7 | 16 | 50% | 0.511 |
| 0.7-1.0 | 10 | 80% | 0.77 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| access_request | 4 | 100% | 100% | 100% | 100% | n/a | 50% | 0 / 0 | 0% | 9.973 | 0.0 |
| functional_defect | 8 | 60% | 60% | 60% | 60% | 100% | 100% | 0 / 0 | 50% | 27.863 | 2.875 |
| integration_failure | 6 | 0% | 0% | 0% | 0% | 100% | 82% | 0 / 0 | 50% | 38.208 | 5.5 |
| master_data_request | 6 | 80% | 80% | 80% | 80% | 100% | 100% | 0 / 0 | 33% | 20.707 | 1.667 |
| notification_failure | 3 | 0% | 0% | 0% | 0% | 100% | 100% | 0 / 0 | 33% | 30.517 | 6.667 |
| performance_report | 3 | 0% | 0% | 0% | 0% | 100% | 75% | 0 / 0 | 100% | 34.22 | 5.0 |
| status_footprint | 5 | 80% | 80% | 80% | 80% | 100% | 100% | 0 / 0 | 0% | 20.324 | 6.6 |
| status_nochange | 5 | 25% | 25% | 25% | 0% | 100% | 100% | 1 / 2 | 40% | 32.094 | 7.2 |

## Confusion (reliable labels; expected -> got)

- access: access=4
- config: user_error=1
- data_correction: data_correction=4, unknown=2, user_error=3
- integration: unknown=3
- master_data: master_data=6, unknown=2
- user_error: unknown=3, user_error=2

## Confusion on root_cause_type (reliable labels; expected -> got)

- access: access=4
- config: config=3, user_error=1
- data_issue: unknown=1, user_error=1
- integration: unknown=3
- master_data: master_data=6, unknown=2
- unknown: config=1, unknown=1, user_error=2
- user_error: unknown=3, user_error=2

## Per ticket

| key | stratum | label | expected | got | axes expected -> got | conf (raw) | findings | tenant | s | q |
|---|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-2143299 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 21.62 | 7 |
| SM-2132761 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 20.82 | 7 |
| SM-2100037 | status_footprint | medium | data_correction | user_error | data_issue/data_fix -> user_error/explain_to_user | 0.60 (0.60) | 2/2 | ok | 18.13 | 6 |
| SM-1967154 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 18.62 | 7 |
| SM-2070070 | status_footprint | high | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.85 (0.85) | 3/3 | ok | 22.43 | 6 |
| SM-2142402 | status_nochange | low | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.38 (0.38) | 2/2 | ok | 56.24 | 8 |
| SM-2122464 | status_nochange | medium | data_correction | user_error | unknown/data_fix -> user_error/explain_to_user | 0.60 (0.60) | 2/2 | ok | 16.11 | 5 |
| SM-2010200 | status_nochange | medium | data_correction | user_error **CW** | unknown/data_fix -> user_error/explain_to_user **CW-rct** | 0.85 (0.85) | 2/2 | ok | 24.59 | 8 |
| SM-2147977 | status_nochange | medium | data_correction | data_correction | unknown/data_fix -> config/data_fix **CW-rct** | 0.78 (0.78) | 2/2 | ok | 23.7 | 8 |
| SM-2137438 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.45 (0.45) | 2/2 | ok | 39.83 | 7 |
| SM-2143981 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.70 (0.70) | 3/3 | ok | 15.83 | 2 |
| SM-2112749 | functional_defect | medium | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.35 (0.35) | 1/1 | - | 31.22 | 2 |
| SM-2128512 | functional_defect | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.45 (0.45) | 1/1 | - | 36.93 | 3 |
| SM-2070960 | functional_defect | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.78 (0.78) | 2/2 | ok | 23.18 | 4 |
| SM-2092603 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.50 (0.50) | 1/1 | - | 16.39 | 0 |
| SM-2104030 | functional_defect | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.35 (0.35) | 1/1 | - | 32.6 | 0 |
| SM-2088913 | functional_defect | low | master_data | user_error | master_data/master_data_change -> user_error/explain_to_user | 0.50 (0.50) | 2/2 | ok | 25.3 | 8 |
| SM-2082092 | functional_defect | low | data_correction | unknown | data_issue/data_fix -> unknown/unknown | 0.45 (0.45) | 2/2 | ok | 41.45 | 4 |
| SM-2146596 | integration_failure | medium | integration | unknown | integration/unknown -> unknown/unknown | 0.35 (0.35) | 1/2 | - | 28.48 | 3 |
| SM-2144810 | integration_failure | medium | integration | unknown | integration/escalate_other_system -> unknown/escalate_other_system | 0.45 (0.45) | 1/2 | - | 45.01 | 3 |
| SM-2122065 | integration_failure | medium | integration | unknown | integration/unknown -> unknown/unknown | 0.50 (0.50) | 3/3 | ok | 32.84 | 8 |
| SM-2118792 | integration_failure | low | integration | master_data | integration/escalate_other_system -> master_data/master_data_change | 0.60 (0.70) | 1/1 | - | 53.94 | 3 |
| SM-2100070 | integration_failure | medium | data_correction | unknown | data_issue/data_fix -> unknown/unknown | 0.38 (0.38) | 1/1 | - | 38.69 | 6 |
| SM-2120545 | integration_failure | low | data_correction | config | data_issue/data_fix -> config/config_change | 0.45 (0.45) | 2/2 | ok | 30.29 | 10 |
| SM-2133110 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.60 (0.60) | 2/2 | - | 12.97 | 2 |
| SM-2092729 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.60 (0.60) | 2/2 | - | 9.51 | 2 |
| SM-2102064 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.70 (0.70) | 2/2 | - | 9.47 | 2 |
| SM-2123794 | master_data_request | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.70 (0.70) | 2/2 | ok | 14.25 | 2 |
| SM-2070909 | master_data_request | medium | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.40 (0.40) | 1/1 | - | 45.08 | 2 |
| SM-2135453 | master_data_request | low | config | unknown | config/config_change -> unknown/unknown | 0.45 (0.45) | 1/1 | - | 32.96 | 0 |
| SM-2143363 | access_request | high | access | access | access/grant_access -> access/grant_access | 0.50 (0.60) | 1/2 | - | 10.13 | 0 |
| SM-2122261 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 9.82 | 0 |
| SM-2132602 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 10.0 | 0 |
| SM-2142046 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/2 | - | 9.94 | 0 |
| SM-2001523 | notification_failure | high | config | user_error | config/config_change -> user_error/explain_to_user | 0.55 (0.55) | 3/3 | ok | 21.63 | 9 |
| SM-2091967 | notification_failure | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.48 (0.48) | 2/2 | - | 52.32 | 3 |
| SM-2127733 | notification_failure | low | config | master_data | config/config_change -> master_data/master_data_change | 0.55 (0.55) | 3/3 | ok | 17.6 | 8 |
| SM-2147237 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.40 (0.40) | 1/1 | ok | 31.37 | 9 |
| SM-2094146 | performance_report | high | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.35 (0.35) | 2/2 | - | 30.22 | 3 |
| SM-2061644 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/none | 0.40 (0.40) | 0/1 | - | 41.07 | 3 |
