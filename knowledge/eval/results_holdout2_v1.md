# HiBot evaluation results: holdout2_v1

Split: **test**, tickets scored: **40** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 38).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **16%** (6 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 15% |
| classification exact, reliable labels | 58% |
| classification lenient, reliable labels | 68% |
| classification exact / lenient, all labels | 60% / 70% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **11%** (4 tickets) |
| root_cause_type accuracy, reliable / all labels | 68% / 70% |
| resolution_action accuracy, reliable labels | 63% |
| both axes correct, reliable / all labels | 58% / 60% |
| work-order resolution (n=29) | 100% |
| tenant resolution (n=29) | 100% |
| expected_findings hit rate (83 checks) | 95% |
| card match rate | 70% |
| avg elapsed s / queries (executed) / LLM calls | 24.606 / 5.025 (5.025) / 2.6 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 1 | 0% | 0.38 |
| 0.4-0.7 | 28 | 68% | 0.559 |
| 0.7-1.0 | 9 | 33% | 0.803 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 1 | 0% | 0.38 |
| 0.4-0.7 | 28 | 75% | 0.559 |
| 0.7-1.0 | 9 | 56% | 0.803 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| access_request | 4 | 100% | 100% | 100% | 100% | n/a | 100% | 0 / 0 | 0% | 14.922 | 0.0 |
| functional_defect | 7 | 71% | 86% | 71% | 71% | 100% | 94% | 1 / 1 | 0% | 24.561 | 5.714 |
| integration_failure | 5 | 25% | 25% | 25% | 25% | 100% | 92% | 2 / 2 | 0% | 26.262 | 8.0 |
| master_data_request | 6 | 100% | 100% | 100% | 100% | 100% | 100% | 0 / 0 | 0% | 17.62 | 4.5 |
| notification_failure | 4 | 50% | 50% | 50% | 25% | 100% | 100% | 0 / 0 | 25% | 27.858 | 3.5 |
| performance_report | 3 | 0% | 0% | 0% | 0% | n/a | 75% | 0 / 0 | 100% | 41.98 | 1.0 |
| status_footprint | 6 | 67% | 100% | 83% | 83% | 100% | 94% | 2 / 0 | 0% | 23.427 | 7.0 |
| status_nochange | 5 | 0% | 20% | 60% | 0% | 100% | 100% | 1 / 1 | 40% | 27.534 | 7.0 |

## Confusion (reliable labels; expected -> got)

- access: access=4
- config: master_data=1, unknown=1
- data_correction: config=3, data_correction=4, unknown=3, user_error=1
- integration: integration=3, master_data=2
- master_data: master_data=9
- user_error: code_defect=1, config=1, unknown=3, user_error=2

## Confusion on root_cause_type (reliable labels; expected -> got)

- access: access=4
- config: config=5, master_data=1, unknown=1
- data_issue: config=1
- integration: integration=3, master_data=2
- master_data: master_data=9
- unknown: config=1, unknown=3, user_error=1
- user_error: code_defect=1, config=1, unknown=3, user_error=2

## Per ticket

| key | stratum | label | expected | got | axes expected -> got | conf (raw) | findings | tenant | s | q |
|---|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-1977792 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 22.06 | 7 |
| SM-1980996 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 18.89 | 6 |
| SM-2044656 | status_footprint | medium | data_correction | data_correction | data_issue/data_fix -> config/data_fix | 0.63 (0.63) | 1/2 | ok | 32.55 | 8 |
| SM-2103087 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.85 (0.83) | 3/3 | ok | 21.2 | 7 |
| SM-2139680 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.85 (0.83) | 3/3 | ok | 21.29 | 7 |
| SM-1969942 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 24.57 | 7 |
| SM-1802347 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.48 (0.48) | 2/2 | ok | 31.74 | 6 |
| SM-1871288 | status_nochange | medium | data_correction | user_error | unknown/data_fix -> user_error/explain_to_user | 0.55 (0.55) | 2/2 | ok | 15.3 | 5 |
| SM-1943978 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.45 (0.45) | 2/2 | ok | 31.97 | 8 |
| SM-1957195 | status_nochange | medium | data_correction | config **CW** | unknown/data_fix -> config/data_fix **CW-rct** | 0.85 (0.85) | 2/2 | ok | 23.4 | 8 |
| SM-2039109 | status_nochange | medium | data_correction | unknown | unknown/data_fix -> unknown/unknown | 0.55 (0.55) | 2/2 | ok | 35.26 | 8 |
| SM-1839332 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.69 (0.70) | 2/2 | ok | 21.08 | 8 |
| SM-1934122 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.60 (0.78) | 2/2 | ok | 25.72 | 7 |
| SM-1870003 | functional_defect | medium | user_error | code_defect | user_error/explain_to_user -> code_defect/code_change | 0.60 (0.68) | 2/3 | ok | 46.16 | 6 |
| SM-2080016 | functional_defect | medium | config | master_data **CW** | config/config_change -> master_data/master_data_change **CW-rct** | 0.78 (0.78) | 2/2 | ok | 23.22 | 4 |
| SM-1881470 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.50 (0.50) | 3/3 | ok | 22.28 | 8 |
| SM-1979237 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.55 (0.55) | 3/3 | ok | 16.65 | 5 |
| SM-1886324 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.55 (0.55) | 2/2 | ok | 16.82 | 2 |
| SM-1924609 | integration_failure | medium | integration | master_data **CW** | integration/escalate_other_system -> master_data/master_data_change **CW-rct** | 0.78 (0.78) | 2/2 | ok | 23.1 | 9 |
| SM-1960355 | integration_failure | medium | integration | master_data **CW** | integration/escalate_other_system -> master_data/master_data_change **CW-rct** | 0.78 (0.78) | 2/2 | ok | 21.78 | 6 |
| SM-1976131 | integration_failure | medium | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.50 (0.50) | 2/3 | ok | 31.91 | 10 |
| SM-1905442 | integration_failure | low | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.55 (0.55) | 2/2 | ok | 24.96 | 7 |
| SM-1917116 | integration_failure | medium | user_error | config | user_error/explain_to_user -> config/config_change | 0.55 (0.63) | 3/3 | ok | 29.56 | 8 |
| SM-2083959 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.60 (0.70) | 3/3 | ok | 24.56 | 9 |
| SM-2103728 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.69 (0.78) | 2/2 | ok | 15.82 | 2 |
| SM-2029368 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.69 (0.78) | 3/3 | ok | 14.13 | 2 |
| SM-2121046 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.65 (0.65) | 2/2 | ok | 14.69 | 2 |
| SM-2065664 | master_data_request | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.69 (0.78) | 2/2 | ok | 22.93 | 9 |
| SM-1954815 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.62 (0.62) | 1/1 | - | 13.59 | 3 |
| SM-1831956 | access_request | high | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/1 | - | 14.67 | 0 |
| SM-1829822 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.55) | 1/1 | - | 15.18 | 0 |
| SM-1850411 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/1 | - | 12.04 | 0 |
| SM-1824360 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.50) | 1/1 | - | 17.8 | 0 |
| SM-2042803 | notification_failure | medium | config | unknown | config/config_change -> unknown/unknown | 0.48 (0.48) | 1/1 | - | 37.63 | 5 |
| SM-1931803 | notification_failure | medium | integration | integration | integration/unknown -> integration/escalate_other_system | 0.55 (0.55) | 2/2 | ok | 26.3 | 5 |
| SM-2105830 | notification_failure | medium | user_error | unknown | user_error/explain_to_user -> unknown/escalate_other_system | 0.55 (0.55) | 2/2 | - | 34.03 | 2 |
| SM-1932075 | notification_failure | medium | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.50 (0.50) | 1/1 | - | 13.47 | 2 |
| SM-2071460 | performance_report | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.38 (0.38) | 2/2 | - | 44.25 | 0 |
| SM-1833067 | performance_report | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.42 (0.42) | 1/1 | - | 33.92 | 0 |
| SM-2059766 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/unknown | 0.45 (0.45) | 0/1 | - | 47.77 | 3 |
