# Investigation protocol (always)

The difference between a shallow and a correct diagnosis is almost always: **the investigator stopped at a plausible story instead of following the data/code to the mechanism.** This protocol prevents that.

## 1. Plan (before any tool call)
Write down:
- **What exactly is the user observing?** (symptom in one sentence, in system terms: which screen / field / action / record)
- **Which entities are given?** Work order numbers, SKUs, user codes, dates, quoted error text, screen names.
- **2–4 competing hypotheses**, each with the one observation that would confirm or kill it. Always include at least one non-code hypothesis (master data, configuration, permission, user process, other system).

## 2. Locate
- Resolve every work order with `wo_locate` → real TenantId and region (see [tenant_and_region.md](tenant_and_region.md)). Never take the tenant from the ticket's country.
- Find the code path: screen → endpoint → manager → SP/function → tables (`graph_search_text`, `graph_trace_endpoint`, `code_search` for exact names, i18n text, error strings).

## 3. Gather evidence: go to the mechanism
- **Read the code that produces the behaviour**: the SP body that computes the value, the screen code that hides the button, the condition that throws the error (`read_file`, `column_writers`). Do not guess what an SP does from its name.
- **Query the inputs** that the mechanism uses, for the concrete records in the ticket (`sql`, templates).
- **Compare with a working sibling**: another product in the same hierarchy, another tenant with the same feature, another work order in the same status that worked. A difference between broken and working is the strongest evidence there is.

## 4. Try to falsify your best hypothesis
Before concluding, ask: *"What would I see if I were wrong?"* and run that check. Examples:
- "Config is missing" → is it really missing for **this tenant**, and present for a tenant where it works?
- "Code defect" → does the code path actually run for this case (tenant branches, feature flags, user type lists)?
- "User error" → is the action actually possible for **this user type** in **this tenant**?

## 5. Stop rules
- Do **not** conclude `unknown`, and do not say "can't tell if code or config", while a relevant SP or screen is known but its source has not been read.
- 0 rows is a finding, not an answer: try alternative identifiers, the other region, or the input tables.
- Stop when the mechanism is explained by evidence, or when the remaining question can only be answered outside our systems (Atlas, SAP, business decision). Then say exactly what must be checked there and by whom.

## 6. Report
- **Root cause type + action** (two axes), confidence only as high as the executed evidence supports.
- **Mechanism in 2–4 sentences** with file:line / SP / table names.
- **Evidence list**: each item is an executed query or a read code location, with the key result.
- **Fix steps**: immediate workaround (no code), proper fix, and who decides (developer / business / other system team).
- If the case matches a known pattern, propose a card.
