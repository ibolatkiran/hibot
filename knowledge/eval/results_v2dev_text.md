# HiBot evaluation results: v2dev_text

Split: **dev**, tickets scored: **7** (missing: 0, errors: 0). Labels are SILVER (heuristic): accuracy against low-confidence labels is reported but the headline numbers use `reliable` labels (high+medium: 5).

## Headline

| metric | value |
|---|---|
| **confidently wrong** (wrong, conf >= 0.7, reliable labels) | **40%** (2 tickets) |
| honest unknown (unknown, conf < 0.5), all tickets | 0% |
| classification exact, reliable labels | 60% |
| classification lenient, reliable labels | 100% |
| classification exact / lenient, all labels | 71% / 100% |
| **confidently wrong on root_cause_type** (conf >= 0.7, reliable labels) | **0%** (0 tickets) |
| root_cause_type accuracy, reliable / all labels | 100% / 100% |
| resolution_action accuracy, reliable labels | 100% |
| both axes correct, reliable / all labels | 100% / 100% |
| work-order resolution (n=5) | 100% |
| tenant resolution (n=5) | 100% |
| expected_findings hit rate (18 checks) | 100% |
| card match rate | 86% |
| avg elapsed s / queries (executed) / LLM calls | 24.53 / 4.571 (4.571) / 2.286 |

## Calibration (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 0 | n/a | n/a |
| 0.4-0.7 | 0 | n/a | n/a |
| 0.7-1.0 | 5 | 60% | 0.848 |

## Calibration on root_cause_type (reliable labels)

| confidence | n | accuracy | mean confidence |
|---|---:|---:|---:|
| 0.0-0.4 | 0 | n/a | n/a |
| 0.4-0.7 | 0 | n/a | n/a |
| 0.7-1.0 | 5 | 100% | 0.848 |

## Per stratum

| stratum | n | exact (rel.) | lenient (rel.) | root cause (rel.) | both axes (rel.) | tenant | findings | conf. wrong (legacy / rct) | honest unk. | avg s | avg q |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| capability_gating | 1 | 100% | 100% | 100% | 100% | n/a | 100% | 0 / 0 | 0% | 33.15 | 2.0 |
| functional_defect | 1 | n/a | n/a | n/a | n/a | n/a | 100% | 0 / 0 | 0% | 32.65 | 1.0 |
| master_data_request | 1 | 100% | 100% | 100% | 100% | 100% | 100% | 0 / 0 | 0% | 30.0 | 5.0 |
| status_footprint | 3 | 33% | 100% | 100% | 100% | 100% | 100% | 2 / 0 | 0% | 18.443 | 6.0 |
| status_nochange | 1 | n/a | n/a | n/a | n/a | 100% | 100% | 0 / 0 | 0% | 20.58 | 6.0 |

## Confusion (reliable labels; expected -> got)

- change_request: change_request=1
- data_correction: config=2
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
| SM-2139009 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.88 (0.88) | 3/3 | ok | 20.19 | 6 |
| SM-2139002 | status_footprint | medium | data_correction | config **CW** | config/data_fix -> config/data_fix | 0.88 (0.88) | 3/3 | ok | 19.71 | 6 |
| SM-2126600 | status_footprint | high | user_error | user_error | user_error/explain_to_user -> user_error/explain_to_user | 0.88 (0.88) | 3/3 | ok | 15.43 | 6 |
| SM-2122480 | status_nochange | low | integration | integration | integration/escalate_other_system -> integration/escalate_other_system | 0.70 (0.70) | 3/3 | ok | 20.58 | 6 |
| SM-2172385 | capability_gating | high | change_request | change_request | change_request/route_to_backlog -> change_request/route_to_backlog | 0.80 (0.80) | 2/2 | - | 33.15 | 2 |
| SM-2169006 | master_data_request | high | master_data | master_data | master_data/master_data_change -> master_data/master_data_change | 0.80 (0.80) | 3/3 | ok | 30.0 | 5 |
| SM-1777386 | functional_defect | low | code_defect | code_defect | code_defect/code_change -> code_defect/code_change | 0.60 (0.70) | 1/1 | - | 32.65 | 1 |
