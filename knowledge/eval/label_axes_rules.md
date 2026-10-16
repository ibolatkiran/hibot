# Label axes rules (round 1) - written BEFORE any re-scoring

Goal: add `expected_root_cause_type` and `expected_resolution_action` to every ticket of `eval_set.json` by a MECHANICAL mapping from the
existing silver fields (`expected_classification`, `label_basis`, `note`) and, where the basis says "manual fix", the footprint fact
`transition_allowed` of the changed status (from the local, git-ignored `knowledge/learning/ticket_windows.jsonl`; when that file is missing
the words in `label_basis` give the same answer: "transition not in StatusStep" = not allowed, "step exists" = allowed).
The rules are applied by `python tools/eval/add_axes.py` to ALL tickets (dev and test), in this order; the first matching rule wins.
No ticket is hand-labelled; no prediction of HiBot was looked at. `axes_rule` records the rule id in `eval_set.json`.

Axis values: root_cause_type = config | code_defect | master_data | integration | user_error | access | data_issue | unknown;
resolution_action = data_fix | config_change | code_change | master_data_change | explain_to_user | grant_access |
escalate_other_system | none | unknown.

| id | condition | root_cause_type | resolution_action |
|---|---|---|---|
| R1 | expected_classification = master_data | master_data | master_data_change |
| R2 | expected_classification = access | access | grant_access |
| R3 | expected_classification = user_error | user_error | explain_to_user |
| R4 | expected_classification = code_defect | code_defect | code_change |
| R5a | expected_classification = integration and `label_basis` or `note` contains "sap" or "atlas" (case-insensitive) | integration | escalate_other_system |
| R5b | expected_classification = integration, otherwise | integration | unknown |
| R6 | expected_classification = config | config | config_change |
| R7 | expected_classification = unknown | unknown | unknown |
| R8a | expected_classification = data_correction, manual fix in the footprint (a `manual_fix_detected` finding) and the footprint says the transition is NOT allowed (`transition_allowed` false / basis "transition not in StatusStep") | config | data_fix |
| R8b | expected_classification = data_correction, manual fix in the footprint and the transition IS allowed (`transition_allowed` true / basis "step exists") | data_issue | data_fix |
| R8c | expected_classification = data_correction and `label_basis` contains "no change" or "no log rows" or "no tracked field change" (no footprint to explain the cause) | unknown | data_fix |
| R8d | expected_classification = data_correction, anything else | data_issue | data_fix |

Notes
* The Egypt D17 tickets (`user_error`, devs forced the status by hand) follow R3: the cause is a deliberate restriction, the action is to explain it.
* "unknown" as an expected root cause means the silver evidence does not reveal the cause. HiBot is scored correct only when it also says `unknown`;
  a confident other answer counts as confidently wrong. Same exact-match rule for every axis (there is no lenient matching on the axes).
* Legacy metrics (exact / lenient on the old `classification`) are kept unchanged so results stay comparable with `baseline_20261016`.

## Deriving axes for reports that only have the legacy label (baseline run)
Done by `tools/eval/score.py` with the same table as `hibot/agent/axes.py` (`LEGACY_TO_AXES`):
code_defect -> (code_defect, code_change); master_data -> (master_data, master_data_change); access -> (access, grant_access);
data_correction -> (data_issue, data_fix); integration -> (integration, escalate_other_system); config -> (config, config_change);
user_error -> (user_error, explain_to_user); unknown -> (unknown, unknown).
Caveat for the comparison: a legacy "config" answer cannot express "config + data_fix", so the baseline's resolution_action / both-axes numbers are
pessimistic by construction; root_cause_type is the fair axis to compare.

## Derivation of the legacy label from the axes (new reports)
root_cause_type in {master_data, access, integration, code_defect} -> same name; otherwise resolution_action = data_fix -> data_correction;
otherwise config -> config, user_error -> user_error, data_issue -> data_correction, unknown -> unknown.
