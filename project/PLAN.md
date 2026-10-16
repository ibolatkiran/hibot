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
- ✅ 19 parça Jira XML export'u alındı, 3.790/3.790 ticket zenginleşti
- 🔄 Read-only prod DB: EU kullanıcısı hazır. `.env` doldurulup `python -m hibot.db.probe --region EU` çalıştırılacak. APAC bekleniyor.
- 🔄 ArGPT adaptörü hazır (D15). `.env` doldurulup `python -m hibot.llm.probe` ile yanıt şekli doğrulanacak.

### Faz 1: Bilgi tabanı
- ✅ Şema kataloğu: `tools/schema_catalog` → `knowledge/schema`
- ✅ Kod izlenebilirlik haritası: `tools/code_map` → `knowledge/codemap` (%92,8 önyüz→backend eşleşmesi)
- ✅ Incident ön işleme: varlık çıkarımı, yeni sınıflama, BM25 benzerlik, 566 tekrar eden grup, Jira XML ayrıştırıcı (`tools/incidents`, 16 test)
- ✅ Güvenli SQL katmanı + 24 teşhis sorgu şablonu (`hibot/db`, `knowledge/queries`, 193 test). Gerçek DB'de ilk çalıştırma ve varsayım doğrulaması DB erişimi bekliyor ⏳.
- ✅ Jira XML toplu indirici: tarayıcı snippet'i (3'erli) + `collect.py` (`tools/jira_export`)
- ✅ Birleşik bilgi grafı: codemap ↔ şema, ileri ve ters izleme, hata metni araması (`tools/graph`, `hibot/knowledge`, 12 test)
- ⏳ Jira XML ile zenginleştirme (export gelince `tools/incidents/build.py` tekrar çalıştırılır)
- ✅ Ön adım (`wo_locate` ile tenant çözümü), temel sorgu setleri, ticket penceresi izi (manuel DB düzeltmesi tespiti) — tek ticket için canlıda çalışıyor (FINDINGS §12)
- ⬜ **DB izinden öğrenme, toplu çalıştırma** (D13): ticket penceresi analizini kapanmış ve iş emri olan tüm EU ticket'larına uygula → etiketli veri seti ("fiilen ne yapıldı") ve kart adayları.
- ⏳ **DB izinden öğrenme** (D13, orijinal madde): kapanmış ticket'lar için açılış–kapanış aralığında ilgili iş emri ve kayıtlarda ne değiştiğini çıkar (`Log.WorkOrderJsonLog`, `WorkOrderGpsInfo`, audit) → "fiilen yapılan çözüm" etiketi. Gerekenler: prod DB + XML'deki resolved tarihleri.
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
Durum: ArGPT ve EU prod (read-only) bağlı, canlı analiz çalışıyor (FINDINGS §11–12). APAC DB yok.
1. **Kullanıcı onayı:** SM-2139009 teşhisi (tenant 68'de StatusStep 329 → 314 eksik) doğru mu? Doğruysa `python -m hibot feedback 20261016-191209-SM-2139009-08c0 --verdict correct` → **ilk gerçek kart**.
2. **Tekrar tespiti:** benzer ZA ticket'larını bul ("cancel", "No Proof or Money", tenant 68) → aynı kök neden mi?
3. **Toplu DB izi:** kapanmış ve iş emri olan EU ticket'larında ticket penceresi analizini toplu çalıştır (LLM'siz, deterministik), sonuçları `knowledge/learning/` altına yaz, manuel düzeltme kalıplarını grupla → kart adayları.
4. Değerlendirme seti: 30–50 ticket ve beklenen sınıflandırma.
4. **Bağımsız işler (beklemeden yapılabilir):** (a) "DB izinden öğrenme" modülünün tasarımı ve kodu (D13), DB gelince çalışır; (b) en büyük ~30 grup için taslak kart üretimi (LLM gelince); (c) değerlendirme seti: elle seçilmiş 30–50 ticket ve beklenen sınıflandırma.
