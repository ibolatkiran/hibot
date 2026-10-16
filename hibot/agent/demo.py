"""Deterministic scripted scenario for `--llm fake`: demonstrates the whole pipeline without an LLM or a database.

The answers are NOT produced by a model: they are a fixed script (history ticket -> inspect endpoint -> two queries ->
final report).  Ids that the ticket does not provide are DEMO placeholders and the run is flagged `demo`
(feedback on a demo run never creates or changes cards).
"""
from __future__ import annotations

from typing import List, Optional

from hibot.llm.client import FakeLLM

DEMO_TENANT_ID = 1
DEMO_WORK_ORDER_ID = 12345678


def build_demo_llm(inc: dict, matches: Optional[List[dict]] = None, region: str = "EU") -> FakeLLM:
    text = ("%s %s" % (inc.get("summary", ""), inc.get("desc", ""))).lower()
    ents = inc.get("entities") or {}
    wo = int((ents.get("work_order_ids") or [DEMO_WORK_ORDER_ID])[0])
    card_ids = [m["card"]["id"] for m in matches or []]
    if inc.get("key") == "SM-1777386" or "history" in text:
        params = {"tenant_id": DEMO_TENANT_ID, "work_order_id": wo}
        evidence = [
            {"type": "code", "ref": "GET api/Technician/GetSiriusWorkOrderHistory/{WorkOrderId}",
             "finding": "Ekran geçmiş sekmesi TechnicianManager.GetSiriusWorkOrderHistory üzerinden dbo.GetSiriusWorkOrderHistory fonksiyonunu çağırıyor (codemap)."},
            {"type": "query", "ref": "wo_history_function",
             "finding": "DEMO: Fonksiyon satır sayısı ile ham Operation.WorkOrderGpsInfo satırları karşılaştırılmalı (RawGpsRowsThisOrder vs FunctionRows)."},
            {"type": "query", "ref": "wo_status_timeline",
             "finding": "DEMO: Durum zaman çizelgesi, geçmişin gerçekten tek kayıt mı yoksa çeviri eksikliğinden mi kısaldığını gösterir."},
        ]
        if card_ids:
            evidence.append({"type": "card", "ref": card_ids[0], "finding": "Belirti imzası bu bilgi kartıyla eşleşiyor (taslak, doğrulanmamış)."})
        script = [
            {"action": "inspect_code", "target": {"kind": "endpoint", "value": "GetSiriusWorkOrderHistory"},
             "why": "Geçmiş ekranının hangi endpoint/SP'yi kullandığını görmek için."},
            {"action": "run_query", "query_id": "wo_history_function", "params": params, "region": region,
             "why": "Fonksiyonun döndürdüğü satırları ham tablo sayılarıyla karşılaştır."},
            {"action": "run_query", "query_id": "wo_status_timeline", "params": params, "region": region,
             "why": "Durum geçmişinin gerçekten tek kayıt olup olmadığını doğrula."},
            {"action": "final_report",
             "summary": "DEMO: Geçmiş ekranı dbo.GetSiriusWorkOrderHistory fonksiyonundan besleniyor; tek kayıt görünmesi çeviri eksikliği (ana veri) veya gerçekten tek kayıt yazılması olabilir.",
             "classification": "master_data",
             "root_cause": "Hipotez (DEMO, doğrulanmadı): fonksiyon Catalog.Status ve Catalog.AppTranslationEntry tablolarına INNER JOIN yaptığı için seçili dilde çevirisi olmayan durum satırları düşüyor.",
             "confidence": 0.7, "evidence": evidence,
             "code_locations": [{"repo": "gfsm-europe", "path": "dbo/Functions/GetSiriusWorkOrderHistory.sql", "line": 1,
                                 "why": "Geçmiş fonksiyonu; Status/AppTranslationEntry INNER JOIN'leri"}],
             "fix_steps": ["wo_history_function sonucunda RawGpsRowsThisOrder ile FunctionRows değerlerini karşılaştırın.",
                           "RawRowsMissingTranslation > 0 ise eksik Catalog.AppTranslationEntry satırlarını ana veri talebi olarak ekletin.",
                           "RawGpsRowsThisOrder = 1 ise geçmişin yazılmadığı yolu (wo_field_changes) inceleyin."],
             "verification_query_ids": ["wo_history_function", "wo_status_timeline", "wo_field_changes"],
             "suggested_card": {"title": "Work order history shows only one record (missing status translation)",
                                "symptom_signature": {"problem_type": None, "app": None, "keywords": ["history", "only 1 record", "only one record"], "entity_requirements": []},
                                "root_cause": "GetSiriusWorkOrderHistory INNER JOINs translations; statuses without a translation in the language are dropped.",
                                "verification_queries": [{"query_id": "wo_history_function", "params_from_entities": {"WorkOrderId": "entities.work_order_ids[0]", "TenantId": "$tenant"}}],
                                "fix_steps": ["Add the missing Catalog.AppTranslationEntry rows (master data request)."]}},
        ]
    else:
        value = (inc.get("summary") or "ticket")[:100]
        script = [
            {"action": "inspect_code", "target": {"kind": "text", "value": value}, "why": "Belirtiyle ilgili kodu bulmak için serbest arama."},
            {"action": "final_report", "summary": "DEMO: Senaryo bu bilet tipi için hazır değil; kök neden belirlenemedi.",
             "classification": "unknown", "root_cause": "Belirsiz (scripted demo, gerçek analiz yapılmadı).", "confidence": 0.1,
             "evidence": [], "code_locations": [], "fix_steps": ["Gerçek bir LLM ile çalıştırın: --llm http"],
             "verification_query_ids": []},
        ]
    return FakeLLM(script)
