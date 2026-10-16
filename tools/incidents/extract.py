"""Per-ticket entity extraction (regex / dictionaries, rule based, deterministic).

Public API:
    Extractor(person_names=[...], screen_matcher=None).extract(ticket_dict) -> dict
    ScreenMatcher(codemap_dir)       -> .match(text, app, error_texts) -> list of hints
    scrub(text, persons_re)          -> text with PII replaced by placeholders
Everything is documented in knowledge/incidents/README.md.
"""
import math
import os
import re
from collections import Counter, defaultdict

from common import CODEMAP, fold, load_json, words

# --------------------------------------------------------------------------- PII scrubbing
RE_URL = re.compile(r"https?://[^\s|\]\)>\"']+", re.I)
RE_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
RE_MENTION = re.compile(r"\[~[^\]\s]+\]")
RE_SECRET = re.compile(r"(?i)\b(password|passwd|pwd|pass|sifre|şifre|parola|parolă|пароль)\b(\W{0,3}?[:=]\W{0,3}?)([^\s*]+)")
RE_SECRET_TR = re.compile(r"(?i)\b((?:şifre|sifre|parola|password)\w*\s+)(\S+)(\s+(?:olarak|is|olsun))")
RE_USERNAME = re.compile(r"(?i)\b(user ?name|user ?id|kullan[ıi]c[ıi] ad[ıi]|login|логин)\b(\W{0,3}?[:=]\W{0,3}?)([^\s,;*]+)")
RE_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d[\d ()\-]{7,}\d|\(?0\d{2,4}\)?[ -]\d{3}[ -]?\d{2}[ -]?\d{2}|0\d{3}[ -]?\d{3}[ -]?\d{4}"
    r"|\d{3}[ -]\d{3}[ -]\d{4}(?!\d))(?![\w+|])")
RE_USERID = re.compile(r"(?<![\w.~-])(?:26|92|93)\d{6}(?![\w.-])")


def build_person_re(names):
    """Regex matching known reporter / assignee full names."""
    parts = set()
    for n in names:
        n = re.sub(r"\s+", " ", (n or "").strip())
        if len(n) >= 5 and " " in n:
            parts.add(n)
    if not parts:
        return None
    alt = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in sorted(parts, key=lambda x: (-len(x), x)))
    return re.compile(r"(?i)(?<!\w)(?:" + alt + r")(?!\w)")


def scrub(text, persons_re=None):
    """Replace PII with placeholders: <EMAIL> <URL> <USER> <SECRET> <USERNAME> <PHONE> <PERSON> <USERID>."""
    if not text:
        return ""
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = RE_EMAIL.sub("<EMAIL>", t)
    t = RE_URL.sub("<URL>", t)
    t = RE_MENTION.sub("<USER>", t)
    t = RE_SECRET.sub(lambda m: m.group(1) + m.group(2) + "<SECRET>", t)
    t = RE_SECRET_TR.sub(lambda m: m.group(1) + "<SECRET>" + m.group(3), t)
    t = RE_USERNAME.sub(lambda m: m.group(1) + m.group(2) + "<USERNAME>", t)
    t = RE_PHONE.sub("<PHONE>", t)
    if persons_re is not None:
        t = persons_re.sub("<PERSON>", t)
    t = RE_USERID.sub("<USERID>", t)
    t = re.sub(r"[ \t  ]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# --------------------------------------------------------------------------- work order / SKU / SAP ids
WO_KW = re.compile(
    r"(?:\bw/?\.?o(?![a-z])\.?|\bwo\s*#|work\s*-?orders?|workorders?|is emri|isemri|is emir|fis\b|servis\s*(?:no|numara)|"
    r"service\s*(?:no|number|order)|case\s*(?:no|number|nr|id)|job\s*(?:no|number)|\borders?\b(?:\s*(?:no|number|nr|id))?|"
    r"zlecenie|comanda|заказ-наряд|заявк|наряд)")
WC_KW = re.compile(r"(?:\bwc\b|work\s*-?case|workcase|is talebi|is talep)")
NOT_WO_BEFORE = re.compile(r"(?:purchase|sales|po|production|material|stock|transfer|customer|return)\s*(?:order)?\W*$")
SAP_KW = re.compile(r"(?:\bsap\b|\bfy\b|document|dokuman|belge|invoice|fatura|billing|\bpo\b|purchase|sales order|\bdoc\b|\bbp\b|"
                    r"order no|accounting|fi doc|mm03)")
LIST_GAP = re.compile(r"^[\s,;/&*\-]*(?:and|ve|und|i)?[\s,;/&*\-]*$")
WO_PREFIXES = {"10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "22"}

RE_NUM = re.compile(r"(?<![\w~<-])(?<!\d[.,])(\d{7,13})(?![\w]|[.,]\d)")
RE_MATERIAL = re.compile(r"(?<![\w])(C\d{8})(?!\w)")
RE_PARTNO = re.compile(r"(?<![\w])(\d{4}-\d{10})(?!\w)")


def _uniq(x):
    seen, out = set(), []
    for i in x:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def extract_ids(text):
    """Return work_order_ids (+strong subset), work_case_ids, sku_codes, material_codes, part_numbers, sap_documents.

    Rules (documented in README): 7-9 digit number preceded (<=22 chars) by a WO keyword = strong WO; a number following a
    strong WO separated only by separators/'and' = strong (list); a bare 8-digit number with prefix 10-19/22 = weak WO
    (prefix-range); 8-digit starting 20 or preceded by WC/work case = work case id; 10-digit with SAP-ish keyword = SAP
    document, otherwise 10-digit starting 7/8/9 = SKU/model, 12-13 digit = EAN/SKU; C+8 digits = SAP material code.
    """
    text = re.sub(r"(?i)\b(w/?o|wc)(?=\d{7})", r"\1 ", text)   # "WO22767569" -> "WO 22767569"
    wo, wo_strong, wc, sku, sap = [], [], [], [], []
    last_end = -100
    for m in RE_NUM.finditer(text):
        num = m.group(1)
        n = len(num)
        before = fold(text[max(0, m.start() - 32):m.start()])
        near = before[-22:]
        if n in (7, 8, 9):
            if re.fullmatch(r"20[1-3]\d[01]\d[0-3]\d", num):  # yyyymmdd
                continue
            kw_wo = list(WO_KW.finditer(near))
            kw_wc = list(WC_KW.finditer(near))
            if kw_wc and (not kw_wo or kw_wc[-1].end() >= kw_wo[-1].end()):
                wc.append(num)
                last_end = -100
                continue
            strong = bool(kw_wo) and not NOT_WO_BEFORE.search(before[-26:])
            if not strong and wo_strong and m.start() - last_end <= 8 and LIST_GAP.match(text[last_end:m.start()].lower()):
                strong = True
            if strong:
                wo.append(num)
                wo_strong.append(num)
                last_end = m.end()
            elif n == 8 and num[:2] in WO_PREFIXES:
                wo.append(num)
                last_end = -100
            elif n == 8 and num[:2] == "20":
                wc.append(num)
                last_end = -100
        elif n == 10:
            if SAP_KW.search(near):
                sap.append(num)
            elif num[0] in "789":
                sku.append(num)
            elif num[:2] in ("10", "11", "18", "19", "49", "50", "51") and re.search(r"order|po\b", near):
                sap.append(num)
        elif n in (12, 13):
            sku.append(num)
    return {
        "work_order_ids": _uniq(wo), "work_order_ids_strong": _uniq(wo_strong), "work_case_ids": _uniq(wc),
        "sku_codes": _uniq(sku), "material_codes": _uniq(RE_MATERIAL.findall(text)),
        "part_numbers": _uniq(RE_PARTNO.findall(text)), "sap_documents": _uniq(sap),
    }


# --------------------------------------------------------------------------- country
COUNTRY_NAMES = {
    "poland": "PL", "polska": "PL", "polonya": "PL", "polish": "PL", "romania": "RO", "romanya": "RO", "romanian": "RO",
    "romana": "RO", "moldova": "MD", "moldavia": "MD", "serbia": "RS", "srbija": "RS", "sirbistan": "RS",
    "bosnia": "BA", "bosna": "BA", "croatia": "HR", "hrvatska": "HR", "hirvatistan": "HR", "slovenia": "SI",
    "slovenija": "SI", "slovakia": "SK", "slovak": "SK", "slovensko": "SK", "slovakya": "SK", "czech": "CZ",
    "czechia": "CZ", "cesko": "CZ", "macedonia": "MK", "makedonya": "MK", "montenegro": "ME", "karadag": "ME",
    "ukraine": "UA", "ukrayna": "UA", "russia": "RU", "rusya": "RU", "kazakhstan": "KZ", "kazakistan": "KZ",
    "uzbekistan": "UZ", "ozbekistan": "UZ", "kyrgyzstan": "KG", "kirgizistan": "KG", "belarus": "BY", "portugal": "PT",
    "portekiz": "PT", "spain": "ES", "ispanya": "ES", "italy": "IT", "italya": "IT", "germany": "DE", "almanya": "DE",
    "austria": "AT", "avusturya": "AT", "hungary": "HU", "macaristan": "HU", "bulgaria": "BG", "bulgaristan": "BG",
    "greece": "GR", "yunanistan": "GR", "malta": "MT", "morocco": "MA", "maroc": "MA", "egypt": "EG",
    "misir": "EG", "turkey": "TR", "turkiye": "TR", "australia": "AU", "new zealand": "NZ", "pakistan": "PK",
    "india": "IN", "thailand": "TH", "tayland": "TH", "vietnam": "VN", "malaysia": "MY", "singapore": "SG",
    "indonesia": "ID", "taiwan": "TW", "hong kong": "HK", "china": "CN", "shanghai": "CN", "japan": "JP",
    "united kingdom": "GB", "england": "GB", "ireland": "IE", "france": "FR", "netherlands": "NL", "belgium": "BE",
    "sweden": "SE", "norway": "NO", "denmark": "DK", "finland": "FI", "switzerland": "CH", "south africa": "ZA",
    "lithuania": "LT", "latvia": "LV", "estonia": "EE", "azerbaijan": "AZ", "armenia": "AM",
    "albania": "AL", "kosovo": "XK", "south korea": "KR", "philippines": "PH", "saudi arabia": "SA", "jordan": "JO",
    "iraq": "IQ", "lebanon": "LB", "tunisia": "TN", "algeria": "DZ", "cyprus": "CY", "israel": "IL",
}
COUNTRY_REGIONS = {"baltics": ["LT", "LV", "EE"], "baltic": ["LT", "LV", "EE"], "balkan": ["RS", "HR", "SI", "MK", "BA", "ME"],
                   "balkans": ["RS", "HR", "SI", "MK", "BA", "ME"]}
REGION_LABEL = {"baltics": "BALTICS", "baltic": "BALTICS", "balkan": "BALKANS", "balkans": "BALKANS"}
COUNTRY_CODES = {"UA": "UA", "RU": "RU", "PL": "PL", "RO": "RO", "MD": "MD", "CZ": "CZ", "SK": "SK", "KZ": "KZ", "UZ": "UZ",
                 "KG": "KG", "BY": "BY", "PT": "PT", "GR": "GR", "HK": "HK", "NZ": "NZ", "AUS": "AU", "TH": "TH",
                 "VN": "VN", "SG": "SG", "TW": "TW", "MY": "MY", "SI": "SI", "HR": "HR", "RS": "RS", "BA": "BA", "MK": "MK",
                 "LT": "LT", "LV": "LV", "BG": "BG", "HU": "HU", "EG": "EG", "MA": "MA"}
COMPANY_CODES = {"AHVN": "VN", "AHHK": "HK", "AHSH": "CN", "AHTW": "TW", "AHST": "TH", "AHTH": "TH", "AHMY": "MY",
                 "AHSG": "SG", "AHID": "ID"}
_COUNTRY_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(sorted((re.escape(k) for k in COUNTRY_NAMES), key=lambda x: -len(x))) + r")(?![a-z0-9])")
_REGION_RE = re.compile(r"(?<![a-z0-9])(" + "|".join(sorted(COUNTRY_REGIONS, key=lambda x: -len(x))) + r")(?![a-z0-9])")
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(sorted(COUNTRY_CODES, key=lambda x: -len(x))) + r")(?![A-Za-z0-9])")
_COMPANY_RE = re.compile(r"(?<![A-Za-z0-9])(AH(?:VN|HK|SH|TW|ST|TH|MY|SG|ID))(?![A-Za-z0-9])")


def detect_country(summary, desc):
    """country (ISO-2, only when single), countries, country_region (BALTICS/BALKANS), company_code, country_source.

    Evidence order: title (names, codes, regions) -> description head (400 chars; 'Turkey' ignored there since it is
    usually the HQ) -> company-code prefix (AHVN...) -> (later, in build.py) reporter hint.
    """
    res = {"country": None, "countries": [], "country_region": None, "company_code": None, "country_source": None}
    cm = _COMPANY_RE.search(summary) or _COMPANY_RE.search(desc or "")
    if cm:
        res["company_code"] = cm.group(1)
    found = []
    for label, raw in (("title", summary), ("desc", (desc or "")[:400])):
        tf = fold(raw)
        for m in _COUNTRY_RE.finditer(tf):
            iso = COUNTRY_NAMES[m.group(1)]
            if iso == "TR" and label == "desc":
                continue
            if iso not in found:
                found.append(iso)
        for m in _CODE_RE.finditer(raw):
            iso = COUNTRY_CODES[m.group(1)]
            if iso not in found:
                found.append(iso)
        rm = _REGION_RE.search(tf)
        if rm:
            res["country_region"] = REGION_LABEL[rm.group(1)]
            for iso in COUNTRY_REGIONS[rm.group(1)]:
                if iso not in found:
                    found.append(iso)
        if found:
            res["country_source"] = label
            break
    if not found and res["company_code"]:
        found = [COMPANY_CODES[res["company_code"]]]
        res["country_source"] = "company_code"
    res["countries"] = found
    if len(found) == 1 or (found and not res["country_region"] and len(found) <= 2):
        res["country"] = found[0]
    return res


# --------------------------------------------------------------------------- app guess
SUB_TO_APP = {"sirius portal": "portal", "sirius crm": "crm", "sirius partner": "partner", "sirius technician": "technician"}
SERVICE_TO_APP = {"manusoft": "other:manusoft", "contact": "other:contact", "arcontact": "other:contact",
                  "contactportal": "other:contact", "global salesforce crm service": "other:salesforce",
                  "atlas": "other:atlas", "amazon connect": "other:amazon_connect", "saris client": "other:saris",
                  "amazon web service (aws)": "other:aws", "identity & access management": "other:iam"}
TEXT_APP_RULES = [
    ("technician", r"technician (app|application|mobile)|tablet|mobile app|teknisyen uygulama|\bios\b|android|\bapk\b|testflight"),
    ("partner", r"sirius partner|partner portal|service ?shop (portal|app)|\basc portal"),
    ("crm", r"sirius crm|call ?cent|\bcrm\b(?! service)|callcenter|cagri merkezi"),
    ("portal", r"sirius portal|head ?office|\bportal\b|job control|admin panel"),
    ("other:manusoft", r"manusoft"), ("other:salesforce", r"salesforce"),
    ("other:contact", r"arcontact|contact\.?(eg|rom)|\bcontact portal"),
]


def guess_app(service, sub_service, summary, desc):
    """app_guess in {portal,crm,partner,technician,sirius_unknown,other:<x>,unknown}; sub_service/service win over text."""
    text = fold(summary + " " + (desc or "")[:300])
    s, sub = fold(service), fold(sub_service)
    alt = None
    for app, rx in TEXT_APP_RULES:
        if re.search(rx, text):
            alt = app
            break
    if sub in SUB_TO_APP:
        app, src = SUB_TO_APP[sub], "sub_service"
    elif s in SERVICE_TO_APP:
        app, src = SERVICE_TO_APP[s], "service"
    elif sub in ("manusoft global", "manusoft turkiye"):
        app, src = "other:manusoft", "sub_service"
    elif s == "sirius" or re.search(r"sirius|sirisu|syrius|sirus\b", text):
        app, src = (alt if alt and not alt.startswith("other:") else "sirius_unknown"), "text"
    elif alt:
        app, src = alt, "text"
    else:
        app, src = "unknown", "none"
    return {"app_guess": app, "app_source": src, "app_text_hint": alt if alt and alt != app else None}


# --------------------------------------------------------------------------- error texts
RE_QUOTED = re.compile(r"[\"“”‘’«»']([^\"“”‘’«»'\n]{8,200}?)[\"“”‘’«»']")
RE_ERRLINE = re.compile(r"(?i)\b(?:error(?: message)?|hata(?: mesaj[ıi])?|exception|warning|uyari|ошибка)\s*[:\-–]\s*([^\n]{6,200})")
RE_EXC = re.compile(r"\b(?:System\.[A-Za-z.]+Exception|[A-Za-z]+Exception|ORA-\d{4,5}|SQLSTATE\s*\w+|NullReference\w*|Object reference not set[^\n.]{0,60})")
RE_HTTP = re.compile(r"(?i)(?:http|status|error|code|hata)\s*(?:code)?\s*[:#-]?\s*\b([45]\d\d)\b|\b([45]\d\d)\s*(?:error|hata|internal server|bad gateway|forbidden|not found|unauthorized)")
ERR_HINT = re.compile(r"(?i)error|invalid|cannot|can't|unable|not (?:found|exist|allowed|valid|available|authorized|defined)|failed|hata|gecersiz|bulunamad|yetki|missing|already|does not|doesn't|exceed|required|unauthori")


def extract_errors(text):
    """Quoted messages that look like errors, 'Error: ...' fragments, exception names, HTTP 4xx/5xx codes."""
    out = []

    def add(x):
        x = re.sub(r"\s+", " ", x).strip(" .:;-")
        if len(x) >= 6 and x not in out:
            out.append(x[:200])
    for m in RE_QUOTED.finditer(text):
        q = m.group(1)
        if ERR_HINT.search(q) and "<URL>" not in q:
            add(q)
    for m in RE_ERRLINE.finditer(text):
        add(m.group(1))
    for m in RE_EXC.finditer(text):
        add(m.group(0))
    https = []
    for m in RE_HTTP.finditer(text):
        c = m.group(1) or m.group(2)
        if c not in https:
            https.append(c)
    return out[:8], https[:4]


# --------------------------------------------------------------------------- problem type rules
# (type, rule_id, folded regex, weight).  Title hit counts x1.5, description hit x1.0 (per rule, once each).
RULES = [
    ("master_data_request", "missing_product", r"(?:missing|not (?:exist|found|available|defined|registered)|new|add(?:ing)?|create|creation|load|upload|open|define|register|activate|insert|include|enter)\W+(?:\w+\W+){0,3}(?:sku|skus|model|models|product|products|material|materials|tenant|ean|barcode)\b", 3),
    ("master_data_request", "missing_tenant", r"missing tenant|tenant (?:for|is missing)|mm03|\btenants?\b", 4),
    ("master_data_request", "sku_mention", r"\bskus?\b|model code|urun ekle|model ekle|malzeme ekle|urun tanim|product (?:code|list)", 2),
    ("master_data_request", "bom", r"\bbom\b|bill of material|spare part list|parts? list|urun agaci", 3),
    ("master_data_request", "spare_part_visibility", r"spare ?parts?\W+(?:\w+\W+){0,4}(?:not (?:visible|orderable|available|appear|show)|missing|add)|(?:cannot|can.?t|unable|not able)\W+(?:\w+\W+){0,3}(?:add|order|find)\W+(?:\w+\W+){0,3}(?:part|material)|part\W+(?:\w+\W+){0,3}(?:not found|not visible|undefined|not defined)|undefined part|(?:add|adding)\W+(?:\w+\W+){0,3}(?:part|material)\W+(?:\w+\W+){0,4}(?:order|list|wo\b|work)|malzeme (?:eklen|gorun)|yedek parca", 3),
    ("master_data_request", "labour_price", r"labou?r\W+(?:\w+\W+){0,2}(?:code|tariff|price|rate|cost|fee|list)|(?:add|update|missing|upload)\W+(?:\w+\W+){0,3}prices?\b|tariff|price list|fiyat listesi|iscilik|tarife|unit price|labou?rs?\W+(?:\w+\W+){0,2}amount|price rate|labou?r rate|discount (?:rate|grade)|price (?:for|of|update|change|correction)", 3),
    ("master_data_request", "code_def", r"(?:defect|work|transport|fault|symptom|repair|reason|cancel(?:l?ation)?|reject) codes?|kod(?:u)? (?:ekle|tanim)|ariza kodu|(?:not|no|missing)\W+(?:\w+\W+){0,3}(?:in|for)\W+(?:romanian|polish|local language|english|turkish)|translat|(?:labou?r|defect|work)\W+(?:and|&|\/)\W+(?:\w+\W+){0,2}(?:not|missing)|(?:add|create|define|set ?up)\W+(?:\w+\W+){0,4}(?:list|types?|codes?|reasons?|categor\w+|statuses)\b", 3),
    ("master_data_request", "warranty_def", r"warranty\W+(?:\w+\W+){0,2}(?:type|period|duration|rule|code|start|date|extension|term)|garanti (?:tip|suresi|turu|kural)", 3),
    ("master_data_request", "partner_def", r"(?:new|create|add|open|duplicate)\W+(?:\w+\W+){0,2}(?:partner|service ?(?:center|shop|point)|asc\b|customer|dealer|branch|depot|warehouse|plant|brand|channel|survey|region)|(?:register|registration)\W+(?:\w+\W+){0,2}(?:customer|partner|dealer)|asc\W+(?:\w+\W+){0,3}(?:assign|region|mapping|wrong)|service (?:center|shop) (?:assign|mapping)", 3),
    ("access_request", "authorization", r"authori[sz]ation|authori[sz]e|yetki|permission|privilege|role\b|roles\b|rol (?:ekle|tanim|ata)|clone the role|access (?:to|right|request|rights)|erisim|grant", 4),
    ("access_request", "user_mgmt", r"(?:create|add|open|new|delete|disable|deactivate|block|unblock|remove|reactivate)\W+(?:\w+\W+){0,2}(?:user|account|login|username)|(?:user|account|kullanici|hesap)\W+(?:\w+\W+){0,2}(?:creation|create|olustur|ac\b|acil|sil|deactivat|disable|block)|new user|user creation|hesap ac|(?:yeni|olustur\w*)\W+(?:\w+\W+){0,2}(?:hesap|kullanici)|(?:hesap\w*|kullanici\w*)\W+(?:\w+\W+){0,6}(?:acin|acilmas\w*|acmak|olusturul\w*|olusturun)", 4),
    ("access_request", "login", r"\blogin\b|log ?in\b|sign ?in|password|passwd|sifre|parola|reset|locked|unlock|okta|\bocta\b|\bsso\b|\b2fa\b|\bmfa\b|unauthorized|giris yap|oturum|credentials", 3),
    ("data_correction", "status_change", r"(?:chang\w*|updat\w*|revert\w*|set|mov\w*|modif\w*|open|reopen|re-open)\W+(?:\w+\W+){0,3}status|status\W+(?:\w+\W+){0,2}(?:change|update|revert|correction)|durum(?:u)? (?:degis|guncel)|statu(?:s|)u? degis|reopen|re-open", 4),
    ("data_correction", "cancel_reject", r"(?:cancel|cancell?ation|reject|close|void|revoke|iptal|reddet|kapat)\W+(?:\w+\W+){0,3}(?:wo\b|w/o|work ?orders?|case|order|is emri|request|talep|invoice|po\b)|(?:wo\b|w/o|work ?orders?)\W+(?:\w+\W+){0,3}(?:cancel|reject|close|iptal)|please (?:reject|cancel)|cannot close|unable to close|can.?t close", 4),
    ("data_correction", "delete_fix", r"\b(?:delete|erase|remove|merge)\b|silin|silme|sil\b|duzelt|\bcorrect(?:ion)?\b|update\W+(?:\w+\W+){0,3}(?:serial|s/n|series|date|name|address|phone|vat|customer|data)|change\W+(?:\w+\W+){0,3}(?:serial|s/n|date|name|address|phone|customer|vat|technician|asc|engineer|assignment)|degistir|guncelle|re-?assign|chang\w*\W+(?:\w+\W+){0,6}(?:for|on|in)\W+(?:\w+\W+){0,2}(?:wo\b|wo.?s\b|work ?orders?)|transfer (?:wo|work|code)|wrong data|data fix|fix (?:the )?data", 3),
    ("integration_failure", "sap", r"\bsap\b|s/?4\b|hana|fy document|failed docs?|integration|entegrasyon|idoc|mulesoft|\bmule\b|\bedi\b|interface|webservice|web service|\bapi\b|sirius\W+(?:\w+\W+){0,3}(?:sf|salesforce)\b|\b(?:sf|salesforce)\W+(?:\w+\W+){0,3}sirius|aktarim|aktarilm|not (?:posted|transferred|transmitted|synchron|sent to)|sync(?:hron)? (?:error|fail|issue)|invoice (?:cannot|can.?t|not)|(?:cannot|can.?t|unable)\W+(?:\w+\W+){0,3}invoice|e-?invoice|\bpo\b\W+(?:\w+\W+){0,3}(?:sap|hold)|payment (?:not|cannot)", 3),
    ("notification_failure", "msg", r"\bsms\b|e-?mail\W+(?:\w+\W+){0,4}(?:not|never|no)\W|(?:not|never|no)\W+(?:\w+\W+){0,3}(?:receiv|sent|deliver|get|arriv)\w*\W+(?:\w+\W+){0,2}(?:e-?mail|sms|code|notification|message|otp)|notification|nps\W+(?:\w+\W+){0,2}(?:sms|survey|mail|email|link|message|send|sent)|(?:sms|mail|email)\W+(?:\w+\W+){0,2}nps|\bpush\b|\botp\b|bildirim|mail gel|mesaj gel|survey (?:sms|link)|\bwhatsapp\b|verification code", 4),
    ("performance_availability", "perf", r"\bslow|yavas|blank|empty page|white page|bos sayfa|not loading|does(?:n.?t| not) load|never load|won.?t load|timeout|time out|time-out|zaman asimi|\bdown\b|outage|erisilem|acilmiyor|acilmadi|\b50[0234]\b|\bstuck|\bhang(?:s|ing|ed)?\b|\bfreez|not respond|no response|performance|loading|service unavailable|system (?:is )?unavailable|temporarily unavailable", 4),
    ("mobile_offline", "mobile", r"offline|\bios\b|android|tablet|mobile app|technician app|\bapk\b|app ?store|testflight|play store|iphone|ipad|\bgps\b|mobile", 3),
    ("report_export", "report", r"\breports?\b|rapor|\bexport|excel|download|dashboard|extract|\bbi\b|power ?bi|data request|veri talep|\bkpi\b|\bquery\b|sorgu", 3),
    ("how_to_question", "howto", r"how (?:to|can|do|should)|nasil|is it possible|possible to|can we|could you (?:explain|clarify)|what is (?:the )?(?:process|procedure)|clarification|explain|training|egitim|guide|\bquestion|soru\b|is there a way|procedure", 3),
    ("functional_defect", "wrong_behaviour", r"\bwrong\b|incorrect|nothing happens|not (?:working|work|show|display|correct|visible|available|calculated|updated|saved|allowed|uploaded|loaded|created|sent|opened|found|received|appear\w*|changed|possible|able)|\bcannot\b|can.?t\b|unable|\berror\b|\bbug\b|\bissue\b|problem|hata|calismiyor|yanlis|gorunmuyor|gorulemiyor|duplicat|missing|failed|\bfail\b|does(?:n.?t| not)|incorrectly|mismatch|different", 1),
]
_COMPILED = [(t, rid, re.compile(rx), w) for t, rid, rx, w in RULES]
TYPE_ORDER = ["master_data_request", "access_request", "data_correction", "integration_failure", "notification_failure",
              "performance_availability", "mobile_offline", "report_export", "how_to_question", "functional_defect"]
PROBLEM_TYPES = TYPE_ORDER + ["other"]


def classify(summary, desc):
    """Rule based problem type; ties resolved by TYPE_ORDER; confidence from margin between top-2 type scores."""
    tf, df = fold(summary), fold(desc)
    scores = defaultdict(float)
    best_rule = {}
    for typ, rid, rx, w in _COMPILED:
        s = 0.0
        if rx.search(tf):
            s += 1.5 * w
        if rx.search(df):
            s += 1.0 * w
        if s:
            scores[typ] += s
            if s > best_rule.get(typ, (0, ""))[0]:
                best_rule[typ] = (s, rid)
    if not scores:
        return {"problem_type": "other", "problem_confidence": 0.3, "problem_rule": "fallback", "problem_scores": {}}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], TYPE_ORDER.index(kv[0])))
    top, top_s = ranked[0]
    second_s = ranked[1][1] if len(ranked) > 1 else 0.0
    conf = round(min(0.95, 0.35 + 0.6 * (top_s - second_s) / top_s * min(1.0, top_s / 6.0) + (0.05 if top_s >= 6 else 0)), 2)
    return {"problem_type": top, "problem_confidence": conf, "problem_rule": best_rule[top][1],
            "problem_scores": {k: round(v, 1) for k, v in ranked[:3]}}


# --------------------------------------------------------------------------- language
TR_STOP = set("ve bir bu icin ile lutfen merhaba selamlar tesekkurler rica ederim olarak yok var olan sistem hata gerekiyor degil ama gibi cok daha sonra yardim".split())
EN_STOP = set("the and for with please this that not can are is to of in on we you have has hello dear thanks thank from our when which been error cannot unable".split())
RO_STOP = set("si este pentru nu care cu din sunt va rog buna ziua multumesc".split())
PL_STOP = set("nie jest dla oraz prosze dzien dobry dziekuje sie jak to ale".split())
OTHER_STOP = {
    "fr": set("le la les des du est pour une bonjour impossible merci avec dans pas sur sont nous vous".split()),
    "de": set("der die das und ist nicht fur bitte guten tag danke mit auf wir sie ein eine".split()),
    "es": set("el los las para por una gracias hola con que como favor del esta son".split()),
    "pt": set("para uma obrigado ola com nao sao dos das por favor bom dia".split()),
    "it": set("il gli per una grazie ciao con non sono della buongiorno".split()),
}


def guess_language(text):
    """Heuristic language guess: script counts + small stopword lists. Returns primary + up to 3 candidates."""
    t = text or ""
    n = max(1, len(re.findall(r"[^\W\d_]", t)))
    ws = words(t)
    nw = max(1, len(ws))
    cyr = len(re.findall(r"[Ѐ-ӿ]", t))
    cjk = len(re.findall(r"[一-鿿]", t))
    thai = len(re.findall(r"[฀-๿]", t))
    arab = len(re.findall(r"[؀-ۿ]", t))
    c_tr = sum(1 for w in ws if w in TR_STOP) + len(re.findall(r"[ğşıİĞŞ]", t))
    c_en = sum(1 for w in ws if w in EN_STOP)
    c_ro = sum(1 for w in ws if w in RO_STOP) + len(re.findall(r"[ăâșțŢ]", t)) // 2
    c_pl = sum(1 for w in ws if w in PL_STOP) + len(re.findall(r"[łąęńśźż]", t))
    cand = [("ru", cyr / n * 10), ("zh", cjk / n * 10), ("th", thai / n * 10), ("ar", arab / n * 10),
            ("tr", c_tr / nw * 6), ("en", c_en / nw * 4), ("ro", c_ro / nw * 6), ("pl", c_pl / nw * 6)]
    for lang, sw in sorted(OTHER_STOP.items()):
        cand.append((lang, sum(1 for w in ws if w in sw) / nw * 6))
    cand = sorted(((l, round(s, 3)) for l, s in cand if s >= 0.12), key=lambda x: (-x[1], x[0]))
    if not cand:
        return {"language": "en" if ws else "unknown", "languages": []}
    langs = [l for l, _ in cand[:3]]
    return {"language": langs[0], "languages": langs}


# --------------------------------------------------------------------------- screen hints
def _camel_words(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return words(name.replace("_", " ").replace("-", " "))


class ScreenMatcher:
    """Matches ticket text against i18n texts (en/tr) and screen/route names of the four Sirius apps.

    Scoring: every maximal matched phrase contributes len(words)/sqrt(#screens using that text) to each screen using it
    (screen-name phrases: 1.2x). Error texts equal to a backend error message add 2/sqrt(#screens). A screen needs a raw
    score >= 0.9 (about one distinctive 3-word UI phrase); reported score = 1-exp(-raw/3).
    """
    APPS = ("portal", "crm", "partner", "technician")
    MAX_SCREENS_PER_PHRASE = 25
    MIN_RAW = 1.8

    def __init__(self, codemap_dir=CODEMAP):
        self.ok = os.path.exists(os.path.join(codemap_dir, "i18n_portal.json"))
        self.phrases = defaultdict(lambda: defaultdict(set))
        self.names = defaultdict(lambda: defaultdict(set))
        self.routes = {}
        self.endpoints = {}
        self.err_index = []
        self.ep_screens = {}
        self.maxlen = 8
        if not self.ok:
            return
        trace = load_json(os.path.join(codemap_dir, "trace_index.json"))
        for app, d in trace.get("by_screen", {}).items():
            for f, eps in d.items():
                self.endpoints[(app, f)] = eps
        self.ep_screens = trace.get("by_endpoint", {})
        for ep, d in sorted(trace.get("endpoint_downstream", {}).items()):
            for msg in d.get("error_messages", []) or []:
                nm = " ".join(words(msg if isinstance(msg, str) else str(msg)))
                if len(nm) >= 18 and "*" not in nm:
                    self.err_index.append((nm, ep))
        for app in self.APPS:
            fe = load_json(os.path.join(codemap_dir, "frontend_%s.json" % app))
            for sc in fe.get("screens", []):
                rts = sc.get("routes") or []
                self.routes[(app, sc["file"])] = rts or (sc.get("reachable_from_routes") or [])[:2]
                parts = sc["file"].replace("\\", "/").split("/")
                if parts and parts[-1].startswith("index."):
                    parts = parts[:-1]
                nm = " ".join(_camel_words(re.sub(r"\.\w+$", "", parts[-1]))) if parts else ""
                if nm.count(" ") >= 1 and len(nm) >= 9:
                    self.names[nm][app].add(sc["file"])
            i18n = load_json(os.path.join(codemap_dir, "i18n_%s.json" % app))
            usage = i18n.get("key_usage", {})
            for lang in ("en", "tr"):
                for ns, kv in i18n.get("translations", {}).get(lang, {}).items():
                    for key, text in kv.items():
                        if not isinstance(text, str) or any(c in text for c in "{@%*"):
                            continue
                        ws = words(text)
                        if len(ws) < 2 or len(ws) > self.maxlen or len(" ".join(ws)) < 9:
                            continue
                        scr = usage.get("%s::%s" % (ns, key))
                        if scr:
                            self.phrases[" ".join(ws)][app].update(scr)
        for ph in list(self.phrases):
            if sum(len(v) for v in self.phrases[ph].values()) > self.MAX_SCREENS_PER_PHRASE:
                del self.phrases[ph]

    def _grams(self, ws):
        n = len(ws)
        grams = {}
        for i in range(n):
            for L in range(2, self.maxlen + 1):
                if i + L > n:
                    break
                g = " ".join(ws[i:i + L])
                if g in self.phrases or g in self.names:
                    grams[(i, L)] = g
        return grams

    def fit(self, texts):
        """Corpus document frequency of every known phrase; phrases found in many tickets (generic words like
        'work order') are down-weighted by min(1, DF_REF / df) so that they cannot produce hints on their own."""
        self.df = Counter()
        for t in texts:
            self.df.update(set(self._grams(words(t)).values()))

    DF_REF = 40

    def match(self, text, app_guess=None, error_texts=(), top=3):
        if not self.ok:
            return []
        ws = words(text)
        n = len(ws)
        grams = self._grams(ws)
        taken = [False] * n
        kept = []
        for i, L in sorted(grams, key=lambda x: (-x[1], x[0])):
            if any(taken[i:i + L]):
                continue
            for k in range(i, i + L):
                taken[k] = True
            kept.append(grams[(i, L)])
        scores = defaultdict(float)
        evid = defaultdict(list)

        def bump(key, w, ev):
            scores[key] += w
            if len(evid[key]) < 4:
                evid[key].append(ev)
        for g in kept:
            L = g.count(" ") + 1
            dfw = min(1.0, self.DF_REF / max(1, getattr(self, "df", {}).get(g, 1)))
            if g in self.phrases:
                tot = sum(len(v) for v in self.phrases[g].values())
                for app, scr in sorted(self.phrases[g].items()):
                    for s in sorted(scr):
                        bump((app, s), dfw * L / math.sqrt(tot), {"type": "i18n", "phrase": g, "n_screens": tot})
            if g in self.names:
                tot = sum(len(v) for v in self.names[g].values())
                for app, scr in sorted(self.names[g].items()):
                    for s in sorted(scr):
                        bump((app, s), dfw * 1.2 * L / math.sqrt(tot), {"type": "screen_name", "phrase": g, "n_screens": tot})
        ep_hits = {}
        for et in error_texts:
            en = " ".join(words(et))
            if len(en) < 18:
                continue
            for nm, ep in self.err_index:
                if nm in en or (len(en) >= 24 and en in nm):
                    ep_hits[ep] = en
        for ep, en in sorted(ep_hits.items()):
            for app, scr in sorted(self.ep_screens.get(ep, {}).items()):
                for s in sorted(scr):
                    bump((app, s), 2.0 / math.sqrt(max(1, len(scr))),
                         {"type": "backend_error_message", "endpoint": ep, "text": en[:80]})
        out = []
        for (app, s), sc in scores.items():
            if app_guess in self.APPS:
                mult = 1.0 if app == app_guess else 0.0
            elif app_guess and app_guess.startswith("other:"):
                mult = 0.0
            else:
                mult = 0.85
            sc *= mult
            if sc >= self.MIN_RAW:
                out.append((sc, app, s))
        out.sort(key=lambda x: (-x[0], x[1], x[2]))
        tokset = set(ws)
        res = []
        for sc, app, s in out[:top]:
            eps = list(self.endpoints.get((app, s), []))
            eps.sort(key=lambda e: (-len(set(_camel_words(e.rsplit("/", 1)[-1] if "{" not in e.rsplit("/", 1)[-1] else e.split("/")[-2])) & tokset), e))
            res.append({"app": app, "screen": s, "routes": self.routes.get((app, s), [])[:2],
                        "score": round(1 - math.exp(-sc / 3.0), 2), "raw_score": round(sc, 2),
                        "evidence": evid[(app, s)], "endpoints": eps[:3]})
        return res


# --------------------------------------------------------------------------- orchestration
class Extractor:
    def __init__(self, person_names=(), screen_matcher=None):
        self.persons_re = build_person_re(person_names)
        self.sm = screen_matcher

    def extract(self, tk, summary=None, desc=None):
        """tk: dict with service/sub_service (+ summary/desc unless given). desc may be the Jira full description."""
        summary = tk.get("summary", "") if summary is None else summary
        desc = tk.get("desc", "") if desc is None else desc
        s_clean = scrub(summary, self.persons_re)
        d_clean = scrub(desc, self.persons_re)
        full = (s_clean + "\n" + d_clean).strip()
        ents = extract_ids(full)
        country = detect_country(s_clean, d_clean)
        app = guess_app(tk.get("service", ""), tk.get("sub_service", ""), s_clean, d_clean)
        errs, https = extract_errors(full)
        cls = classify(s_clean, d_clean)
        lang = guess_language(full)
        hints = self.sm.match(full, app["app_guess"], errs) if self.sm else []
        return {
            "text_clean": {"summary": s_clean, "desc": d_clean},
            "entities": dict(ents, error_texts=errs, http_codes=https),
            "country": country, "app": app, "language": lang, "problem": cls, "screen_hints": hints,
        }
