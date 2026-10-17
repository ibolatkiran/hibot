# What did the developer actually do? (ticket-window footprint)

Developers rarely write the real diagnosis or fix into Jira, but their fixes leave traces in the DB (D13).

## Steps
1. Ticket window = Jira `created` (UTC+03:00) − 5 min … `resolved` (own offset) + 15 min; DB datetimes are UTC.
2. For each resolved work order: `wo_field_changes` (Log.WorkOrderJsonLog before/after) and `wo_status_timeline` (WorkOrderGpsInfo).
3. A status change inside the window **without** a matching history row (±5 min) is a suspected **manual DB fix**. If `UpdateUserId` is also unchanged, the level is high.
4. For every status change found, run `status_step_check`: a manual fix of a not-configured transition means the app refused it. The real question is then *why* (config gap or deliberate rule).
5. No change in the window ≠ user error. The fix may be in other tables (master data, user setup) or in another system.
6. Baseline: about 2% of status changes outside ticket windows have no history row (jobs/triggers). Rapid chains (< 120 s) are likely automatic flows, not manual fixes.

Batch version: `tools/learning/batch_footprint.py` + `mine_patterns.py` → patterns and draft cards (FINDINGS §13).
