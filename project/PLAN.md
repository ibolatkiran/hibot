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
- ✅ **DB izinden öğrenme, toplu çalıştırma** (D13): 577 EU ticket → 40 manuel düzeltme, 7 taslak kart (`tools/learning`, FINDINGS §13)
- ⬜ Taslak kartların insan incelemesi (onay veya red): T49 ve T46 için red öneriliyor
- ⬜ APAC DB gelince toplu çalıştırmayı APAC için tekrarla
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

## Hedefe uzaklık değerlendirmesi (2026-10-16, kullanıcıyla paylaşıldı)
İlk aşama hedefinin ~%60'ındayız.

| Hedef | Durum |
|---|---|
| Analiz + kanıt sorguları | 🟢 Çalışıyor, ama sadece 3 ticket'ta denendi, **isabet ölçülmedi** |
| "Sorun tam olarak nerede" | 🟡 DB kaynaklı vakalarda iyi; kod hatası vakaları canlıda hiç denenmedi |
| Çözüm yolu | 🟡 Ana veri ve yetki (%43) canlıda denenmedi |
| Öğrenme | 🟡 Mekanizma hazır; geri bildirim sadece CLI'dan |
| Tekrar → doğrudan kök neden | 🟢 Kartla analiz çalışıyor (2 canlı vaka, FINDINGS §14); kart sayısı az |
| Otomasyon | ⬜ |

Kapsam sınırları: ticket'ların ~%34'ü Sirius dışı (Manusoft, Contact, Salesforce; kod ve DB'leri yok). APAC DB yok.

**Kullanıcıyla kararlaştırılan sıra:** (1) değerlendirme seti ve isabet ölçümü → (2) kod hatası vakaları → (3) ana veri ve yetki → (4) developer'a teslim + tek tık geri bildirim → (5) APAC, sonra otomasyon.

## Sıradaki iş (bir sonraki agent için)
Durum: ArGPT ve EU prod (read-only) bağlı, canlı analiz çalışıyor (FINDINGS §11–12). APAC DB yok.
1. **Kart incelemesi (kullanıcı veya ekip):** 7 taslak kart (FINDINGS §13). Onay ve red için bir CLI komutu gerekebilir (`hibot cards review`). SM-2139009 kalıbı toplu analizle 3 ticket'ta doğrulandı.
2. **Kartların analizde kullanımı:** yeni bir ticket draft veya confirmed bir kartla eşleştiğinde kartın doğrulama sorguları otomatik çalışmalı → "bilinen sorun" kısa yolu. Canlı test: SM-2139002 ve SM-2126600.
3. Değerlendirme seti: 30–50 ticket ve beklenen sınıflandırma (toplu sonuçlardan yarı otomatik).
4. Faz 4 hazırlığı: yeni ticket alımı (API yok → bildirim e-postaları).
4. **Bağımsız işler (beklemeden yapılabilir):** (a) "DB izinden öğrenme" modülünün tasarımı ve kodu (D13), DB gelince çalışır; (b) en büyük ~30 grup için taslak kart üretimi (LLM gelince); (c) değerlendirme seti: elle seçilmiş 30–50 ticket ve beklenen sınıflandırma.
