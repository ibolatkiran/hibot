---
skill: error_message_trace
keywords: error|exception|hata|unexpected|failed|not authorized|invalid|network error
required_evidence:
- id: error_text_search | description: Search the exact error text (and fragments without placeholders) in code / i18n / SP texts to find the throwing condition | satisfied_by: tool:graph_search_error_text ; tool:code_search
---
# A quoted error message: trace it to the throwing code

## Steps
1. **Exact text first.** Search the message (and fragments without placeholders) with `graph_search_error_text` and `code_search`:
   - frontend i18n values (`public/locales/*/*.json`) → key → `t('<key>')` in a view → screen;
   - backend C# literals (`ErrorDescription = "..."`, exceptions);
   - SP `RAISERROR` / `THROW` texts and trigger messages (e.g. "Failed to change status please check data!" is thrown by `TR_WorkOrder_U`, WorkOrder.sql:196).
   Production translations live in the DB (`Catalog.AppTranslation*`); if there is no hit in the repo, query the translation tables.
2. Generic texts ("Unexpected Error", "An unexpected error occurred.", "Network Error", "Failed with status code 500") come from global handlers (`ErrorHandlingMiddleware`, axios). Then you need the **failing endpoint**: find the screen/action the user describes → endpoint → manager → SP.
3. **Look for the error in the DB logs around the ticket time**: `db_errors_for_wo` (ErrorMessage LIKE 'WOId:<id>'), `db_errors_recent` (by procedure / time window), `V2.*` mobile logs (EU), `Operation.OfflineTransactionLog`.
4. Read the throwing condition and check the concrete record against it (which field or status violates the rule?).
5. Conclude with the exact condition, the record state that triggered it, and whether that state is wrong data or a wrong rule.
