"""Report writers: lookups.json, diagnostic_tables.md, README.md, stats."""
import re
from collections import Counter, defaultdict

# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------
LOOKUP_NAME_RE = re.compile(r'(status|type|reason|priority|channel|tenant|category|level|source|kind|language|country|currency|cause|result|resolution|outcome|severity|origin|group)', re.I)


def build_lookups(objects):
    out = {}
    for key, o in objects.items():
        if o['type'] != 'table' or o.get('is_backup_or_copy') or not key.lower().startswith('catalog.'):
            continue
        cols = o.get('columns', [])
        names = [c['name'] for c in cols]
        low = {n.lower(): n for n in names}
        has_tr = 'apptranslationid' in low
        name = key.split('.', 1)[1]
        if not (has_tr or (LOOKUP_NAME_RE.search(name) and len(cols) <= 14)):
            continue
        if len(cols) > 25:
            continue
        idc = low.get('id') or (o['primary_key'][0] if o.get('primary_key') else None)
        namec = [low[n] for n in ('dsc', 'name', 'code', 'description', 'shortname', 'text') if n in low]
        rec = {'regions': o['regions'], 'id_column': idc, 'name_columns': namec,
               'translation_column': low.get('apptranslationid'), 'has_TenantId': o.get('has_TenantId', False),
               'columns': [c['name'] + ' ' + str(c['sql_type']) for c in cols], 'primary_key': o.get('primary_key', [])}
        if has_tr:
            rec['resolve_display_name'] = (
                "JOIN Catalog.AppTranslationEntry L ON L.AppTranslationId = X.%s AND L.LanguageId = @LanguageId -> L.FieldText"
                % low['apptranslationid'])
        elif namec:
            rec['resolve_display_name'] = 'Use column %s directly (no AppTranslationId).' % namec[0]
        out[key] = rec
    return {
        'note': 'Lookup/status tables in schema Catalog (heuristic: has AppTranslationId, or name looks like '
                'status/type/reason/priority/channel/tenant... and <=14 columns). Data rows are NOT in the SSDT project.',
        'translation': {
            'table': 'Catalog.AppTranslationEntry',
            'columns': [c['name'] + ' ' + str(c['sql_type']) for c in objects.get('Catalog.AppTranslationEntry', {}).get('columns', [])],
            'languages_table': 'Catalog.AppLanguage (Id, Code)',
            'pattern': "SELECT L.FieldText FROM <lookup> X JOIN Catalog.AppTranslationEntry L WITH (NOLOCK) "
                       "ON L.AppTranslationId = X.AppTranslationId AND L.LanguageId = @LanguageId",
            'example': 'dbo.GetSiriusWorkOrderHistory resolves the status name as Catalog.Status S -> AppTranslationEntry '
                       '(S.AppTranslationId, @LanguageId) -> L.FieldText',
        },
        'tables': out,
    }


# ---------------------------------------------------------------------------
# diagnostic tables
# ---------------------------------------------------------------------------
DIAG_GROUPS = [
    ('Error / exception logs', [
        ('Log.DB_Errors', 'Written by Util.AddErrorLog from CATCH blocks. No WorkOrderId column: correlate via ErrorMessage text '
                          "(`ErrorMessage LIKE 'WOId:%'` / `'%<id>%'`) and ErrorProcedure + ErrorDateTime window. UserName = caller."),
    ]),
    ('Work order change / status history', [
        ('Log.WorkOrderJsonLog', "Written by trigger TR_WorkOrder_U on Operation.WorkOrder (one row per changed single-row update). "
                                 "Join: `WorkOrderJsonLog.WorkOrderId = Operation.WorkOrder.Id`. New/old row as JSON; read by dbo.getChangesByWorkOrderId."),
        ('Operation.WorkOrderGpsInfo', "NOT GPS-only: this is the WORK ORDER STATUS HISTORY (one row per status transition). "
                                       "Join: `WorkOrderGpsInfo.WorkOrderId = Operation.WorkOrder.Id`; `StatusId -> Catalog.Status.Id` "
                                       "(display via AppTranslationEntry); `EmployeeId -> Catalog.Employee.Id`; Comment = status note. "
                                       "Used by dbo.GetSiriusWorkOrderHistory."),
        ('dbo.StatusUpdaterLog', "EU only. One row per automatic status change made by dbo.StatusUpdater (OldStatusId -> NewStatusId, Message). Join `WorkOrderId = Operation.WorkOrder.Id`; status names via Catalog.Status -> AppTranslationEntry. Use LogDate (UTC)."),
    ]),
    ('Integration / API call logs', [
        ('Log.RESTEntries', "Outbound REST call journal. `Key`/`Value` is a generic key pair (Value usually the id, e.g. Key='WorkOrderId' / document no - verify by sampling; index on Value+RequestUrl+Result+Timestamp makes Value lookups cheap). `Result` bit = success, `TryCount` = retries, StatusCode = HTTP code. Request/ResponseBody may contain PII."),
        ('Log.RFCCalls', "SAP RFC call journal written by Log.InsertRFCCalls. `Id_Key`/`Id_Value` identify the business object (Id_Value int is probably the WorkOrder/document id, Id_Key names the type - verify by sampling); `Status` bit = success; Request/Response are payloads (PII risk). No TenantId."),
        ('Gateway.PostLog', "Written by Gateway.Post: raw inbound/outbound package (`PackageName`, `PackageBody`). No WorkOrderId column: search `PackageBody LIKE '%<id>%'` within a CreateDate window."),
        ('Gateway.PostErrorLog', "Failed Gateway packages with `ErrorText` (written by many Gateway.* procs). No WorkOrderId column: search `PackageBody`/`ErrorText` LIKE '%<id>%' within a CreateDate window."),
        ('Gateway.Notification', "Generic gateway notification queue (`ProcedureName` = data procedure to call, `GetData`, `Retry`, `ErrorText`). No WorkOrderId column: `RecipientId` is the external recipient key; search `GetData`/`ErrorText` for the id. Rising `Retry` = stuck message."),
        ('Gateway.CaseNotification', "Outbound case/work-order notification rows to CRM (CaseId = Operation.WorkCase.Id, ServicePaperId = work order/service paper id -> Operation.WorkOrder.Id, `Retry`, `NotificationDate`). Wide denormalised snapshot."),
    ]),
    ('ERP / SAP documents', [
        ('Operation.ErpDocument', "ERP document header (costs, DocumentNr/InvoiceNr, ErrorDescription, StatusId). NO WorkOrderId column: link via `Operation.WorkOrderErpDetails.ErpDocumentId = ErpDocument.Id` then `WorkOrderErpDetails.WorkOrderId`. ErpDocument.StatusId -> Catalog.ErpDocumentStatus."),
        ('Operation.WorkOrderErpDetails', "Link table work order <-> ERP document: `WorkOrderId`, `ErpDocumentId -> Operation.ErpDocument.Id`, `Type` (document type), `DocumentNr`, `StatusId` (Catalog.ErpDocumentStatus), `ErrorDescription` = SAP error text. Primary place to see why a WO did not post to SAP."),
        ('Operation.WorkOrderErpDetailsFi', "FI (financial document) number per work order: `WorkOrderId`, `FiDocumentNr`, `StatusId`, `ErrorDescription`."),
        ('Operation.WorkOrderErpDetailsMm', "MM (material document) number per work order: `WorkOrderId`, `MmDocumentNr`, `StatusId`, `ErrorDescription`."),
    ]),
    ('Jobs', [
        ('SiriusInt.JobRunLog', "EU+APAC. Job run journal (JobName, StartTime, EndTime, Status, ErrorMessage); no work order column. Written only by SiriusInt.SAPtoSiriusMasterJob. Other jobs: HangFire.* tables, Log.JOBControl."),
        ('SiriusInt.SiriusIntLog', "SiriusInt master-data import log (`ProcedureName`, `ErrorText`); no work order column, no PK."),
    ]),
    ('SMS / e-mail / notifications', [
        ('Operation.SmsEmailSendingData', "Older/Operation-schema SMS/e-mail queue: `WorkOrderId`, `SendType`, `SendingAddress` (PII), `SendingMessage`, `SendingStatus`, QDate/SentDate. Written by dbo.SetSmsEmailSendingData."),
        ('Communication.SmsEmailSendingData', "Current SMS/e-mail pool (written by Communication.* prepare jobs and trigger Trg_WorkOrderNotificationConsumer_Insert): per-WO rows: `WorkOrderId`, `SendType`, `CommunicationType`, `SendingStatus`, provider reply in `SendingResponse`, QDate (queued) / SentDate. SendingMessage/SendingAddress are PII."),
        ('Email2Case.OutboxMessage', "Outbound mail queue for the Email2Case flow (written by Gateway.InsertEmailInfo / dbo.SetWorkOrderUpdateHeader). Contents are PII."),
    ]),
    ('Mobile / API client diagnostics', [
        ('Operation.OfflineTransactionLog', "EU only. Offline sync transaction journal from technician app (Request/Response payloads, PII risk). Join WorkOrderId."),
        ('V2.MobileOperationLog', "EU only. V2 mobile operations per work order, ordered by `Sequence`; `ClientOperationId` unique = idempotency key. Join WorkOrderId + TenantId."),
        ('V2.ApiRequestAudit', "EU only. Audit of every V2 API request: `CorrelationId`, `Endpoint`, `HttpMethod`, `RequestPayload`/`ResponsePayload` (PII risk), `StatusCode`, `DurationMs`, `CreatedAt`. NOTE `TenantId` and `UserId` are NVARCHAR here (not int). No WorkOrderId column: filter by Endpoint (path often contains the id) or payload LIKE."),
        ('V2.ClientLog', "EU only. Mobile app log lines: `Level`, `Category`, `EventName`, `Message`, `StackTrace`, `WorkOrderId`, `EmployeeId`, `Endpoint`/`HttpStatusCode`, Request/ResponsePayload (PII risk), ClientCreatedAt vs ServerReceivedAt."),
        ('dbo.DiagnosticHistory', "EU only. Findings written by dbo.Diag_LabourMissing / dbo.Diag_MaterialMissing diagnostic procs per work order."),
    ]),
    ('Status / translation / tenant lookups', [
        ('Catalog.AppTranslationEntry', "Display text: `AppTranslationId` + `LanguageId` -> `FieldText`."),
        ('Catalog.Status', "Work order status master; `AppTranslationId` -> display text; `MobileStatusId` maps to mobile status."),
        ('Catalog.StatusStep', "Allowed status transitions / flow steps."),
        ('Catalog.StatusTenant', "Per-tenant status mapping (LocalId, MobileStatusId)."),
        ('Catalog.Tenant', "Tenant (country/company) master; `ShowUTCHour` = UTC offset used for display."),
    ]),
]


def _col_line(c):
    bits = [str(c['sql_type'])]
    bits.append('NULL' if c.get('nullable') else 'NOT NULL')
    if c.get('identity'):
        bits.append('IDENTITY')
    if c.get('default'):
        bits.append('DEFAULT ' + str(c['default']))
    d = c.get('description')
    return '`%s` %s%s' % (c['name'], ' '.join(bits), (' -- ' + d[:100]) if d else '')


ALIASES = {'Operation.WorkOrderErpDetailsFi': 'Operation.WorkOrderErpFi',
           'Operation.WorkOrderErpDetailsMm': 'Operation.WorkOrderErpMm'}


def _find(objects, key):
    if key in objects:
        return key
    low = key.lower()
    for k in objects:
        if k.lower() == low:
            return k
    return None


def _join_hint(o):
    names = {c['name'].lower(): c['name'] for c in o.get('columns', [])}
    hints = []
    for cand in ('workorderid', 'workorder_id', 'woid'):
        if cand in names:
            hints.append('`%s` = Operation.WorkOrder.Id' % names[cand])
    for cand in ('workcaseid', 'caseid'):
        if cand in names:
            hints.append('`%s` = Operation.WorkCase.Id' % names[cand])
    if 'tenantid' in names:
        hints.append('`TenantId` -> Catalog.Tenant.Id (filter by tenant)')
    for cand in ('key', 'keyvalue', 'value', 'objectid', 'referenceid', 'documentid', 'externalid'):
        if cand in names:
            hints.append('`%s` may carry the WorkOrder id / external key (verify by sampling)' % names[cand])
    for cand in ('errormessage', 'message', 'requestbody', 'request', 'response', 'logtext', 'description', 'text'):
        if cand in names:
            hints.append("free text `%s`: search with `LIKE 'WOId:%%'` / `LIKE '%%<id>%%'`" % names[cand])
    return hints


def write_diagnostic_md(path, objects, domains):
    missing = []
    L = ['# Diagnostic / audit / integration tables',
         '',
         'Generated by `tools/schema_catalog/build.py` from the SSDT projects (EU = superset, APAC subset). '
         'Column lists are exact (parsed from CREATE TABLE). Join hints in the first line of each table are curated; '
         '"auto join candidates" are derived from column names. Row data is not in the SSDT projects.',
         '',
         '## How to correlate with a Work Order',
         '',
         '1. Direct id: tables with `WorkOrderId` -> `Operation.WorkOrder.Id` (e.g. Log.WorkOrderJsonLog, Operation.WorkOrderGpsInfo).',
         '2. Key/Value style tables (Log.RESTEntries, Gateway.*): the work order id is in a key column or inside the request/response text.',
         "3. Error log: `Log.DB_Errors.ErrorMessage LIKE 'WOId:%<id>%'` (procedures put `WOId:<id>` in the message when calling Util.AddErrorLog), "
         'then narrow with `ErrorDateTime` and `ErrorProcedure`.',
         '4. Always filter by `TenantId` where the table has it (multi-tenant DB).',
         '5. Status names: `Catalog.Status.AppTranslationId` + `LanguageId` -> `Catalog.AppTranslationEntry.FieldText`.',
         '']
    summary = []
    for group, items in DIAG_GROUPS:
        L.append('## ' + group)
        L.append('')
        for key, note in items:
            k = _find(objects, key)
            if not k and _find(objects, ALIASES.get(key, '')):
                k = _find(objects, ALIASES[key])
                missing.append('%s (requested name does not exist; actual table is %s)' % (key, k))
                note = 'REQUESTED NAME `%s` DOES NOT EXIST - this is the actual table. ' % key + note
            elif not k:
                missing.append(key)
                L.append('### %s' % key)
                L.append('')
                L.append('**NOT FOUND in either project.**  Closest names: %s' % (
                    ', '.join(sorted(x for x in objects if x.split('.', 1)[1].lower() == key.split('.', 1)[1].lower()
                                     or key.split('.', 1)[1].lower() in x.lower())[:8]) or 'none'))
                L.append('')
                continue
            o = objects[k]
            L.append('### %s' % k)
            L.append('')
            L.append('- Type: %s | Regions: %s%s' % (o['type'], '+'.join(o['regions']),
                                                       '' if o.get('region_identical', True) else ' (definitions differ between regions)'))
            L.append('- Source: ' + '; '.join('%s `%s`' % (r, p) for r, p in o['source_files'].items()))
            L.append('- Notes: ' + note)
            if o['type'] != 'table':
                L.append('- (not a table)')
                L.append('')
                continue
            L.append('- PK: %s | has TenantId: %s' % (', '.join(o.get('primary_key') or []) or 'none', o.get('has_TenantId')))
            hints = _join_hint(o)
            if hints:
                L.append('- Auto join candidates: ' + '; '.join(hints))
            if o.get('eu_only_columns') or o.get('apac_only_columns'):
                L.append('- Region column differences: EU-only %s; APAC-only %s' % (o.get('eu_only_columns', []), o.get('apac_only_columns', [])))
            if o.get('indexes'):
                L.append('- Indexes: ' + '; '.join('%s(%s)' % (i['name'], ', '.join(i['columns'])) for i in o['indexes'][:8]))
            if o.get('triggers'):
                L.append('- Triggers: ' + '; '.join('%s [%s]' % (t['name'], ','.join(t['events'])) for t in o['triggers']))
            if o.get('written_by'):
                wb = o['written_by']
                L.append('- Written by (%d): %s%s' % (len(wb), ', '.join(wb[:8]), ' ...' if len(wb) > 8 else ''))
            if o.get('referenced_by'):
                rb = [x for x in o['referenced_by'] if x not in o.get('written_by', [])]
                if rb:
                    L.append('- Read by (%d): %s%s' % (len(rb), ', '.join(rb[:6]), ' ...' if len(rb) > 6 else ''))
            L.append('- Columns:')
            for c in o['columns']:
                L.append('  - ' + _col_line(c))
            L.append('')
    if missing:
        L.append('## Not found')
        L.append('')
        for m in missing:
            L.append('- ' + m)
        L.append('')
    # other log-ish tables not already listed (discovery aid)
    listed = {_find(objects, k) for _, items in DIAG_GROUPS for k, _ in items}
    extra = [k for k, d in domains['tables'].items() if d['domain'] in ('logging_audit', 'integration_gateway', 'job_scheduling')
             and k not in listed and re.search(r'log|history|audit|error|queue|notification|message|job|debug|outbound|post|request|package|retry', k, re.I)]
    L.append('## Other logging / integration / job tables (names only; see domains.json)')
    L.append('')
    L.append(', '.join('`%s`' % k for k in extra))
    L.append('')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L))
    return missing


# ---------------------------------------------------------------------------
# stats / readme
# ---------------------------------------------------------------------------
def compute_stats(objects, domains, scanned, missing):
    by_type = Counter(o['type'] for o in objects.values())
    by_region = Counter('+'.join(o['regions']) for o in objects.values())
    type_region = defaultdict(Counter)
    for o in objects.values():
        for r in o['regions']:
            type_region[o['type']][r] += 1
    backups = sum(1 for o in objects.values() if o.get('is_backup_or_copy'))
    tables = [o for o in objects.values() if o['type'] == 'table']
    trg = sum(len(o.get('triggers', [])) for o in tables)
    warn = [k for k, o in objects.items() if o.get('parse_warning')]
    notable = [k for k, o in objects.items() if o['type'] == 'table' and not o.get('columns')]
    pii_tables = sum(1 for d in domains['tables'].values() if d.get('pii'))
    return {'summary': {'objects': len(objects), 'by_type': dict(by_type), 'by_region_presence': dict(by_region),
                        'by_type_and_region': {t: dict(c) for t, c in type_region.items()},
                        'backup_or_copy': backups, 'tables_with_tenantid': sum(1 for o in tables if o.get('has_TenantId')),
                        'triggers': trg, 'domain_counts': domains['counts'], 'pii_tables': pii_tables,
                        'parse_warnings': warn[:20], 'tables_without_columns': notable[:20]},
            'missing': missing, 'skipped': {r: dict(scanned[r][2]) for r in scanned}}


def write_readme(path, stats, args):
    s = stats['summary']
    L = ['# HiBot DB schema catalog',
         '',
         'Machine-readable catalog of the Sirius DB, generated from the two SSDT SQL projects (read-only):',
         '',
         '- EU: `%s` (superset)' % args.eu,
         '- APAC: `%s`' % args.apac,
         '',
         '## Regenerate',
         '',
         '```',
         'cd d:\\repos\\HiBot',
         'set PYTHONIOENCODING=utf-8',
         'python tools/schema_catalog/build.py [--eu PATH] [--apac PATH] [--out PATH]',
         '```',
         '',
         'Python 3 stdlib only; deterministic (sorted output). Code: `tools/schema_catalog/{build,sqlcat,classify,reports}.py`.',
         '',
         '## Files',
         '',
         '| File | Content |', '|---|---|',
         '| `objects.json` | One record per object keyed `Schema.Name` (case-insensitive merge of EU+APAC). |',
         '| `domains.json` | Domain tag per non-backup table + `pii_columns`; `by_domain` index. |',
         '| `diagnostic_tables.md` | Logging/audit/integration/job/status-history tables: exact columns, join hints, regions. |',
         '| `lookups.json` | Catalog status/lookup tables and how to resolve display names via `Catalog.AppTranslationEntry`. |',
         '| `security_principals.json` | Names only of roles/users from Security folders (no contents; e-mail-named users counted, not listed). |',
         '',
         '## objects.json record format',
         '',
         'Common: `type` (table/view/procedure/function/udt/synonym/sequence), `regions` (["EU","APAC"]), `source_files` {region: path}, '
         '`region_identical` (normalized text equal in both regions; absent when only in one), `is_backup_or_copy`, `line_count`, '
         '`referenced_by`.',
         '',
         '- table: `columns` [{name, sql_type, nullable, identity, default, description?}], `primary_key`, `indexes` [{name, columns, unique, clustered, include?, filter?}], '
         '`foreign_keys`, `triggers` [{name, events, timing, disabled, called_objects, written_tables, called_procs, raises_errors}], '
         '`trigger_calls`, `has_TenantId`, `written_by` (procs/triggers that INSERT/UPDATE/DELETE/MERGE it), `description`, `eu_only_columns`/`apac_only_columns`.',
         '- procedure/function/view: `parameters` [{name, sql_type, default, output?}], `referenced_objects` (ALL known objects mentioned, includes written tables and called procs), '
         '`written_tables`, `called_procs`, `raises_errors` [{kind, message, severity|number}] (literal text; `{@var}` = interpolated expression), '
         '`writes_error_log`, `dynamic_sql_refs` (known objects named inside string literals only), `returns` (functions), `eu_only_refs`/`apac_only_refs`.',
         '- Bodies are NOT stored; open `source_files`.',
         '',
         '## Stats (this run)',
         '',
         '- Objects: %d; by type: %s' % (s['objects'], s['by_type']),
         '- By region presence: %s' % s['by_region_presence'],
         '- Per type and region: %s' % s['by_type_and_region'],
         '- Backup/copy flagged (heuristic): %d; tables with TenantId: %d; triggers: %d' % (s['backup_or_copy'], s['tables_with_tenantid'], s['triggers']),
         '- Domains (non-backup tables): %s' % s['domain_counts'],
         '- Tables flagged PII: %d' % s['pii_tables'],
         '- Diagnostic tables not found: %s' % (stats['missing'] or 'none'),
         '- Skipped folders (unknown kinds): %s' % stats['skipped'],
         '',
         '## Known limitations of the regex parsing',
         '',
         '- Not a real T-SQL parser. Comments and string literals are stripped before reference extraction; objects are matched only if the '
         '`schema.name` token (bracketed or not) equals a known file-derived object. Unqualified names resolve to `dbo` only.',
         '- Dynamic SQL (`EXEC(@sql)`, `sp_executesql`) is not analysed; known names inside string literals are listed separately as `dynamic_sql_refs`.',
         '- Aliased column references like `T.Col` never match because `T.Col` is not a known object; but an alias identical to a schema name, or a column named like an object, can produce false positives.',
         '- `referenced_objects` does not distinguish reads from writes (use `written_tables`). Writes via `UPDATE alias SET ... FROM table alias` are resolved only when the alias is declared in a FROM/JOIN.',
         '- Writes to temp tables, table variables, synonyms (resolved only to the synonym key) and cross-database/linked-server objects are not tracked.',
         '- RAISERROR/THROW: only the message expression is captured; format args and non-literal messages appear as `{@var}`.',
         '- `is_backup_or_copy` and domains are name heuristics and will have false positives/negatives; domain precedence is first-match.',
         '- Triggers: embedded CREATE TRIGGER batches; DISABLE TRIGGER statements detected in same file.',
         '- Computed columns have sql_type `computed`; user-defined types are kept as written.',
         '- When EU and APAC definitions differ, the EU parse is used (APAC only for APAC-only objects); differences summarised in `eu_only_*`/`apac_only_*`.',
         '- Row data (lookup values, tenants) is not available: only structure.',
         ]
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L) + '\n')
