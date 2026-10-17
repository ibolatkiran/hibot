# Claude vs ArGPT on the same 40 test tickets

Subset: `knowledge/eval/subset20.json` (stratified half of the test split, reliable labels preferred). Silver labels; headline metrics on reliable labels. Claude limitation: `manual_fix_detected` only hits when the model called `ticket_window` itself.

| metric | holdout2_claude_haiku | holdout2_v1 | holdout2_v2 |
|---|---|---|---|
| n scored | 40 | 40 | 40 |
| reliable labels | 38 | 38 | 38 |
| confidently wrong (legacy) | 8% | 16% | 11% |
| confidently wrong (root_cause_type) | 8% | 11% | 5% |
| exact / legacy | 58% | 58% | 39% |
| lenient / legacy | 61% | 68% | 58% |
| root_cause_type acc | 55% | 68% | 45% |
| resolution_action acc | 53% | 63% | 47% |
| both axes | 45% | 58% | 42% |
| findings hit | 94% | 95% | 94% |
| WO resolution | 90% | 100% | 100% |
| tenant resolution | 90% | 100% | 100% |
| honest unknown | 12% | 15% | 18% |
| avg seconds | 86.975 | 24.606 | 66.044 |
| avg LLM calls | 14.175 | 2.6 | 6.925 |
| API cost (USD) | 0.705 | - | - |

## Per stratum, root_cause_type accuracy (all labels)

| stratum | holdout2_claude_haiku | holdout2_v1 | holdout2_v2 |
|---|---|---|---|
| access_request | 100% (n=4) | 100% (n=4) | 100% (n=4) |
| functional_defect | 43% (n=7) | 71% (n=7) | 57% (n=7) |
| integration_failure | 20% (n=5) | 40% (n=5) | 20% (n=5) |
| master_data_request | 67% (n=6) | 100% (n=6) | 83% (n=6) |
| notification_failure | 50% (n=4) | 50% (n=4) | 0% (n=4) |
| performance_report | 33% (n=3) | 33% (n=3) | 33% (n=3) |
| status_footprint | 83% (n=6) | 83% (n=6) | 67% (n=6) |
| status_nochange | 40% (n=5) | 60% (n=5) | 0% (n=5) |

## Per ticket

| key | stratum | expected rct | holdout2_claude_haiku | holdout2_v1 | holdout2_v2 |
|---|---|---|---|---|---|
| SM-1824360 | access_request | access | access ✓ 0.30 | access ✓ 0.50 | access ✓ 0.50 |
| SM-1829822 | access_request | access | access ✓ 0.40 | access ✓ 0.50 | access ✓ 0.35 |
| SM-1831956 | access_request | access | access ✓ 0.40 | access ✓ 0.50 | access ✓ 0.50 |
| SM-1850411 | access_request | access | access ✓ 0.35 | access ✓ 0.50 | access ✓ 0.50 |
| SM-1839332 | functional_defect | master_data | master_data ✓ 0.60 | master_data ✓ 0.69 | master_data ✓ 0.58 |
| SM-1870003 | functional_defect | user_error | data_issue 0.60 | code_defect 0.60 | user_error ✓ 0.32 |
| SM-1881470 | functional_defect | user_error | master_data 0.60 | user_error ✓ 0.50 | user_error ✓ 0.50 |
| SM-1886324 | functional_defect | master_data | master_data ✓ 0.60 | master_data ✓ 0.55 | master_data ✓ 0.57 |
| SM-1934122 | functional_defect | master_data | master_data ✓ 0.70 | master_data ✓ 0.60 | unknown 0.00 |
| SM-1979237 | functional_defect | user_error | master_data 0.80 | user_error ✓ 0.55 | config 0.54 |
| SM-2080016 | functional_defect | config | master_data 0.80 | master_data 0.78 | unknown 0.00 |
| SM-1905442 | integration_failure | integration | code_defect 0.50 | integration ✓ 0.55 | integration ✓ 0.50 |
| SM-1917116 | integration_failure | user_error | unknown 0.35 | config 0.55 | config 0.57 |
| SM-1924609 | integration_failure | integration | master_data 0.60 | master_data 0.78 | master_data 0.68 |
| SM-1960355 | integration_failure | integration | master_data 0.80 | master_data 0.78 | unknown 0.40 |
| SM-1976131 | integration_failure | integration | integration ✓ 0.50 | integration ✓ 0.50 | config 0.48 |
| SM-1954815 | master_data_request | master_data | master_data ✓ 0.60 | master_data ✓ 0.62 | master_data ✓ 0.63 |
| SM-2029368 | master_data_request | master_data | user_error 0.50 | master_data ✓ 0.69 | master_data ✓ 0.63 |
| SM-2065664 | master_data_request | master_data | master_data ✓ 0.72 | master_data ✓ 0.69 | config 0.85 |
| SM-2083959 | master_data_request | master_data | data_issue 0.40 | master_data ✓ 0.60 | master_data ✓ 0.58 |
| SM-2103728 | master_data_request | master_data | master_data ✓ 0.50 | master_data ✓ 0.69 | master_data ✓ 0.72 |
| SM-2121046 | master_data_request | master_data | master_data ✓ 0.60 | master_data ✓ 0.65 | master_data ✓ 0.59 |
| SM-1931803 | notification_failure | integration | integration ✓ 0.60 | integration ✓ 0.55 | user_error 0.68 |
| SM-1932075 | notification_failure | integration | integration ✓ 0.50 | integration ✓ 0.50 | config 0.65 |
| SM-2042803 | notification_failure | config | integration 0.65 | unknown 0.48 | unknown 0.48 |
| SM-2105830 | notification_failure | user_error | integration 0.50 | unknown 0.55 | unknown 0.30 |
| SM-1833067 | performance_report | user_error | access 0.35 | unknown 0.42 | unknown 0.00 |
| SM-2059766 | performance_report | unknown | unknown ✓ 0.30 | unknown ✓ 0.45 | unknown ✓ 0.25 |
| SM-2071460 | performance_report | user_error | unknown 0.35 | unknown 0.38 | integration 0.45 |
| SM-1969942 | status_footprint | config | config ✓ 0.60 | config ✓ 0.78 | config ✓ 0.65 |
| SM-1977792 | status_footprint | config | config ✓ 0.65 | config ✓ 0.78 | config ✓ 0.48 |
| SM-1980996 | status_footprint | config | config ✓ 0.80 | config ✓ 0.78 | user_error 0.58 |
| SM-2044656 | status_footprint | data_issue | config 0.45 | config 0.63 | config 0.58 |
| SM-2103087 | status_footprint | config | config ✓ 0.80 | config ✓ 0.85 | config ✓ 0.85 |
| SM-2139680 | status_footprint | config | config ✓ 0.60 | config ✓ 0.85 | config ✓ 0.85 |
| SM-1802347 | status_nochange | unknown | user_error 0.40 | unknown ✓ 0.48 | config 0.54 |
| SM-1871288 | status_nochange | unknown | unknown ✓ 0.35 | user_error 0.55 | user_error 0.48 |
| SM-1943978 | status_nochange | unknown | unknown ✓ 0.40 | unknown ✓ 0.45 | user_error 0.48 |
| SM-1957195 | status_nochange | unknown | config 0.50 | config 0.85 | config 0.85 |
| SM-2039109 | status_nochange | unknown | config 0.60 | unknown ✓ 0.55 | config 0.55 |
