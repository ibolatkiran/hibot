# HiBot evaluation results: round2_dev_20261017

Split: **dev**, tickets scored: **7** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 5).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **0%** (0 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 14% |
| classification exact, reliable labels | 100% |
| classification lenient, reliable labels | 100% |
| classification exact / lenient, all labels | 71% / 71% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **0%** (0 tickets) |
| root_cause_type accuracy, reliable / all labels | 100% / 71% |
| resolution_action accuracy, reliable labels | 100% |
| both axes correct, reliable / all labels | 100% / 71% |
| work-order resolution (n=5) | 100% |
| tenant resolution (n=5) | 100% |
| expected_findings hit rate (18 checks) | 100% |
| card match rate | 86% |
| avg elapsed s / queries (executed) / LLM calls | 28.93 / 4.714 (4.714) / 2.571 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 0 | n/a | n/a |
| 0.4-0.7 | 1 | 100% | 0.45 |
| 0.7-1.0 | 4 | 100% | 0.802 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 0 | n/a | n/a |
| 0.4-0.7 | 1 | 100% | 0.45 |
| 0.7-1.0 | 4 | 100% | 0.802 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capability_gating | 1 | 100% | 100% | 100% | 100% | n/a | 100% | 0 / 0 | 0% | 30.14 | 2.0 |
| functional_defect | 1 | n/a | n/a | n/a | n/a | n/a | 100% | 0 / 0 | 0% | 36.99 | 0.0 |
| master_data_request | 1 | 100% | 100% | 100% | 100% | 100% | 100% | 0 / 0 | 0% | 20.57 | 5.0 |
| status_footprint | 3 | 100% | 100% | 100% | 100% | 100% | 100% | 0 / 0 | 0% | 22.693 | 6.0 |
| status_nochange | 1 | n/a | n/a | n/a | n/a | 100% | 100% | 0 / 0 | 100% | 46.73 | 8.0 |

## Confusion (reliable labels; expected -> got)

- change_request: change_request=1
- data_correction: data_correction=2
- master_data: master_data=1
- user_error: user_error=1

## Confusion on root_cause_type (reliable labels; expected -> got)

- change_request: change_request=1
- config: config=2
- master_data: master_data=1
- user_error: user_error=1

## Per ticket

| key | stratum | label | expected | got | axes expected -> got | conf (raw) | findings | tenant | s | q |
|---|---|---|---|---|---|---:|---:|---|---:|---:|
| SM-2139009 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.80 (0.80) | 3/3 | ok | 22.39 | 6 |
| SM-2139002 | status_footprint | medium | data_correction | data_correction | config/data_fix -> config/data_fix | 0.78 (0.78) | 3/3 | ok | 27.3 | 6 |
| SM-2126600 | status_footprint | high | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.85 (0.85) | 3/3 | ok | 18.39 | 6 |
| SM-2122480 | status_nochange | low | integration | unknown | integration/escalate_other_system -> unknown/escalate_other_system | 0.48 (0.48) | 3/3 | ok | 46.73 | 8 |
| SM-2172385 | capability_gating | high | change_request | change_request | change_request/route_to_backlog -> change_request/route_to_backlog | 0.45 (0.45) | 2/2 | - | 30.14 | 2 |
| SM-2169006 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.78 (0.78) | 3/3 | ok | 20.57 | 5 |
| SM-1777386 | functional_defect | low | code_defect | unknown | code_defect/code_change -> unknown/route_to_backlog | 0.50 (0.55) | 1/1 | - | 36.99 | 0 |
