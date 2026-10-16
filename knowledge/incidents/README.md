# HiBot incident knowledge base

Rule-based, deterministic (Python 3 stdlib only) processing of the 3790 Jira tickets in
`docs/kronik_sorunlar_son_1_yil_data.json` (key `tickets`). The legacy `clusters` of that file are keyword buckets with templated
root causes and are **ignored**.

## Run

```
cd d:\repos\HiBot
set PYTHONIOENCODING=utf-8
python tools/incidents/build.py            # ~15 s, byte-identical output on re-run
python -m unittest discover tools/incidents
# options: --dataset --jira-dir --out --codemap --edge-min --group-threshold
```

Code (`tools/incidents/`): `extract.py` (entities, country, app, errors, problem type, language, screen hints, PII scrub),
`similarity.py` (BM25 + grouping), `jira_xml.py` (Jira XML parser), `build.py` (entry point), `common.py`, tests + `testdata/`.

## Outputs

| file | content |
|---|---|
| `tickets_enriched.json` | `{meta, tickets[]}`; one record per ticket (below) |
| `groups.json` | `{meta, groups[]}` recurring problem groups (size >= 2) |
| `stats.md` | distributions, coverage percentages, top-30 groups |

### ticket record
`key, link, status, priority, created, service, sub_service` (original), `reporter_id` (R+sha1 prefix), `assignee_id` (A+...)
- names are never stored. Raw `summary`/`desc` are **not** stored either (they contain passwords, phones, mails); only
`text_clean.{summary,desc}` with placeholders `<EMAIL> <URL> <USER> <SECRET> <USERNAME> <PHONE> <PERSON> <USERID>`.
Legacy `cluster_*` fields are dropped.

- `entities`: `work_order_ids` (+ `work_order_ids_strong` = keyword evidence only), `work_case_ids`, `sku_codes` (10-digit models
  starting 7/8/9, 12-13 digit EAN-like), `material_codes` (`C` + 8 digits), `part_numbers` (`dddd-dddddddddd`), `sap_documents`
  (10-digit with SAP/FY/invoice/PO context), `error_texts` (quoted error-like messages, `Error:` fragments, exception names),
  `http_codes`.
- `country` (ISO-2 only when single), `countries`, `country_region` (`BALTICS`/`BALKANS`), `company_code` (AHVN, AHHK, AHSH->CN, AHTW,
  AHST->TH, AHTH, AHMY, AHSG, AHID), `country_source` = `title | desc | company_code | reporter_hint | jira_custom_field`.
- `app_guess`: `portal | crm | partner | technician | sirius_unknown | other:<manusoft|contact|salesforce|atlas|amazon_connect|saris|aws|iam> | unknown`;
  `app_source` (`sub_service`/`service`/`text`), `app_text_hint` (text suggests a different app; informational).
- `language`/`languages` (script counts + stopword lists: en, tr, ru, zh, th, ar, ro, pl, fr, de, es, pt, it).
- `problem_type`, `problem_confidence`, `problem_rule`, `problem_scores` (top-3 type scores).
- `screen_hints[]` (<= 3): `{app, screen, routes, score, raw_score, evidence[], endpoints[<=3]}`; `endpoints` come from
  `codemap/trace_index.json` (`by_screen`), ranked by word overlap with the ticket, so they are candidates, not proof.
- `similar[]` (top 5 `{key, score}`), `group_id` (null for singletons), `jira_enriched`, `desc_truncated`.
- when `jira_enriched`: `resolution, resolved, updated, issue_type, components, labels, custom_fields, comments[{author(pseudonym), created, text}]`,
  `comment_entities` (ids found in comments) - and the extraction/classification then runs on the **full** description.

## Taxonomy and rules (`extract.RULES`)
Rules are regexes on diacritic-folded lowercase text. Score per type = sum over its rules of weight x (1.5 if title hit) + weight x (1 if
description hit). Highest score wins, ties by the order below; confidence = f(margin to runner-up, absolute score); no hit = `other` (0.3).
1. `master_data_request` - missing/new SKU, model, product, tenant (`MM03`), BOM / part list, spare part not addable/visible, labour/price/tariff,
   defect/work/transport/reason codes, labour/defect not in a language, warranty type/period, new partner/ASC/customer/region, ASC assignment.
2. `access_request` - authorization/role/permission/yetki, create/disable user/account, login/password/Okta/locked.
3. `data_correction` - status change, cancel/reject/close WO, delete/erase/merge/correct, update serial/date/address/customer, re-assign.
4. `integration_failure` - SAP/S4/FY document/IDoc/Mule/EDI/interface/Sirius<->Salesforce, not posted/transferred, invoice cannot be created.
5. `notification_failure` - SMS, e-mail not received, notification, NPS survey send, OTP, push, WhatsApp.
6. `performance_availability` - slow, blank page, not loading, timeout, down/outage, 5xx, hang.
7. `mobile_offline` - offline, iOS/Android, tablet, technician app, APK, GPS.
8. `report_export` - report, export, Excel, download, dashboard, query.
9. `how_to_question` - how to, is it possible, clarification, training.
10. `functional_defect` - generic wrong behaviour (weight 1, so every specific type outranks it).
11. `other`.

## Screen hints
Ticket text is tokenised (folded) and every word n-gram (2-8 words) is looked up in an index of all en/tr i18n texts of the four apps
(`codemap/i18n_<app>.json`, texts with placeholders skipped) and in CamelCase-split screen/route names. Maximal matches only. A phrase adds
`words / sqrt(#screens using it)` to each screen, multiplied by `min(1, 40 / corpus_df)` (phrases occurring in many tickets, e.g. "work order",
carry no signal). Quoted error texts equal to a backend error message (`endpoint_downstream.error_messages`) add 2/sqrt(#screens). Only
screens with raw score >= 1.8 are kept (`score = 1-exp(-raw/3)`, >= 0.45); if the app is known only that app's screens are considered.
Ticket in a non-Sirius app gets no hints.

## Similarity and groups
Tokens: fold, `<wo>`/`<idN>`/`<mat>`/`<code>` placeholders for ids, en/tr/ro stopwords + boilerplate removed; summary counted twice + summary bigrams.
BM25 (k1=1.2, b=0.75). `sim(a,b) = (bm25(a->b)+bm25(b->a)) / (self(a)+self(b))`. Groups: average-link agglomerative clustering over pairs with
`sim >= 0.22` (`EDGE_MIN`), merging while the average pairwise sim (missing pair = 0) is `>= 0.30` (`GROUP_THRESHOLD`). Chosen by manual review of
~20 groups: 0.30 keeps near-duplicates ("Error - missing FY Document", "Missing Tenant for ...") together without chaining unrelated "add SKU" /
"access" tickets across apps; lower values start merging different problems of the same country. A group lists size, keys, representative
(highest total in-group similarity), dominant problem_type/app/country (with share), first/last seen, avg similarity, common terms,
`with_work_order_pct`.

## Jira XML enrichment
Drop Jira "Export -> XML" files into `docs/jira_export/raw/*.xml` (see `docs/jira_export/README.md`) and re-run `build.py`. `jira_xml.parse_file` reads
`<item>`: key, summary, type, priority, status, resolution, created/updated/resolved, assignee/reporter, HTML description -> text, `<component>`,
`<labels>`, ordered `<comments>` and `<customfields>` (name -> values). Truncated files yield their complete items plus a stderr warning. Matching
is by key; tickets without a match keep `jira_enriched: false`. Custom fields with personal data or ITSM-form boilerplate (name/e-mail/phone/
reporter/creator/message/SLA/...; values > 300 chars) are dropped; all text goes through the same PII scrub; comment authors are pseudonymised
(`U...`). A custom field `Country Code` fills the country when nothing else did. The real export format (checked on a 200-ticket chunk) works
with this parser; each ticket there carries ~100 custom fields of which most are empty/boilerplate.

## Limitations
- Text is truncated at 400 chars in the dataset unless Jira XML is present: WO lists, errors and screenshots-only tickets lose evidence.
- PII: known reporter/assignee full names, e-mails, URLs, phones, passwords after `password:`/`şifre`, user ids 26/92/93xxxxxx, Jira mentions are scrubbed.
  Free-text names in signatures ("Thanks, Christine"), customer names and unlabelled credentials are **not** reliably removed.
- Country is best-effort; ~53% undetected (many tickets name no market); `reporter_hint` (reporter's other tickets, >=70% one country, >=3 samples)
  is a guess. Short codes DE/AT/IT/IN are deliberately not used (ambiguous); cities are not mapped.
- `problem_type` is keyword-based: expect ~80% agreement with a human labeller; multi-topic tickets get one label; non-English texts rely on a few
  Turkish rules only (Russian/Chinese/Romanian/Polish parts are not understood unless the ticket is bilingual).
- Screen hints are conservative and still ~40% precise at top-1; they only cover tickets that quote UI texts or screen names. They point to candidates
  for the trace graph, not to a root cause.
- 8-digit "weak" WO ids (prefix 10-19/22 without keyword) can also be other ids (case/work-case numbers differ by customer); work cases (`20xxxxxx`) are separated.
- Groups are text-similarity clusters, not verified root causes; the legacy dataset clusters are not used.
