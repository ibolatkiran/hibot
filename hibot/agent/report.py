"""Renders the final report (dict) as developer-friendly Turkish Markdown.  Technical names stay unchanged."""
from __future__ import annotations

from typing import Any, Dict, List

CLASS_TR = {
    "code_defect": "Kod hatası (code_defect)",
    "master_data": "Ana veri eksikliği/hatası (master_data)",
    "access": "Yetki/erişim talebi (access)",
    "data_correction": "Veri düzeltme (data_correction)",
    "integration": "Entegrasyon (integration)",
    "config": "Konfigürasyon (config)",
    "user_error": "Kullanıcı hatası / tasarım gereği (user_error)",
    "unknown": "Belirsiz (unknown)",
}
EVIDENCE_TR = {"query": "sorgu", "code": "kod", "similar_case": "benzer vaka", "card": "bilgi kartı"}
STATUS_TR = {"executed": "ÇALIŞTIRILDI", "not_executed": "ÇALIŞTIRILMADI", "not_run": "SORGU YOK"}


def confidence_label(c: float) -> str:
    return "düşük" if c < 0.4 else ("orta" if c < 0.75 else "yüksek")


def _cell(v: Any, n: int = 40) -> str:
    s = "" if v is None else str(v).replace("|", "/").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def _table(cols: List[str], rows: List[List[Any]], max_rows: int = 5, max_cols: int = 8) -> List[str]:
    cols = cols[:max_cols]
    out = ["| " + " | ".join(_cell(c, 30) for c in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows[:max_rows]:
        out.append("| " + " | ".join(_cell(c) for c in r[:max_cols]) + " |")
    return out


def _query_state(q: dict) -> str:
    if q.get("executed"):
        return "ÇALIŞTIRILDI"
    if q.get("mode") == "dry-run":
        return "ÇALIŞTIRILMADI (dry-run)"
    return "HATA"


def render_markdown(run: Dict[str, Any]) -> str:
    rep, queries = run["report"], run.get("queries") or []
    inc = run.get("incident") or {}
    L: List[str] = []
    title = inc.get("key") or "serbest metin"
    L.append("# HiBot Analiz Raporu: %s" % title)
    L.append("")
    L.append("- Run: `%s`" % run.get("run_id"))
    L.append("- Mod: `%s` | Bölge: `%s` | LLM: `%s` | Oluşturma: %s" % (run.get("mode"), run.get("region"), run.get("llm"), run.get("created")))
    executed = sum(1 for q in queries if q.get("executed"))
    L.append("- Sorgular: %d hazırlandı, **%d gerçekten çalıştırıldı**" % (len(queries), executed))
    if rep.get("fallback"):
        L.append("")
        L.append("> **Uyarı:** Otomatik analiz tamamlanamadı; bu rapor yedek (fallback) rapordur.")
    if executed == 0:
        L.append("")
        L.append("> **Uyarı:** Bu analizde **hiçbir sorgu gerçekten çalıştırılmadı** (dry-run veya hata). "
                 "Aşağıdaki bulgular veritabanı kanıtıyla doğrulanmış değildir.")
    L.append("")
    L.append("## Özet")
    L.append(rep.get("summary", ""))
    L.append("")
    L.append("**Sınıflandırma:** %s" % CLASS_TR.get(rep.get("classification"), rep.get("classification")))
    L.append("")
    L.append("## Kök neden")
    L.append(rep.get("root_cause", ""))
    L.append("")
    L.append("## Kanıtlar")
    evid = rep.get("evidence") or []
    if not evid:
        L.append("_Kanıt sunulmadı._")
    by_no = {q["n"]: q for q in queries}
    for i, ev in enumerate(evid, 1):
        st = STATUS_TR.get(ev.get("status"), "")
        L.append("")
        L.append("### %d. [%s] `%s`%s" % (i, EVIDENCE_TR.get(ev.get("type"), ev.get("type")), ev.get("ref"), (" — **%s**" % st) if st else ""))
        L.append(ev.get("finding", ""))
        if ev.get("note"):
            L.append("")
            L.append("_%s_" % ev["note"])
        q = by_no.get(ev.get("query_no"))
        if q:
            L.extend(_query_block(q))
    tw = rep.get("ticket_window_changes")
    if tw and tw.get("text"):
        L.append("")
        L.append(tw["text"])
    L.append("")
    L.append("## Çalıştırılan sorgular")
    if queries:
        L.append("")
        L.append("| # | Sorgu | Bölge | Durum | Satır |")
        L.append("|---|---|---|---|---|")
        for q in queries:
            L.append("| %s | `%s` | %s | %s | %s |" % (q["n"], q["query_id"], q["region"], _query_state(q),
                                                         "" if q.get("row_count") is None else q["row_count"]))
        shown = {ev.get("query_no") for ev in evid}
        for q in queries:
            if q["n"] not in shown:
                L.append("")
                L.append("**Sorgu #%s `%s`** — %s" % (q["n"], q["query_id"], q.get("why") or ""))
                L.extend(_query_block(q))
    else:
        L.append("_Hiç sorgu hazırlanmadı._")
    L.append("")
    L.append("## Kod konumu")
    locs = rep.get("code_locations") or []
    if locs:
        for c in locs:
            L.append("- `%s` / `%s`%s: %s" % (c.get("repo"), c.get("path"), (":%s" % c["line"]) if c.get("line") else "", c.get("why", "")))
    else:
        L.append("_Belirlenemedi._")
    L.append("")
    L.append("## Çözüm adımları")
    for i, s in enumerate(rep.get("fix_steps") or ["_Önerilen adım yok._"], 1):
        L.append("%d. %s" % (i, s))
    vq = rep.get("verification_query_ids") or []
    if vq:
        L.append("")
        L.append("Doğrulama için çalıştırılabilecek şablonlar: " + ", ".join("`%s`" % v for v in vq))
    L.append("")
    L.append("## Güven")
    c = float(rep.get("confidence", 0))
    L.append("**%.2f / 1.00** (%s)" % (c, confidence_label(c)))
    if rep.get("confidence_note"):
        L.append("")
        L.append("_%s_" % rep["confidence_note"])
    L.append("")
    L.append("## Benzer vakalar")
    sim = inc.get("similar") or []
    if sim:
        for s in sim[:5]:
            L.append("- `%s` (benzerlik %s, %s) %s" % (s["key"], s.get("score"), s.get("problem_type"), s.get("summary", "")))
        L.append("")
        L.append("_Benzer vakalar metin benzerliğidir; doğrulanmış kök neden değildir._")
    else:
        L.append("_Benzer vaka bulunamadı._")
    mc = run.get("matches") or []
    if mc:
        L.append("")
        L.append("**Eşleşen bilgi kartları:** " + ", ".join("`%s` (%.2f, %s)" % (m["card"]["id"], m["score"], m["card"]["status"]) for m in mc))
    L.append("")
    L.append("## Geri bildirim")
    L.append("`python -m hibot feedback %s --verdict correct|wrong|partial [--note \"...\"]`" % run.get("run_id"))
    L.append("")
    return "\n".join(L)


def _query_block(q: dict) -> List[str]:
    out = ["", "Sorgu #%s (`%s`, %s, %s)%s:" % (q["n"], q["query_id"], q["region"], _query_state(q),
                                              (" parametreler: %s" % q["params"]) if q.get("params") else ""),
           "```sql", (q.get("sql_display") or q.get("sql") or "").replace("```", "'''"), "```"]
    if q.get("kind") == "template":
        out.append("_Parametre değerleri yalnızca gösterim için SQL'e işlenmiştir; çalıştırma bağlı (bound) parametrelerle yapılır._")
    if q.get("executed"):
        out.append("Sonuç: %s satır%s." % (q.get("row_count"), " (satır limiti nedeniyle kesildi)" if q.get("truncated") else ""))
        if q.get("rows"):
            out.append("")
            out.extend(_table(q.get("columns") or [], q["rows"]))
    elif q.get("error"):
        out.append("Hata: %s" % q["error"])
    return out
