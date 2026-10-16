# HiBot evaluation results: baseline_20261016

Split: **test**, tickets scored: **40** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 30).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **23%** (7 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 35% |
| classification exact, reliable labels | 40% |
| classification lenient, reliable labels | 50% |
| classification exact / lenient, all labels | 38% / 45% |
| work-order resolution (n=21) | 95% |
| tenant resolution (n=20) | 100% |
| expected_findings hit rate (78 checks) | 92% |
| card match rate | 70% |
| avg elapsed s / queries (executed) / LLM calls | 23.684 / 3.775 (3.725) / 3.025 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 10 | 0% | 0.311 |
| 0.4-0.7 | 6 | 83% | 0.52 |
| 0.7-1.0 | 14 | 50% | 0.816 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | tenant | findings | conf. wrong | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| access_request | 4 | 25% | 25% | n/a | 50% | 0 | 75% | 22.477 | 0.0 |
| functional_defect | 8 | 60% | 60% | 100% | 100% | 1 | 38% | 22.223 | 2.5 |
| integration_failure | 6 | 25% | 25% | 100% | 82% | 0 | 67% | 28.932 | 4.0 |
| master_data_request | 6 | 80% | 80% | 100% | 100% | 0 | 0% | 15.938 | 1.667 |
| notification_failure | 3 | 0% | 0% | 100% | 100% | 1 | 33% | 25.73 | 5.333 |
| performance_report | 3 | 0% | 0% | 100% | 100% | 0 | 67% | 34.213 | 5.0 |
| status_footprint | 5 | 40% | 100% | 100% | 100% | 3 | 0% | 21.084 | 6.6 |
| status_nochange | 5 | 25% | 25% | 100% | 100% | 2 | 20% | 25.04 | 6.6 |

## Confusion (reliable labels; expected -> got)

- access: access=1, unknown=3
- config: user_error=1
- data_correction: config=3, data_correction=2, unknown=2, user_error=2
- integration: integration=1, unknown=2
- master_data: master_data=6, unknown=1, user_error=1
- user_error: access=1, unknown=2, user_error=2

## Per ticket

| key | stratum | label | expected | got | conf | findings | tenant | s | q |
|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-2143299 | status_footprint | medium | data_correction | config **CW** | 0.86 | 3/3 | ok | 21.46 | 7 |
| SM-2132761 | status_footprint | medium | data_correction | config **CW** | 0.90 | 3/3 | ok | 19.69 | 7 |
| SM-2100037 | status_footprint | medium | data_correction | data_correction | 0.70 | 2/2 | ok | 19.91 | 6 |
| SM-1967154 | status_footprint | medium | data_correction | config **CW** | 0.86 | 3/3 | ok | 19.42 | 7 |
| SM-2070070 | status_footprint | high | user_error | user_error | 0.90 | 3/3 | ok | 24.94 | 6 |
| SM-2142402 | status_nochange | low | data_correction | user_error | 0.78 | 2/2 | ok | 19.92 | 5 |
| SM-2122464 | status_nochange | medium | data_correction | user_error **CW** | 0.86 | 2/2 | ok | 15.91 | 5 |
| SM-2010200 | status_nochange | medium | data_correction | user_error **CW** | 0.85 | 2/2 | ok | 23.86 | 8 |
| SM-2147977 | status_nochange | medium | data_correction | data_correction | 0.82 | 2/2 | ok | 26.43 | 8 |
| SM-2137438 | status_nochange | medium | data_correction | unknown | 0.38 | 2/2 | ok | 39.08 | 7 |
| SM-2143981 | functional_defect | medium | master_data | master_data | 0.70 | 3/3 | ok | 13.3 | 2 |
| SM-2112749 | functional_defect | medium | master_data | unknown | 0.30 | 1/1 | - | 32.62 | 3 |
| SM-2128512 | functional_defect | medium | user_error | access **CW** | 0.78 | 1/1 | - | 15.75 | 3 |
| SM-2070960 | functional_defect | high | master_data | master_data | 0.78 | 2/2 | ok | 14.11 | 2 |
| SM-2092603 | functional_defect | medium | user_error | user_error | 0.55 | 1/1 | - | 7.29 | 0 |
| SM-2104030 | functional_defect | low | unknown | unknown | 0.20 | 1/1 | - | 35.74 | 0 |
| SM-2088913 | functional_defect | low | master_data | master_data | 0.62 | 2/2 | ok | 20.32 | 6 |
| SM-2082092 | functional_defect | low | data_correction | unknown | 0.38 | 2/2 | ok | 38.65 | 4 |
| SM-2146596 | integration_failure | medium | integration | unknown | 0.25 | 1/2 | - | 30.39 | 0 |
| SM-2144810 | integration_failure | medium | integration | integration | 0.45 | 1/2 | - | 13.12 | 0 |
| SM-2122065 | integration_failure | medium | integration | unknown | 0.35 | 3/3 | ok | 30.7 | 8 |
| SM-2118792 | integration_failure | low | integration | unknown | 0.35 | 1/1 | - | 35.37 | 2 |
| SM-2100070 | integration_failure | medium | data_correction | unknown | 0.35 | 1/1 | - | 36.96 | 4 |
| SM-2120545 | integration_failure | low | data_correction | master_data | 0.70 | 2/2 | ok | 27.05 | 10 |
| SM-2133110 | master_data_request | high | master_data | master_data | 0.62 | 2/2 | - | 11.59 | 2 |
| SM-2092729 | master_data_request | high | master_data | master_data | 0.40 | 2/2 | - | 20.85 | 2 |
| SM-2102064 | master_data_request | high | master_data | master_data | 0.70 | 2/2 | - | 12.47 | 2 |
| SM-2123794 | master_data_request | medium | master_data | master_data | 0.86 | 2/2 | ok | 15.11 | 2 |
| SM-2070909 | master_data_request | medium | master_data | user_error | 0.55 | 1/1 | - | 16.89 | 2 |
| SM-2135453 | master_data_request | low | config | user_error | 0.55 | 1/1 | - | 18.72 | 0 |
| SM-2143363 | access_request | high | access | access | 0.55 | 1/2 | - | 9.39 | 0 |
| SM-2122261 | access_request | medium | access | unknown | 0.25 | 1/2 | - | 29.93 | 0 |
| SM-2132602 | access_request | medium | access | unknown | 0.25 | 1/2 | - | 22.87 | 0 |
| SM-2142046 | access_request | medium | access | unknown | 0.30 | 1/2 | - | 27.72 | 0 |
| SM-2001523 | notification_failure | high | config | user_error **CW** | 0.86 | 3/3 | ok | 20.04 | 9 |
| SM-2091967 | notification_failure | medium | user_error | unknown | 0.38 | 2/2 | - | 37.18 | 1 |
| SM-2127733 | notification_failure | low | config | user_error | 0.85 | 3/3 | ok | 19.97 | 6 |
| SM-2147237 | performance_report | low | unknown | user_error | 0.78 | 1/1 | ok | 26.64 | 7 |
| SM-2094146 | performance_report | high | user_error | unknown | 0.30 | 2/2 | - | 34.29 | 4 |
| SM-2061644 | performance_report | low | unknown | unknown | 0.48 | 1/1 | - | 41.71 | 4 |
