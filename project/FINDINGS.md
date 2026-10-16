# Keşif bulguları

Bu dosya alt agent raporlarının kalıcı özetidir. Ayrıntılı ve makine tarafından okunabilir çıktılar `knowledge/*/README.md` dosyalarında. Yeni bulgu eklenince ilgili bölüm güncellenir.

---

## 1. Incident verisi (`docs/kronik_sorunlar_son_1_yil_data.json`), 2026-10-16

**Kapsam:** 3.790 ticket, 13.10.2025 – 25.09.2026, aylık ~220–400 ticket. 233 cluster var ama **ground truth değil** (D3).

**Dağılım**
- service: Sirius %66 (2.490), Manusoft 634, CONTACT 169, Salesforce 120, boş 154
- sub_service: SIRIUS Portal 1.743, CRM 475, Manusoft Global 402, Partner 159, Technician 74. Call Center ve Head Office diye bir değer yok.
- status: Kapalı %82, İptal %8,6. Öncelik: Normal 2.175 / Kritik 953 / Yüksek 662. Ticket'ların %25'i "Kritik", yani öncelik alanı ayırt edici değil.
- app ve sub_service alanları gürültülü (232 boş sub_service). Tek başına güvenilmez.

**Dil:** açıklamalar ~%78 İngilizce, ~%15 Türkçe; Rusça ve Çince azınlıkta, sıklıkla iki dilli. Başlıklarda ülke veya şirket kodu öneki var: AHVN, AHHK, AHSH, AHTW, AHST, AHMY (Arçelik Hitachi iştirakleri).

**Metin kalitesi**
- `desc` **400 karakterde kesilmiş** (ticket'ların %17,5'i bu sınırda). Ortalama 213 karakter.
- %44'ünde iş emri numarası veya "work order" geçiyor, %22'sinde hata kelimesi.
- Stack trace pratikte yok (3.790'da 1 ticket). Ticket'lar teknik log değil, iş diliyle yazılmış kullanıcı şikâyetleri.
- Yüksek sinyalli örnekler:
  - SM-2011812: SMS API 401 Unauthorized
  - SM-1794726: WO SAP'ye gönderilemiyor, `Value ... not allowed for characteristic 'Product'`
  - SM-1967249: "SAP Integration FAILED Docs" raporu `rsProcessingAborted` veriyor
  - SM-1817370: eksik FY dokümanı

**Mevcut cluster'lar**
- 233 cluster'da sadece 19 kategori ve **19 farklı `root_cause` şablonu** var. Bu metinler doğrulanmamış tahminler.
- `general_process_issue` (%27) ve `order_edi_delivery` (%34) fiilen her şeyin atıldığı kovalar.
- 140 cluster'da 3 veya daha az ticket var.
- Tutarlı olanlar sadece SKU/ürün, SMS ve yetki kovaları.

**Tekrar eden somut problemler** (sayılar üst sınırdır, aynı ticket birden fazla grupta sayılabilir)
- Ülkede eksik ürün/SKU/model (~105+)
- "Missing tenant for <SKU>" (Polonya, SAP MM03 ile çözülüyor; 41)
- Yetki/rol talebi (~280), login/şifre/kilitli hesap (~140), yeni kullanıcı açma (~30–50)
- İş emrine parça veya BOM eklenemiyor (26–45), yedek parça görünmüyor (~60), BOM talebi veya düzeltmesi (41)
- İş emri reddetme veya iptal talebi (~26–40), statü değiştirme veya iş emri kapatma (~140)
- Eksik FY dokümanı (14), SAP'ye aktarılamayan iş emri / "SAP Integration FAILED Docs" (20+)
- SMS veya e-posta gitmiyor (~28–41), NPS SMS (28)
- İşçilik tarifesi (geniş eşleşme), Rumence işçilik ve arıza kodları (18), arıza/iş/nakliye kodu eksik (67)
- Bölgeye göre yanlış ASC / servis merkezi ataması (RU, 12), garanti tipi veya süresi (~99), fatura oluşturulamıyor (43)
- Mükerrer partner veya müşteri (22), Technician uygulaması offline / iOS (56), Sirius açılmıyor veya yavaş (46)

**Sonuçlar**
- Başlıkların ~%90'ı tekil. Benzer vaka bulmak için birebir metin değil, anlamsal veya BM25 eşleşmesi gerekiyor.
- Tekrar eden işlerin büyük kısmı **kod hatası değil, ana veri ve yetki talebi**. "Bilinen sorun → bilinen çözüm" kartları için en hızlı kazanılacak alan bu.

**Eksikler:** resolution, yorumlar, kök neden, kapanış tarihi, bağlı commit veya PR, component/label, tam açıklama. Bunlar Jira XML export ile gelecek (D4).

---

## 2. Önyüzler (4 React uygulaması), 2026-10-16

**Ortak özellikler**
- CRA (react-scripts 3), React 16.11, Redux Toolkit + redux-saga, axios 0.21, Material-UI v4, react-router v5 (merkezi `src/routes.js`), Okta.
- Tenant seçimi env profiliyle yapılıyor (arcelik, beko, hitachi, …).
- **Ortak paket yok.** 4 uygulama aynı şablondan kopyalanmış ve zamanla ayrışmış (drift var). Bir hata 1 ile 4 uygulama arasında herhangi birinde olabilir, bu yüzden **her zaman dördüne de bakılmalı**.

| Uygulama | sub_service | Persona | API deseni |
|---|---|---|---|
| SiriusHeadOffice | SIRIUS Portal | Merkez ofis ve admin; katalog, ana veri, muhasebe, raporlar (~212 route) | `src/store/slices/*Slice.js` createAsyncThunk. axios instance'ları: `axiosCatalog` → `/Technician`, `axiosWOBaseUrl` → `/Api`, `axiosHeadOffice` → `/Api/HeadOffice` |
| SiriusCallCenter | SIRIUS CRM | Çağrı merkezi agent'ları; iş emri, email2case, satış (~54 route) | `utils/axios.js` interceptor'ı her URL'nin başına `/Technician` ekliyor; akış view → action → saga → `store/api/*.js` |
| SiriusServiceShopCallCenter | SIRIUS Partner | Servis noktası ve partner (EU/UK); tradepartner, iade, filo, ödeme (~51 route) | CRM ile aynı |
| SiriusTechnician | SIRIUS Technician | Saha teknisyeni, offline çalışabilen PWA (~45 route) | CRM ile aynı + `offlineAxios`, local storage |

**Hata gösterimi ve i18n**
- Merkezi hata yönetimi yok, her ekran hatayı kendisi gösteriyor (snackbar). Kullanıcının gördüğü mesaj ya backend'den gelen `ErrorMessage` ya da önyüzde sabit yazılmış bir metin. Tek merkezi durum: 401 → "Başka bir cihazdan giriş yaptığınız için oturumunuz sonlandırıldı".
- i18n: i18next, düz anahtarlar, `public/locales/<lang>/<ns>.json`.
- **Prod'da çeviriler DB'den geliyor** (`TranslationController`, `Catalog.AppTranslation*`). Yereldeki JSON bir anlık görüntü; ekranda görünen metin ondan farklı olabilir.
- İz sürme yolu: metin → anahtar → `t('<key>')` → view → endpoint.
- Türkçe klasör adları: Tarihce = history, Fis = iş emri, Bulten, Dosya, Uyari, Malzeme.

**Güvenlik:** `translate.js` içinde sabit Okta JWT'si, `.env-cmdrc.json` içinde Scandit, Mule ve VAPID anahtarları var. **Bu dosyalar indekslenmez.**

**Mevcut AI izleri:** HeadOffice'te `views/TicketManagement/AITicketAnalyzer`, Technician'da `WorkOrder/AiSuggestion`. HiBot bunların üzerine inşa edilmiyor (D6).

---

## 3. Backend (`d:\repos\SiriusApi`), 2026-10-16

**Teknoloji**
- ASP.NET Core net5.0, `Startup.cs`, `UseMvc`, SignalR hub'ları.
- 40 controller, `[Route("api/[controller]")]` + `[HttpGet("ActionName/{id}")]`.
- **Endpoint adı ≈ manager metodu ≈ SP adı**, bu yüzden zincir grep ile izlenebiliyor.

**God class:** `Sirius.Business/TechnicianManager/TechnicianManager.cs` 15k+ satır, ~487 SP çağrısı. Önüne `TechnicianController.cs` (~113 action) bağlı. Mobil ve portal endpoint'lerinin çoğu burada.

**Veri erişimi**
- Dapper ile SP çağrıları (759), EF Core (`GFSMContext` scaffold, `CatalogContext` ~98 SP), ADO.NET.
- Satır içi SQL de var: `SELECT * FROM dbo.Fn(...) FOR JSON PATH`.
- **İş mantığının çoğu SP ve fonksiyonlarda.**

**Veritabanları**
- Ana DB `GFSM` (+ `GfsmReadOnlyConnection`); Contact, SARIS AU/NZ, MonaSys, COMET_DW, SAP_SIRIUS_INT, Mongo, Redis.
- Bölge veya ülke ayrımı **TenantId** ile yapılıyor; ülkeye özel şemalar ve SP'ler var. **Her sorguda TenantId filtresi şart.**

**Loglama**
- Serilog sink'leri kodda yapılandırılmamış (dışarıda yapılıyor).
- App Insights (`app-sirius-api`, %5 sampling).
- TransactionId ve AOP log'ları **sadece `Logging:ActiveTenants=[46]`** için aktif. Diğer tenant'larda istek bazlı log pratikte yok.
- **Sonuç: teşhisin ana kaynağı DB log tabloları.**

**Hata yönetimi**
- `ErrorHandlingMiddleware` → 500 `"An unexpected error occurred."`.
- Ağırlıklı desen: metot bazlı try/catch → `ResponseDto{ErrorCode, ErrorDescription=ex.Message}`. Birçok yerde hata yutuluyor (`catch {}`).
- Kullanıcının gördüğü mesajların çoğu SP'deki RAISERROR/THROW'dan veya SAP yanıtından geliyor.

**Entegrasyonlar**
- SAP (SOAP/RFC: FI park doc, ZSD sales order, material availability), Mulesoft (Salesforce, Experian), Oracle TOA, Okta, Azure Graph (mail), Twilio, OneTrust, Sironex, Digiport (Jira), SMS gateway.
- Hosted service'ler: Email2Case dispatcher, MailOrbit, cache invalidation.
- **Hangfire veya Quartz yok.** Zamanlanmış işler SQL Agent veya repo dışındaki servislerde. Incident'lerde repo dışı job'ları da hesaba kat.

**Güvenlik:** `Sirius/appsettings.json` içinde açık metin DB şifreleri ve API anahtarları var. Kullanıcıya bildirildi; **indekslenmez**.

---

## 4. Veritabanı (gfsm-europe EU, gfsm-apac APAC), 2026-10-16

**Proje yapısı**
- SSDT sqlproj, yol `<Schema>/<Type>/<Name>.sql`. Trigger'lar tablo dosyalarının içinde.
- EU: 1.268 tablo, 1.216 SP, 615 fonksiyon, 197 view. APAC bunun alt kümesi: 763 tablo, 999 SP.
- **Aynı çekirdek şema.** Sadece EU'da olanlar: `V2.*`, `Email2Case.*`, ülke şemaları, `dbo.StatusUpdaterLog`, `Operation.OfflineTransactionLog`, `dbo.DiagnosticHistory`.

**Kurallar**
- FK constraint yok. Birleştirmeler her zaman ID + **TenantId** ile yapılır.
- Görünen adlar `Catalog.AppTranslationEntry` (AppTranslationId, LanguageId, FieldText) üzerinden çözülür.

**Ana tablolar**
- `Operation.WorkCase` (vaka) → `Operation.WorkOrder` (iş, 57 kolon, 3 trigger) → `WorkOrderDetail` (satırlar)
- Müşteri: `Consumer*` (PII), `Catalog.Product`, `Material`, `ServiceShop`, `Employee`
- ERP: `ErpDocument`, `WorkOrderErpDetails`, `WorkOrderErpFi`, `WorkOrderErpMm`
- Malzeme: `MaterialOrder*`
- Statü: `Catalog.Status`, `StatusStep` (izin verilen geçişler), `StatusTenant`, `StatusUpdaterData`

**Teşhis tabloları** (tam liste ve kolonlar: `knowledge/schema/diagnostic_tables.md`)

| Tablo | İçerik |
|---|---|
| `Operation.WorkOrderGpsInfo` | Adı yanıltıcı: **iş emri statü geçmişi**. "History ekranı" bunu okuyor. |
| `Log.WorkOrderJsonLog` | WorkOrder değişikliklerinin öncesi ve sonrası (JSON). `TR_WorkOrder_U` trigger'ı doğrudan yazıyor. `dbo.getChangesByWorkOrderId` farkı çıkarıyor. |
| `Log.DB_Errors` | SP hataları (`Util.AddErrorLog`). ErrorMessage içinde `WOId:<id>` geçiyor. |
| `dbo.StatusUpdaterLog` | Otomatik statü job'ı (EU) |
| `Log.RFCCalls`, `Log.RESTEntries` | SAP ve REST çağrıları |
| `Gateway.PostLog`, `PostErrorLog`, `Notification`, `CaseNotification` | Gelen ve giden kuyruklar |
| `ErpDocument.ErrorDescription`, `WorkOrderErpDetails.ErrorDescription` | "SAP'ye gitmedi" vakalarında ilk bakılacak yer |
| `SiriusInt.JobRunLog`, `SiriusIntLog`, `HangFire.*` (DB'de var) | Job geçmişi |
| `Operation`/`Communication.SmsEmailSendingData` | SMS ve e-posta |
| `V2.MobileOperationLog`, `ApiRequestAudit`, `ClientLog`, `Operation.OfflineTransactionLog` | Mobil |

**Güvenlik:** `Security/ReadOnlyDeveloper.sql` mevcut, read-only login için aday. `Password` kolonları var ve SQL katmanında engellenir.

---

## 5. Uçtan uca örnek zincir (doğrulandı)

"Work order history sadece 1 kayıt gösteriyor" (SM-1777386):
```
HeadOffice WorkOrderDetail/History → workOrderHistorySlice.getSiriusWorkOrderHistory  (ProductExchange ekranı da aynı thunk'ı kullanıyor!)
Technician/CRM/Partner WorkOrder/History.js → taskHistoryRequest → saga → store/api/taskHistory.js
  → GET api/Technician/GetSiriusWorkOrderHistory/{WorkOrderId} → TechnicianController.cs:2093
  → TechnicianManager.cs:6131-6145 → SELECT * FROM dbo.GetSiriusWorkOrderHistory(@WorkOrderId,@TenantId,@LanguageId) ORDER BY SysDate DESC FOR JSON PATH
  → okuduğu tablolar: Operation.WorkOrderGpsInfo, WorkOrder, WorkCase, Catalog.Status, AppTranslationEntry, Employee, Role, ServiceShop, Tenant, AppLanguage, CancellationReason, WorkCaseCancellation
```
Teşhis sırası:
1. `WorkOrderGpsInfo` satır sayısı
2. Fonksiyonun döndürdüğü satır sayısı (join, tenant veya dil filtresi kayıp yaratıyor mu?)
3. Önyüz slice'ı (dizi mi tek obje mi; iki kez dispatch ediliyor)

## 6. Kod ile API arasındaki uyumsuzluklar (potansiyel canlı hatalar)

Önyüzün çağırdığı ama backend'de karşılığı olmayan **109 benzersiz çağrı** var, liste: `knowledge/codemap/trace_index.json` → `unmatched_frontend_calls`. Örnekler:
- API'den kaldırılmış legacy `Artemis*` endpoint'leri
- Partner uygulamasının çağırdığı `HeadOffice/GetUserTypes` (API'de `Technician/GetUserTypes`)
- `Technician/payment/create-session` (API'de `Payment`)
- GET/POST uyuşmazlıkları
