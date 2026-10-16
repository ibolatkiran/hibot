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
- ✅ Güvenli SQL katmanı + 24 teşhis sorgu şablonu (`hibot/db`, `knowledge/queries`, 193 test). Gerçek DB'de ilk çalıştırma ve varsayım doğrulaması DB erişimi bekliyor ⏳.
- ✅ Jira XML toplu indirici: tarayıcı snippet'i (3'erli) + `collect.py` (`tools/jira_export`)
- ✅ Birleşik bilgi grafı: codemap ↔ şema, ileri ve ters izleme, hata metni araması (`tools/graph`, `hibot/knowledge`, 12 test)
- ⏳ Jira XML ile zenginleştirme (export gelince `tools/incidents/build.py` tekrar çalıştırılır)
- ⏳ **DB izinden öğrenme** (D13): kapanmış ticket'lar için açılış–kapanış aralığında ilgili iş emri ve kayıtlarda ne değiştiğini çıkar (`Log.WorkOrderJsonLog`, `WorkOrderGpsInfo`, audit) → "fiilen yapılan çözüm" etiketi. Gerekenler: prod DB + XML'deki resolved tarihleri.
- ⏳ Bilinen Sorun Kartları v1: en büyük ~30 tekrar eden grup için kod haritası, şema ve DB izi üzerinden taslak kart; developer bir kez ✅/❌ verir → `knowledge/cards/`. Jira yorumlarına **güvenilmez** (D13).

### Faz 2: MVP teşhis agent'ı
- ✅ LLM istemci katmanı (FakeLLM + HttpChatClient), JSON çıkarma, doğrulama ve onarım döngüsü
- ⏳ Şirket endpoint'ine bağlama ve prompt'ları gerçek modelde ayarlama (endpoint dokümanı bekleniyor)
- ✅ Orkestratör: ticket → varlık → kart eşleşmesi → soruşturma döngüsü (bütçeli) → rapor (`hibot/agent`)
- ✅ Rapor formatı (Türkçe Markdown + JSON), çalışmayan sorgular kanıt sayılmıyor
- ✅ CLI: `python -m hibot analyze|feedback`; FakeLLM demosu uçtan uca çalışıyor
- ✅ Geri bildirim → kart promosyonu (Faz 3'ün çekirdeği)
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
1. **Kullanıcı XML'leri indirince:** `python tools/jira_export/collect.py`, ardından `python tools/incidents/build.py`. Zenginleşen istatistikleri FINDINGS §7'ye işle.
2. **DB erişimi gelince:** `HIBOT_DB_EU` / `HIBOT_DB_APAC` ortam değişkenlerini kullanıcı ayarlar (asla repoya yazılmaz). Önce `tenant_lookup` ve `wo_header` ile duman testi yap, sonra FINDINGS §8'deki varsayımları doğrula. Login'in `db_datareader`-only olduğunu teyit et.
3. **AI endpoint dokümanı gelince:** `hibot/llm/client.py` içindeki `_FORMATS` tablosuna adaptör ekle, `--llm http` ile SM-1777386 üzerinde dene, prompt'ları ayarla. Embedding varsa BM25'in yanına ekle (D10).
4. **Bağımsız işler (beklemeden yapılabilir):** (a) "DB izinden öğrenme" modülünün tasarımı ve kodu (D13), DB gelince çalışır; (b) en büyük ~30 grup için taslak kart üretimi (LLM gelince); (c) değerlendirme seti: elle seçilmiş 30–50 ticket ve beklenen sınıflandırma.
