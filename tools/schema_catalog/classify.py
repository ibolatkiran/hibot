"""Heuristic classification: backup/copy detection, domain tagging, PII columns."""
import re

BACKUP_SCHEMA_RE = re.compile(r'^(migrat|pkmigration|trash|datatransfer|import$)', re.I)
BACKUP_NAME_RES = [
    re.compile(r'(^|[_\-\.])(old|bak|bck|bkp|backup|yedek|copy|tmp|temp|test|archive|arsiv)([_\-\.\d]|$)', re.I),
    re.compile(r'(backup|yedek|_bak|bkp|_old$|_copy|copyof|_test|^tmp_|^temp_)', re.I),
    re.compile(r'^(Test|TEST|Tmp|TMP|Temp|TEMP|test|tmp|temp)(?![a-z])|(Test|TEST|Tmp|Temp)$'),
    re.compile(r'(?<!\d)(19|20)\d{6}(?!\d)'),          # 8 digit date (yyyymmdd)
    re.compile(r'(?<!\d)\d{8}(?!\d)'),                  # ddmmyyyy
    re.compile(r'(?<!\d)\d{6}(?!\d)'),                  # ddmmyy / yyyymm
    re.compile(r'(^|_)(migration|migrate)', re.I),
    re.compile(r'(Backup_?20\d\d)', re.I),
    re.compile(r'_\d{4}_\d{2}', re.I),
]


def is_backup_or_copy(key):
    schema, name = key.split('.', 1)
    if BACKUP_SCHEMA_RE.match(schema):
        return True
    return any(r.search(name) for r in BACKUP_NAME_RES)


# ---------------------------------------------------------------------------
# domains
# ---------------------------------------------------------------------------
# (domain, regex on lower 'schema.name').  First match wins.
DOMAIN_RULES = [
    ('logging_audit', r'^(log|util)\.|(^|\.)(.*(errorlog|dberror|auditlog|\blog\b|_log$|logs?$|history|audit|debug|trace|changelog|logged|requestlog|apilog|offlinetransaction|clientlog|diagnostic))'),
    ('job_scheduling', r'(jobrun|jobcontrol|^hangfire\.|\.job[a-z]*$|schedule[a-z]*job|\.jobs?[a-z_]*$|joblog|scheduledtask|jobparam)'),
    ('notification_sms_email', r'(mailgroup|mailproperties|mailtemp|announcement|supportinformation|smsemail|sms|email|notification|mailbox|outbox|push|whatsapp|inbox|^communication\.)'),
    ('integration_gateway', r'(^gateway\.|^sapint\.|^siriusint\.|^s4hana|^v2\.|^tradepartner\.|^exchangedocument|^fipark|restentries|rfccall|webservice|^monasys\.|apikey|ihp|\.interface|integration|postlog|^bulk\.|^caching\.)'),
    ('erp_sap_invoicing', r'(legalentity|shipto|soldto|erp|invoice|sapdoc|^salescontract|^salesdocument|billing|vatrate|taxnumber|\.tax|fidocument|^fipark|accounting|salesorder|purchaseorder|^s4hana)'),
    ('payment', r'(\.vat|revenue|costconfig|markup|glaccount|payment|^payment\.|collection|cashbox|bonus|compensation|discount|price|tariff|currency|installment|creditcard|posdevice|bank)'),
    ('survey_nps', r'(survey|nps|rating|feedback|questionnaire|satisfaction|checklist|\.fls|siriusquestion|siriusanswer|siriusresponse|anket)'),
    ('translation_i18n', r'(translation|^translation\.|language|apptranslation|\.localiz|appmessage|resource)'),
    ('user_auth', r'(siriususer|keyuser|\.user|employee|\brole|login|password|token|auth|permission|accessgroup|\.session|sysuser|\.menu|screenauth|rights|access)'),
    ('status_lookup', r'^catalog\.[a-z0-9]*(status|type|reason|priority|channel|source|category|level|kind)[a-z0-9]*$'),
    ('technician_scheduling', r'(workzone|servicearea|schedulingstatus|appointment|slot|schedule|route|\.van|capacity|workinghour|shift|leave|vacation|calendar|technician|engineer|workload|gps|geo|location|zone|skill)'),
    ('service_shop_partner', r'(retailer|serviceshop|distributor|partner|dealer|subcontract|supplier|vendor|authorizedservice|\.asc|asc[a-z]*$|tradepartner)'),
    ('material_spare_parts', r'(material|sparepart|inventory|stock|warehouse|\.part|bom|consumable|order.*material|requisition|shipment|cargo|delivery)'),
    ('product_catalog', r'(technology|refurbish|gradedetail|mdm|product|brand|model|defect|issue|hierarchy|serial|barcode|warranty|asset|appliance|\.symptom|commonissue)'),
    ('consumer_crm', r'(consumer|customer|contact|address|^globaladdress|^nzcontact|^aucontact|phone|crm|complaint|callcenter|call_center|outboundcall|campaign|consent)'),
    ('work_case', r'(workcase|\.case|callcase|claim)'),
    ('work_order', r'(verification|workorder|^operation\.|^workorder\.|^work\.|repair|visit|\.job$|service(request|call|order)|^verification\.|^warranty\.|^maintenance\.)'),
]
DOMAIN_RULES = [(d, re.compile(p, re.I)) for d, p in DOMAIN_RULES]

# schema-level defaults, applied after name rules do not match
SCHEMA_DEFAULTS = {
    'operation': 'work_order', 'workorder': 'work_order', 'work': 'work_order', 'verification': 'work_order',
    'consumer': 'consumer_crm', 'contact': 'consumer_crm', 'aucontact': 'consumer_crm', 'nzcontact': 'consumer_crm',
    'globaladdress': 'consumer_crm', 'product': 'product_catalog', 'asset': 'product_catalog',
    'material': 'material_spare_parts', 'inventory': 'material_spare_parts',
    'ihprussiamaterialorder': 'material_spare_parts', 's4hanamaterialorder': 'material_spare_parts',
    'gateway': 'integration_gateway', 'sapint': 'integration_gateway', 'siriusint': 'integration_gateway',
    'v2': 'integration_gateway', 'tradepartner': 'integration_gateway', 'monasys': 'integration_gateway',
    'bulk': 'integration_gateway', 'caching': 'integration_gateway', 'exchangedocument': 'integration_gateway',
    'exchange': 'integration_gateway', 'fipark': 'erp_sap_invoicing',
    's4hana': 'erp_sap_invoicing', 's4hanaexchange': 'erp_sap_invoicing', 's4hanagradechange': 'erp_sap_invoicing',
    's4hanapurchaseorder': 'erp_sap_invoicing', 'salescontract': 'erp_sap_invoicing', 'salesdocument': 'erp_sap_invoicing',
    'log': 'logging_audit', 'util': 'logging_audit', 'hangfire': 'job_scheduling',
    'communication': 'notification_sms_email', 'email2case': 'notification_sms_email',
    'payment': 'payment', 'translation': 'translation_i18n', 'sysuser': 'user_auth', 'aiprediction': 'other',
    'dashboard': 'other', 'config': 'other', 'common': 'other',
    'eg': 'country_specific', 'ph': 'country_specific', 'pakistan': 'country_specific', 'slovak': 'country_specific',
    'sariscz': 'country_specific', 'saristhai': 'country_specific', 'sarisza': 'country_specific',
    'sariseng': 'country_specific', 'southafrica': 'country_specific', 'ukr': 'country_specific',
    'unifyvietnam': 'country_specific', 'sairsza': 'country_specific', 'sairsthai': 'country_specific',
    'sariscek': 'country_specific', 'sarizcz': 'country_specific', 'sarisireland': 'country_specific',
}
# country schemas always win over name rules
COUNTRY_SCHEMAS = {'eg', 'ph', 'pakistan', 'slovak', 'sariscz', 'saristhai', 'sarisza', 'sariseng', 'southafrica',
                   'ukr', 'unifyvietnam', 'sairsza', 'sairsthai', 'sariscek', 'sarizcz', 'sarisireland', 'sarischn'}
# Catalog tables that must not be mis-tagged by loose rules
EXPLICIT = {
    'catalog.tenant': 'status_lookup', 'catalog.status': 'status_lookup', 'catalog.statusstep': 'status_lookup',
    'catalog.statustenant': 'status_lookup', 'catalog.apptranslationentry': 'translation_i18n',
    'catalog.apptranslation': 'translation_i18n', 'catalog.applanguage': 'translation_i18n',
    'catalog.employee': 'user_auth', 'catalog.role': 'user_auth', 'catalog.serviceshop': 'service_shop_partner',
    'catalog.country': 'other',
}


COUNTRY_NAME_RE = re.compile(r'(belgi|poland|polish|romania|ireland|irish|pakistan|ukrain|^ukr|ukrevenue|ukcurry|hongkong|^hk|moldova|china|bangladesh|banga|holland|netherlands|macedonia|serbia|thai|malaysia|malesia|slovenia|slovak|czech|egypt|russia|vietnam|_ro_|singapore)', re.I)


def classify_domain(key):
    low = key.lower()
    schema, name = low.split('.', 1)
    if schema in COUNTRY_SCHEMAS:
        return 'country_specific'
    if low in EXPLICIT:
        return EXPLICIT[low]
    if schema in ('dbo', 'catalog', 'bulk', 'sapint', 'dashboard') and COUNTRY_NAME_RE.search(name):
        return 'country_specific'
    # schema default for strongly purposed schemas
    if schema in ('log', 'util', 'hangfire', 'communication', 'email2case', 'gateway', 'sapint', 'siriusint', 'v2',
                  'payment', 'translation', 'sysuser', 'tradepartner', 'monasys', 'consumer', 'operation',
                  'workorder', 'work', 'product', 'material', 'inventory', 's4hana', 's4hanaexchange',
                  's4hanagradechange', 's4hanapurchaseorder', 'salescontract', 'salesdocument', 'fipark',
                  'verification', 'exchangedocument', 'contact', 'aucontact', 'nzcontact', 'globaladdress', 'asset'):
        # still let log/audit-like names inside operation/consumer etc. surface
        if schema in ('operation', 'consumer', 'product', 'material', 'inventory', 'workorder', 'work', 'verification',
                      'contact', 'asset', 'tradepartner', 'monasys'):
            for d, rx in DOMAIN_RULES[:3]:
                if rx.search(low):
                    return d
            if schema in ('operation', 'workorder', 'work', 'verification'):
                for d in ('technician_scheduling', 'work_case', 'material_spare_parts', 'consumer_crm', 'survey_nps',
                          'payment', 'erp_sap_invoicing'):
                    rx = dict(DOMAIN_RULES)[d]
                    if rx.search(name):
                        return d
        return SCHEMA_DEFAULTS[schema]
    for d, rx in DOMAIN_RULES:
        if rx.search(low):
            return d
    if schema in SCHEMA_DEFAULTS:
        return SCHEMA_DEFAULTS[schema]
    return 'other'


# ---------------------------------------------------------------------------
# PII
# ---------------------------------------------------------------------------
PII_RULES = [
    ('name', re.compile(r'^(first|last|sur|full|middle|contact|customer|consumer|owner|person|receiver|recipient|buyer|holder|cardholder|payer|requester|caller|user|login|mother|father)_?name$|^(name|surname|firstname|lastname|fullname)$|^(isim|ad|soyad|adsoyad)$', re.I)),
    ('phone', re.compile(r'phone|mobile|gsm|telephone|^tel(\d|no|nr|number)?$|cellular|fax|msisdn|whatsapp', re.I)),
    ('email', re.compile(r'e_?mail', re.I)),
    ('address', re.compile(r'address|street|^addr|streetname|housenumber|zipcode|postcode|postalcode|apartment|^district$|^neighbo', re.I)),
    ('iban_bank', re.compile(r'iban|bankaccount|accountnumber|cardnumber|creditcard|^pan$|cvv', re.I)),
    ('identity', re.compile(r'^tc(kn|kimlik|_?no|_?nr)?$|identity|nationalid|national_?id|passport|^ssn$|taxnumber|taxno|taxnr|vkn|^tckn|idnumber|idno|birth|dateofbirth|gender|^cpf$|^nric|^aadhaar|^pesel', re.I)),
]
FREE_TEXT_RE = re.compile(r'(request|response|body|payload|json|xml|rawdata|rawmessage|message|content|postdata|input|output|exception|stack)', re.I)
NOT_PII_NAME = re.compile(r'(table|schema|column|file|procedure|object|host|server|machine|param|field|product|model|brand|shop|company|material|status|type|role|group|city|country|key|class|app|device|path|domain|service|program)name$', re.I)


ID_SUFFIX_RE = re.compile(r'((?<=[a-z])Id|Ids|Type|Flag|Count|Format|Date|Status|Code)$')


def _is_big_text(sql_type):
    t = (sql_type or '').lower()
    if 'max' in t or t.startswith(('text', 'ntext', 'xml')):
        return True
    m = re.search(r'\((\d+)', t)
    return bool(m and int(m.group(1)) >= 1000 and t.startswith(('varchar', 'nvarchar')))


def pii_columns(cols, domain):
    out = []
    for c in cols:
        n = c['name']
        reason = None
        if NOT_PII_NAME.search(n) or ID_SUFFIX_RE.search(n):
            pass
        else:
            for tag, rx in PII_RULES:
                if rx.search(n):
                    reason = tag
                    break
            if not reason and domain == 'consumer_crm' and n.lower() in ('name', 'dsc'):
                reason = 'name'
        if not reason and FREE_TEXT_RE.search(n) and _is_big_text(c.get('sql_type')):
            reason = 'free_text_body'
        if reason:
            out.append({'name': n, 'kind': reason})
    return out


def build_domains(objects):
    tables = {}
    counts = {}
    for key, o in objects.items():
        if o['type'] != 'table' or o.get('is_backup_or_copy'):
            continue
        d = classify_domain(key)
        pii = pii_columns(o.get('columns', []), d)
        rec = {'domain': d, 'regions': o['regions']}
        if d == 'consumer_crm' or pii:
            rec['pii'] = True
        if pii:
            rec['pii_columns'] = pii
        tables[key] = rec
        counts[d] = counts.get(d, 0) + 1
    by_domain = {}
    for k, r in tables.items():
        by_domain.setdefault(r['domain'], []).append(k)
    return {
        'note': 'Heuristic domain tags (first matching rule wins; see tools/schema_catalog/classify.py). '
                'pii=true means the table is in consumer_crm or has pii_columns. Backup/copy tables excluded.',
        'counts': dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        'tables': tables,
        'by_domain': {d: sorted(v, key=str.lower) for d, v in sorted(by_domain.items())},
    }
