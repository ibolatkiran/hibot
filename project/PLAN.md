# Plan ve durum

Durum işaretleri: ✅ bitti · 🔄 devam ediyor · ⏳ bekliyor (bağımlılık var) · ⬜ başlamadı

## Hedef mimari

```
                ┌─────────── BİLGİ TABANI (offline, düzenli yenilenir) ───────────┐
                │ 1. Kod haritası: ekran→endpoint→controller→manager→SP→tablo     │  knowledge/codemap
                │ 2. Şema kataloğu: tablolar, lookup'lar, log tabloları, PII      │  knowledge/schema
                │ 3. Incident hafızası + "Bilinen Sorun Kartları"                 │  knowledge/incidents, knowledge/cards
                │    (belirti imzası → kök neden → doğrulama sorgusu → çözüm)     │
                │ 4. Teşhis sorgu kütüphanesi                                     │  knowledge/queries
                └──────────────────────────────────────────────────────────────────┘
                                      ▲ okur              │ öğrenir
Yeni ticket ──► [1] Varlık çıkarımı (WO no, ülke→tenant, uygulama, ekran, hata metni)
                [2] Benzer vaka / kart eşleşmesi ── güçlü ──► kartın doğrulama sorgusu ──┐
                        │ zayıf                                                          │
                        ▼                                                                │
                [3] Soruşturma döngüsü. Tool calling YOK, adımları bizim kodumuz yürütür: │
                    LLM JSON çıktı verir {"next": "run_query", "query_id": .., "params": ..}│
                    kod doğrular → çalıştırır → maskeleyip sonucu LLM'e geri verir        │
                    (kod/harita araması: codemap + şema kataloğu + SP .sql dosyaları)      │
                        ▼                                                                ▼
                [4] Rapor: kök neden + kanıt sorguları/sonuçları + dosya:satır / SP + çözüm adımları + güven skoru
                        ▼
                [5] Developer onayı/düzeltmesi ──► kart oluştur/güncelle (öğrenme döngüsü)
```

## Fazlar

### Faz 0: Hazırlık
- ✅ Keşif: veri, 4 önyüz, backend, DB (bkz. FINDINGS.md)
- ✅ Git repo + dokümantasyon iskeleti
- ✅ Jira XML export talimatı ve JQL parçaları (`docs/jira_export/`)
- ⏳ **Kullanıcı:** 19 parça XML export'u `docs/jira_export/raw/` altına koyacak
- ⏳ **Kullanıcı:** read-only prod DB erişimi (EU + APAC)
- ⏳ **Kullanıcı:** şirket AI endpoint'inin dokümanı (model, format, embedding var mı)

### Faz 1: Bilgi tabanı
- ✅ Şema kataloğu: `tools/schema_catalog` → `knowledge/schema`
- ✅ Kod izlenebilirlik haritası: `tools/code_map` → `knowledge/codemap` (%92,8 önyüz→backend eşleşmesi)
- ✅ Incident ön işleme: varlık çıkarımı, yeni sınıflama, BM25 benzerlik, 566 tekrar eden grup, Jira XML ayrıştırıcı (`tools/incidents`, 16 test)
- 🔄 Güvenli SQL katmanı + teşhis sorgu şablonları (`hibot/db`, `knowledge/queries`)
- ⬜ codemap ↔ şema kataloğu birleşimi: endpoint → SP → SP'nin okuduğu/yazdığı tablolar (tek graf)
- ⏳ Jira XML ile zenginleştirme (export gelince `tools/incidents/build.py` tekrar çalıştırılır)
- ⏳ Bilinen Sorun Kartları v1: tekrar eden gruplardan LLM ile kart çıkarımı (Jira yorumları ve çözüm notları gerekli) → `knowledge/cards/`

### Faz 2: MVP teşhis agent'ı
- ⏳ LLM istemcisi (şirket endpoint'i), JSON çıktı doğrulama ve yeniden deneme
- ⬜ Orkestratör: ticket → varlık → kart eşleşmesi → soruşturma döngüsü (adım/bütçe limiti) → rapor
- ⬜ Rapor formatı (Markdown): kök neden, kanıt, kod konumu, çözüm, güven skoru, kullanılan sorgular
- ⬜ CLI: `python -m hibot analyze SM-xxxxxx` veya serbest metin
- ⬜ Değerlendirme seti: geçmiş ticket'lardan, kök nedeni Jira yorumlarında bilinen 30–50 vaka

### Faz 3: Öğrenme döngüsü
- ⬜ Developer geri bildirimi (onay/düzeltme) → kart oluşturma/güncelleme, versiyonlama
- ⬜ Metrikler: doğru kök neden oranı, analiz süresi, kart isabet oranı

### Faz 4: Otomasyon
- ⬜ Yeni ticket alımı (API yok → Jira bildirim e-postaları + Graph/Email2Case altyapısı adayı)
- ⬜ Ticket'a otomatik yorum veya bildirim
- ⬜ Ana veri ve yetki taleplerini otomatik yönlendirme
- ⬜ Proaktif izleme: `SiriusInt.JobRunLog`, `Gateway.*` hataları, `ErpDocument.ErrorDescription`, `Log.DB_Errors` artışları

## Sıradaki iş (bir sonraki agent için)
1. `tools/incidents` ve `hibot/db` alt agent sonuçlarını denetle, FINDINGS ve WORKLOG'u güncelle, commit et.
2. codemap ↔ şema birleşimini yap.
3. Kullanıcıdan gelenleri bekle: XML export, DB erişimi, AI endpoint dokümanı. Geldikçe ilgili ⏳ maddeleri aç.
