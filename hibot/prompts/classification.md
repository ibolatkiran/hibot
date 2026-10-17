## Classification rules (two independent axes; never mix them)
`root_cause_type` = WHY the user sees the problem. `resolution_action` = WHAT has to be done about it. Decide both from EVIDENCE you saw.

root_cause_type
- config: tenant / status-flow / notification / pricing CONFIGURATION is missing or restrictive (e.g. a transition is not defined in Catalog.StatusStep for the tenant; a tenant parameter row is absent).
- code_defect: ONLY when data and configuration are correct and the code / SP does the wrong thing. Cite file:line.
- master_data: a SKU / model / labour material / price group / defect code / translation / tenant assignment / storage location is missing or wrong (setup data, not a transaction).
- integration: SAP / gateway / queue / job failure, or the OTHER system (Atlas, SAP, Salesforce, Okta ...) disagrees with Sirius.
- user_error: the system behaved as designed and the user misunderstood (a deliberate restriction, a required process step skipped).
- access: ONE user / account lacks a role, tenant membership, employee link, or is disabled / missing.
- data_issue: ONE record is in a wrong state / has wrong values and nothing structural explains it.
- change_request: nothing is broken; the system works exactly as coded and the reporter wants DIFFERENT behaviour (a feature or permission scope the code limits on purpose).
- unknown: the evidence does not decide.

resolution_action
- data_fix (a developer corrects the record in the DB), config_change, code_change, master_data_change, explain_to_user, grant_access,
  escalate_other_system (SAP / Atlas / Okta ... side must act), route_to_backlog (needs a product / business decision plus a code or config change), none, unknown.

Decision rules (apply them in this order of specificity)
1. DELIBERATE RESTRICTION -> user_error / explain_to_user. The action is blocked by design (e.g. the transition is intentionally not offered and a historical manual force of it was a process violation). Do not turn it into a config or code finding.
2. MISSING CONFIGURATION / TRANSITION -> config. A status step is absent for the tenant and a developer forced the status by hand (ticket-window MANUAL DB FIX) = root_cause_type config AND resolution_action data_fix, because data_fix is what was actually done; name the permanent fix (config_change) in fix_steps, do not put it in resolution_action. Config missing and the fix is to add it = config + config_change.
3. HARD-CODED LIST in code (user-type / role / tenant ids in a screen or SP) EXCLUDES the reporter's role or tenant, and the reporter wants to be included = change_request / route_to_backlog. Give file:line, the allowed ids, and options (workaround by an allowed role; add the role after business approval; move the list to configuration). ONE user missing a role the application's own configuration grants others = access / grant_access. A tenant configuration row (not code) = config.
4. MISSING SETUP DATA (no labour material / price group for a SKU or whole hierarchy in the tenant, SKU / model / defect code absent) = master_data / master_data_change. A calculated value is 0 because the INPUT rows do not exist is NOT a code defect: ask why the rows do not exist.
5. OTHER-SYSTEM STATE (Atlas / SAP / Salesforce shows something different, a document was not sent) = integration / escalate_other_system unless OUR logs show our side failed (then integration with the failing job / queue as the finding). The other system's data is not in our DB: say what cannot be verified and keep confidence <= 0.6 unless a log decides it.
6. Access requests (login, role, disabled account, third-party app access): access / grant_access (escalate_other_system when the other system's admin must act). If no user code is in the ticket the nature is still clear: access + grant_access with confidence <= 0.6, do not answer unknown.
7. SNAPSHOT CAVEAT for master-data tickets: tickets are analysed AFTER they were handled. A request to ADD / register / load a SKU, model, labour or defect code whose object EXISTS now is the footprint of the master-data change that fixed it => master_data / master_data_change, not user_error and not "already exists", unless dates or text prove it was complete for that tenant before the ticket.
8. "NO CHANGE IN THE TICKET WINDOW" is NOT evidence of user_error. A status / data-correction request with no change on the work order in the window may have been fixed elsewhere (other tables, another system, a job, later) or done through the UI. Prefer unknown / unknown (or escalate_other_system when the ticket names another system) with confidence <= 0.5, unless an executed query shows the system really blocked the action by design. A work order that is already in the requested status does NOT mean user_error: find out HOW it got there.
9. Status tickets: never conclude from the current status alone. If the ticket-window footprint (case-file section or ticket_window tool) says MANUAL DB FIX suspected, the real resolution was a direct data correction; the root cause is WHY the application did not allow the change (StatusStep gap = config with resolution_action data_fix; blocked by design = user_error / explain_to_user).
10. Notification (SMS / e-mail) not sent: separate "row stuck unsent" (sender failure = integration), "no row" (sending condition / template configuration = config), "row SENT" (recipient side = user_error). Slow / not loading: if the window shows nothing, say so (unknown, confidence <= 0.5) and list what is needed (URL, user, exact time).
11. 0 rows from a lookup is a FINDING, not a reason for unknown or user_error: try another identifier, the other region, the input tables.

KNOWN-CARD RULE
A known-issue card (case-file section "BİLİNEN SORUN KARTI", or a cards_match result) is a HYPOTHESIS that the evidence confirms or refutes.
- CONFIRMED card + its verification says it is SUPPORTED (DOĞRULAMA SONUCU: DESTEKLİYOR) and the ticket text mentions the card's target action: ADOPT the card. Use its root_cause_type, resolution_action, root cause and fix steps (mark your own additions as additions), cite it as evidence (type "card", ref = card id).
- DRAFT card, or a card matched only on tenant + status, or verification DOĞRULANAMADI: keep it as a hypothesis and keep investigating.
- Verification CONTRADICTS (ÇELİŞİYOR): say so explicitly in the report and investigate on your own.
- A user_error card means the system behaved as designed: do not turn it into a config / code finding.

Confidence is post-processed by the program: capped at 0.5 when no query ran, at 0.6 unless an executed query with a non-empty relevant result (or a supported card / manual-fix finding) supports your root cause, at 0.69 unless a confirmed supported card or >= 2 INDEPENDENT evidences (distinct executed queries, or code files you really read and cite) support it, at 0.85 unless a confirmed supported card or >= 3 independent evidences support it, and at 0.55 for user_error / config on a status or data-correction ticket whose ticket window shows no status change (unless status_step_check proved the transition missing). Give an honest raw value and cite the supporting queries as evidence (ref = template id, or `sql` for ad-hoc SQL).
