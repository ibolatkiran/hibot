# HiBot evaluation results: holdout2_v2

Split: **test**, tickets scored: **40** (missing: 0, errors: 4). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 38).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **11%** (4 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 18% |
| classification exact, reliable labels | 39% |
| classification lenient, reliable labels | 58% |
| classification exact / lenient, all labels | 42% / 60% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **5%** (2 tickets) |
| root_cause_type accuracy, reliable / all labels | 45% / 48% |
| resolution_action accuracy, reliable labels | 47% |
| both axes correct, reliable / all labels | 42% / 42% |
| work-order resolution (n=29) | 100% |
| tenant resolution (n=29) | 100% |
| expected_findings hit rate (83 checks) | 94% |
| card match rate | 70% |
| avg elapsed s / queries (executed) / LLM calls | 66.044 / 7.45 (7.275) / 6.925 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 6 | 33% | 0.162 |
| 0.4-0.7 | 27 | 44% | 0.55 |
| 0.7-1.0 | 5 | 20% | 0.824 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 6 | 33% | 0.162 |
| 0.4-0.7 | 27 | 44% | 0.55 |
| 0.7-1.0 | 5 | 60% | 0.824 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| access_request | 4 | 100% | 100% | 100% | 100% | n/a | 100% | 0 / 0 | 0% | 35.095 | 0.0 |
| functional_defect | 7 | 57% | 57% | 57% | 57% | 100% | 88% | 0 / 0 | 29% | 62.401 | 8.429 |
| integration_failure | 5 | 0% | 0% | 0% | 0% | 100% | 92% | 0 / 0 | 20% | 82.724 | 12.0 |
| master_data_request | 6 | 83% | 100% | 83% | 83% | 100% | 100% | 1 / 1 | 0% | 56.032 | 6.833 |
| notification_failure | 4 | 0% | 0% | 0% | 0% | 100% | 100% | 0 / 0 | 50% | 83.332 | 7.0 |
| performance_report | 3 | 0% | 0% | 0% | 0% | n/a | 75% | 0 / 0 | 67% | 75.597 | 2.333 |
| status_footprint | 6 | 33% | 83% | 67% | 50% | 100% | 94% | 2 / 0 | 0% | 64.097 | 8.833 |
| status_nochange | 5 | 0% | 60% | 0% | 0% | 100% | 100% | 1 / 1 | 0% | 74.014 | 10.0 |

## Confusion (reliable labels; expected -> got)

- access: access=4
- config: unknown=2
- data_correction: config=6, data_correction=2, user_error=3
- integration: config=2, master_data=1, unknown=1, user_error=1
- master_data: config=1, master_data=7, unknown=1
- user_error: config=2, integration=1, unknown=2, user_error=2

## Confusion on root_cause_type (reliable labels; expected -> got)

- access: access=4
- config: config=4, unknown=2, user_error=1
- data_issue: config=1
- integration: config=2, master_data=1, unknown=1, user_error=1
- master_data: config=1, master_data=7, unknown=1
- unknown: config=3, user_error=2
- user_error: config=2, integration=1, unknown=2, user_error=2

## Per ticket

| key | stratum | label | expected | got | axes expected -> got | conf (raw) | findings | tenant | s | q |
|---|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-1977792 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.48 (0.48) | 3/3 | ok | 62.75 | 7 |
| SM-1980996 | status_footprint | medium | data_correction | user_error | config/data_fix -> user_error/explain_to_user | 0.58 (0.58) | 3/3 | ok | 53.83 | 7 |
| SM-2044656 | status_footprint | medium | data_correction | data_correction | data_issue/data_fix -> config/data_fix | 0.58 (0.58) | 1/2 | ok | 48.52 | 10 |
| SM-2103087 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.85 (0.71) | 3/3 | ok | 63.28 | 10 |
| SM-2139680 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.85 (0.71) | 3/3 | ok | 78.95 | 10 |
| SM-1969942 | status_footprint | medium | data_correction | config | config/data_fix -> config/none | 0.65 (0.65) | 3/3 | ok | 77.25 | 9 |
| SM-1802347 | status_nochange | medium | data_correction | config | unknown/data_fix -> config/config_change | 0.54 (0.54) | 2/2 | ok | 76.06 | 6 |
| SM-1871288 | status_nochange | medium | data_correction | user_error | unknown/data_fix -> user_error/explain_to_user | 0.48 (0.48) | 2/2 | ok | 67.37 | 7 |
| SM-1943978 | status_nochange | medium | data_correction | user_error | unknown/data_fix -> user_error/explain_to_user | 0.48 (0.48) | 2/2 | ok | 95.04 | 16 |
| SM-1957195 | status_nochange | medium | data_correction | config **CW** | unknown/data_fix -> config/data_fix **CW-rct** | 0.85 (0.00) | 2/2 | ok | 59.56 | 11 |
| SM-2039109 | status_nochange | medium | data_correction | config | unknown/data_fix -> config/config_change | 0.55 (0.55) | 2/2 | ok | 72.04 | 10 |
| SM-1839332 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.58 (0.58) | 2/2 | ok | 55.37 | 10 |
| SM-1934122 | functional_defect | medium | master_data | unknown | master_data/master_data_change -> unknown/unknown | 0.00 (0.00) | 1/2 | ok | 24.31 | 6 |
| SM-1870003 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.32 (0.32) | 3/3 | ok | 78.79 | 14 |
| SM-2080016 | functional_defect | medium | config | unknown | config/config_change -> unknown/unknown | 0.00 (0.00) | 1/2 | ok | 30.11 | 2 |
| SM-1881470 | functional_defect | medium | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.50 (0.50) | 3/3 | ok | 84.22 | 11 |
| SM-1979237 | functional_defect | medium | user_error | config | user_error/explain_to_user -> config/config_change | 0.54 (0.54) | 3/3 | ok | 82.3 | 9 |
| SM-1886324 | functional_defect | medium | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.57 (0.57) | 2/2 | ok | 81.71 | 7 |
| SM-1924609 | integration_failure | medium | integration | master_data | integration/escalate_other_system -> master_data/master_data_change | 0.68 (0.68) | 2/2 | ok | 44.4 | 10 |
| SM-1960355 | integration_failure | medium | integration | unknown | integration/escalate_other_system -> unknown/route_to_backlog | 0.40 (0.40) | 2/2 | ok | 123.39 | 9 |
| SM-1976131 | integration_failure | medium | integration | config | integration/escalate_other_system -> config/explain_to_user | 0.48 (0.48) | 2/3 | ok | 105.49 | 16 |
| SM-1905442 | integration_failure | low | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.50 (0.50) | 2/2 | ok | 54.56 | 12 |
| SM-1917116 | integration_failure | medium | user_error | config | user_error/explain_to_user -> config/config_change | 0.57 (0.57) | 3/3 | ok | 85.78 | 13 |
| SM-2083959 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.58 (0.58) | 3/3 | ok | 38.45 | 9 |
| SM-2103728 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.72 (0.72) | 2/2 | ok | 63.57 | 8 |
| SM-2029368 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.63 (0.63) | 3/3 | ok | 61.68 | 4 |
| SM-2121046 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.59 (0.59) | 2/2 | ok | 57.02 | 3 |
| SM-2065664 | master_data_request | medium | master_data | config **CW** | master_data/master_data_change -> config/data_fix **CW-rct** | 0.85 (0.68) | 2/2 | ok | 41.8 | 12 |
| SM-1954815 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.63 (0.63) | 1/1 | - | 73.67 | 5 |
| SM-1831956 | access_request | high | access | access | access/grant_access -> access/grant_access | 0.50 (0.55) | 1/1 | - | 33.0 | 0 |
| SM-1829822 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.35 (0.35) | 1/1 | - | 41.14 | 0 |
| SM-1850411 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.55) | 1/1 | - | 29.12 | 0 |
| SM-1824360 | access_request | medium | access | access | access/grant_access -> access/grant_access | 0.50 (0.55) | 1/1 | - | 37.12 | 0 |
| SM-2042803 | notification_failure | medium | config | unknown | config/config_change -> unknown/none | 0.48 (0.48) | 1/1 | - | 93.13 | 5 |
| SM-1931803 | notification_failure | medium | integration | user_error | integration/unknown -> user_error/explain_to_user | 0.68 (0.68) | 2/2 | ok | 50.26 | 6 |
| SM-2105830 | notification_failure | medium | user_error | unknown | user_error/explain_to_user -> unknown/none | 0.30 (0.30) | 2/2 | - | 94.17 | 6 |
| SM-1932075 | notification_failure | medium | integration | config | integration/escalate_other_system -> config/config_change | 0.65 (0.65) | 1/1 | - | 95.77 | 11 |
| SM-2071460 | performance_report | medium | user_error | integration | user_error/explain_to_user -> integration/escalate_other_system | 0.45 (0.45) | 2/2 | - | 98.83 | 3 |
| SM-1833067 | performance_report | medium | user_error | unknown | user_error/explain_to_user -> unknown/unknown | 0.00 (0.00) | 1/1 | - | 31.23 | 0 |
| SM-2059766 | performance_report | low | unknown | unknown | unknown/unknown -> unknown/none | 0.25 (0.25) | 0/1 | - | 96.73 | 4 |
