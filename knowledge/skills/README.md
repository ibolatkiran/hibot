# HiBot investigation skills

Written methods for investigating Sirius incidents. Each skill is a recipe the coordinator (Claude Opus) used to find root causes HiBot missed, written so that **any** investigator (HiBot's GPT-4o loop, or a developer using Claude Code with the HiBot MCP tools) can follow the same path.

Every brain reads a skill with the registry tool `read_skill(name)` (HiBot v2 loop, Claude native tool calling, MCP / Claude Code); the file name without `.md` is the name.

Rules for this library:
- A skill describes **how to investigate**, not a specific answer. Specific answers belong in cards (`knowledge/cards/`).
- Every step names the tool to use (see the HiBot tool layer `hibot/tools`, MCP server `python -m hibot.mcp_server`).
- After a case is solved by hand, add or improve a skill (teacher loop, D13/D19). Keep each skill short; add a "Worked example" with the ticket key.

## Always start with
- [investigation_protocol.md](investigation_protocol.md): the general loop (plan → locate → evidence → falsify → report). **Mandatory for every ticket.**
- [tenant_and_region.md](tenant_and_region.md): resolve the work order, tenant and region before anything else.

## Pick by symptom

| Symptom in the ticket | Skill |
|---|---|
| A value is 0 / wrong / missing (cost, price, labour, total, date, name) | [value_provenance.md](value_provenance.md) |
| "Cannot do X", button/field missing or not editable, "please update/change field", feature request | [feature_and_permission_gating.md](feature_and_permission_gating.md) |
| Cannot change / cancel / reject / close; "please change status to …" | [status_transition.md](status_transition.md) |
| Missing product / SKU / model / tenant / labour / price / BOM / defect code / warranty type | [master_data_setup.md](master_data_setup.md) |
| User quotes an error message or a screen shows an error | [error_message_trace.md](error_message_trace.md) |
| Not sent / not transferred to SAP, ERP, FY document, Salesforce, Atlas, Mule | [integration_failure.md](integration_failure.md) |
| SMS / e-mail / NPS / push not sent | [notification_failure.md](notification_failure.md) |
| Access, role, login, new user | [access_request.md](access_request.md) |
| What did the developer actually do on a closed ticket? (learning) | [ticket_window_footprint.md](ticket_window_footprint.md) |

Several skills can apply; run the cheapest discriminating step of each first.
