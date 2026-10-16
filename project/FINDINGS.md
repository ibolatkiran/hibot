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

## 7. Incident ön işleme sonuçları (`tools/incidents` → `knowledge/incidents`), 2026-10-16

Ayrıntılar: `knowledge/incidents/README.md`, `stats.md`. Bu sonuçlar sadece mevcut dataset'e dayanıyor; Jira XML ile zenginleştirme henüz yapılmadı.

**Yeni problem sınıflaması** (kural tabanlı, kesinlik ~%80)

| Tür | Oran |
|---|---|
| master_data_request | %27,8 |
| access_request | %15,3 |
| functional_defect | %12,7 |
| other | %12,7 |
| data_correction | %11,2 |
| integration_failure | %7,8 |
| report_export | %6,9 |
| performance_availability | %2,2 |
| notification_failure | %2,0 |
| how_to_question | %1,1 |
| mobile_offline | %0,3 |

→ Ticket'ların **%54'ü ana veri, yetki veya veri düzeltme talebi**, yani kod hatası değil. Kod hatası ve entegrasyon kaynaklı olanlar ~%25.

**Varlık çıkarımı**

| Varlık | Kapsama | Kesinlik |
|---|---|---|
| İş emri numarası | %14,4 | ~%100 (elle kontrol) |
| Ülke | %47,4 | ~%100 (açıkça yazıldığında) |
| Ekran tahmini | Sirius ticket'larının %14,7'si | top-1'de ~%40, sadece aday |

**Tekrar eden gruplar** (BM25, edge 0,22 / merge 0,30)
- 566 grup, 2.094 ticket'ı (%55) kapsıyor; en büyüğü 48 ticket. Elle bakılan 10 grubun 8'i temiz.
- En büyükler: Türkçe "Sirius ürün ekleme" (48), Missing Tenant for SKU / PL (44), WO'ya parça eklenemiyor (39), Contact statü değiştirme (27), Portekiz'de Contact SKU kaydı (23), Rumence işçilik ve arıza kodu (23).

**Gizlilik**
- Ham metin saklanmıyor, sadece `text_clean` (placeholder'lı) tutuluyor. Kişiler takma ID'lerle temsil ediliyor.
- ⚠️ Kaynak veride **açık metin şifreler** var (kullanıcılar ticket'a şifre yazmış). Scrubber bunları temizliyor.
- Metin içindeki imza isimleri ("Thanks, X") tam temizlenmiyor.

**Tam Jira zenginleştirmesi (2026-10-16, 19 parçanın hepsi)**

| Ölçüt | Değer |
|---|---|
| Zenginleşen ticket | 3.790 / 3.790 |
| Resolved tarihi olan | 3.573 |
| Çözüm tipi | Done 3.044 · Won't Do 323 · Unresolved 217 · Insufficient Information 206 |
| Ülke tespiti | %47,4 → **%99,2** (`Country Code` alanı sayesinde) |
| Açıklaması 400 karakterden uzun | 541 (artık kesik değil, ortalama 235 karakter) |
| Ticket başına yorum | 3,5 |
| İş emri numarası olan | %15,3 |
| Tekrar eden grup | 568 grup, 2.098 ticket |

**Jira XML**
- Ayrıştırıcı gerçek bir export'la doğrulandı: ilk 78 MB'lık chunk_01, 200 ticket, ortalama 4,8 yorum, resolution ve custom field'lar okundu.
- `Country Code` custom field'ı ülke boşluğunu dolduruyor.
- ~100 custom field'ın çoğu ITSM boilerplate ve filtreleniyor.
- Not: tarayıcıdan "metin olarak" kaydedilen XML işe yaramaz (bkz. `docs/jira_export/README.md`).

## 8. Güvenli SQL katmanı ve sorgu şablonları (`hibot/db`, `knowledge/queries`), 2026-10-16

Ayrıntılar: `hibot/db/README.md`, `knowledge/queries/README.md`. Testler: `python -m unittest discover tests` (193 test; DB ve pyodbc gerekmiyor).

**Bileşenler**

| Modül | Görevi |
|---|---|
| `safe_sql.validate(sql, region=, allowed_params=, allow_pii=)` | Tokenizer tabanlı doğrulama: sadece SELECT / WITH…SELECT, tek statement, katalog allowlist'i (bölgeye göre), TOP enjeksiyonu (varsayılan 200, en fazla 1000), credential kolonları her zaman yasak, ad-hoc SQL'de PII kolonu yasak (şablonlarda serbest ama sonuç maskelenir), `SELECT *` güvenli kolon listesine çevrilir |
| `masking.py` | Sonuçlardaki PII'yı maskeler |
| `templates.py` | Şablonları yükler ve doğrular, parametreleri ODBC parametresi olarak bağlar |
| `runner.py` | Ortam değişkenleri `HIBOT_DB_EU` / `HIBOT_DB_APAC`, `ApplicationIntent=ReadOnly`, READ UNCOMMITTED, 30 sn zaman aşımı, her denemede `logs/query_audit.jsonl` denetim logu (sonuç verisi yazılmaz; log yazılamıyorsa sorgu çalışmaz) |

**24 şablon**
- EU + APAC:
  - İş emri: `wo_header`, `wo_status_timeline`, `wo_history_function`, `wo_field_changes`, `wo_status_transition_check`, `workcase_tree`, `wo_lines_totals`
  - ERP/SAP ve malzeme: `wo_erp_sap`, `sap_rfc_rest_calls`, `material_orders`
  - Hatalar: `db_errors_for_wo`, `db_errors_recent`, `db_errors_list`
  - Bildirim, kuyruk ve job: `notifications_for_wo`, `outbound_queue_backlog`, `job_health`
  - Bağlam ve lookup: `technician_appointment_context`, `product_sku_lookup`, `tenant_lookup`, `user_access_lookup`
- Sadece EU: `wo_status_updater_log`, `mobile_api_trace`, `wo_discount_log`, `wo_debug_log`

**Koordinatör denetimi**
- 16 saldırı denemesinin hepsi reddedildi: stacked query, satır sonundaki yorumla gizlenen ikinci statement, EXEC, SELECT INTO, CTE içinde UPDATE, OPENQUERY, xp_cmdshell, `sys.*`, prosedürü fonksiyon gibi çağırma, WAITFOR, DECLARE/SET, UNION ile Password okuma, Unicode noktalı virgül, ad-hoc PII.
- Geçerli CTE, window function ve TVF sorguları geçiyor. TOP 5000 → 200'e iniyor.
- Tasarım notu: sorgu birincil anahtarla tek satıra gidiyorsa (`Id=@x`) TenantId uyarısı verilmiyor.
- ⚠️ Düzeltme: `Operation.WorkOrder` tablosunda **`UpdateDate` kolonu yok** (§4'teki ilk DB raporu yanılmış). Validator bunu katalogdan doğru yakaladı.

**Gerçek DB'de doğrulanması gereken varsayımlar**
- `WorkOrderErp*.StatusId` → `Catalog.ErpDocumentStatus`
- `SmsEmailSendingStatus` join'i
- `TechnicianId` = `Employee.Id`

**Bilinen boşluklar**
- `ApplicationIntent=ReadOnly` sadece yönlendirme yapar. Asıl garanti **`db_datareader`-only bir login** olmalı; DB erişimi istenirken bu talep edilmeli.
- Maliyet tahmini yok; pahalı sorguları sadece TOP ve zaman aşımı sınırlıyor.
- Nitelenmemiş kolon adları katalogla kontrol edilmiyor.
- Serbest metin içindeki isimler maskelenmeyebilir.

## 9. Birleşik bilgi grafı (`tools/graph` → `knowledge/graph`, `hibot/knowledge/graph.py`), 2026-10-16

Ayrıntılar: `knowledge/graph/README.md`. Kod haritası ve şema kataloğu tek grafta birleşti: 7.487 düğüm, 16.954 kenar, `graph.json` 4,5 MB. Üretim komutu: `python tools/graph/build.py` (~3 sn).

**API:** `CodeGraph.load()` · `trace_endpoint` · `trace_screen` · `trace_db_object` (ters yön; tablo, kolon veya trigger alır) · `search_error_text` · `search_text` (BM25).

**Kapsama**
- Endpoint'lerin %89,6'sı en az bir tabloya ulaşıyor (SP veya inline SQL üzerinden %66,7).
- Backup olmayan SP'lerin %95–97'sinin tabloları çözülmüş.
- Trigger zinciri de izleniyor. Örnek: `SetSiriusWorkOrderStatusUpdate` → `Operation.WorkOrder` → `TR_WorkOrder_U` → `Log.WorkOrderJsonLog`.

**Doğrulama**
- Alt agent 13 zinciri elle kontrol etti, hatalı zincir bulmadı.
- Koordinatör kontrolü:
  - History endpoint'inin 4 uygulamada da çağıranı var; fonksiyon 12 tablo okuyor, hiçbirine yazmıyor.
  - "work order history only one record" serbest metni → doğru endpoint, fonksiyon ve ekran (ilk 3 sonuç).
  - "Failed to change status please check data!" → `WorkOrder.sql:196` (`TR_WorkOrder_U`) ve 89 endpoint.

**Sınırlamalar**
- `search_text` kelime eşleşmesine dayanıyor. Ana veri taleplerinde (ör. "Missing tenant for SKU") ilgisiz SP'ler dönüyor; bu vakaları **kartlar** çözecek.
- Paylaşılan trigger'lar ters izlemede gürültü yapıyor (`via_trigger` ile işaretli ve sonda sıralanıyor).
- ORM tablo adlarında şema belirsizliği var: `WorkOrder` 5 şemada var.
- Portal'daki 337 ekrandan sadece 200'ünün doğrudan route'u var.

## 10. Faz 2 orkestratör iskeleti (`hibot/llm`, `hibot/agent`, `hibot/prompts`), 2026-10-16

Ayrıntılar: `hibot/agent/README.md`, `hibot/llm/README.md`. Sahte LLM ve DB olmadan uçtan uca çalışıyor: `python -m hibot analyze SM-1777386 --llm fake`.

**Akış**
1. intake (Jira key veya serbest metin; varlık çıkarımı)
2. benzer vakalar (BM25 / önceden hesaplanmış)
3. kart eşleşmesi
4. kod bağlamı (CodeGraph)
5. soruşturma döngüsü: LLM JSON aksiyonu → doğrulama → çalıştırma → maskelenmiş gözlem
6. `final_report` → Türkçe Markdown + JSON (`runs/<run_id>/`)

**Aksiyonlar:** `run_query` (şablon), `run_sql` (ad-hoc, safe_sql, PII yasak), `inspect_code` (graf), `read_source` (izinli repolar, secret listesi, `..` yasak, en fazla 120 satır), `final_report`.

**Bütçe:** 8 adım, 6 sorgu, 2 ad-hoc sorgu. Bütçe biterse LLM'den bir kez zorla `final_report` istenir; o da başarısız olursa yedek rapor oluşturulur.

**Güvenilirlik kuralları**
- Varsayılan mod dry-run.
- Çalışmayan sorgu kanıt sayılmaz ve rapor bunu en üstte uyarır.
- Hiç sorgu çalışmadıysa güven skoru en fazla 0,6.

**Geri bildirim**
- `correct`: `suggested_card` onaylı karta dönüşür ya da mevcut karta kanıt olarak eklenir.
- `wrong`: kartın güven skoru düşer.
- Reddedilmiş kart yeniden canlanmaz.
- Demo çalıştırmaları öğrenmeye katılmaz.

**Kartlar:** format `hibot/agent/cards.py` ve README'de. `knowledge/cards/` altında 2 tane **elle yazılmış ÖRNEK taslak** var; bunlar öğrenilmiş değil.

**LLM:** `HttpChatClient` openai, anthropic ve raw formatlarını destekliyor; endpoint'e özgü kısım tek bir `_FORMATS` tablosunda. Şirket endpoint'iyle **henüz test edilmedi**; prompt'lar gerçek bir modelde denenmedi.

**Koordinatör denetimi**
- 287 test geçiyor (1 symlink testi atlanıyor).
- Demo raporu doğru: "0 sorgu çalıştırıldı" uyarısı en üstte, bölge APAC (AHSH ticket'ı) doğru seçildi.
- `read_source` için 5 saldırı denemesinin hepsi reddedildi: appsettings, translate.js, `..` ile atlama, `.ssh`, HiBot'un ham verisi.

**Açık konular**
- Demo, ticket'ta yoksa yer tutucu ID'ler kullanıyor (`tenant_id=1`, WO 12345678).
- Gerekli bir varlık eksikse kartın skoru yarıya düşüyor ve kart adaylardan çıkabiliyor.

## 11. Canlı bağlantılar ve ilk gerçek analiz, 2026-10-16

**ArGPT** (`python -m hibot.llm.probe`)
- HTTP 200, 1,8 sn. Yanıt şekli `{"content": "<metin>", "message": "success"}`.
- Türkçe "sadece JSON döndür" talimatına birebir uydu.

**EU prod DB** (`python -m hibot.db.probe --region EU`)
- Login `gfsm_readonly_usr`, DB `gfsm`. Sadece `db_datareader`; INSERT, UPDATE, DELETE, EXECUTE ve ALTER yetkilerinin hepsi 0. ✅
- `tenant_lookup` 1,2 sn'de gerçek veri döndürdü.
- `.env`'deki bağlantı dizesi `{ODBC Driver 18 …};Server=…` biçimindeydi (`Driver=` yazılmamış). `build_connection_string` artık bunu tolere ediyor.

**İlk canlı analiz:** SM-2122480 ("WO 22197255 Sirius'ta Financed, Atlas'ta Pending Approval (HQ)"), run `20261016-184754-SM-2122480-132f`. LLM şu adımları izledi:
1. `tenant_lookup(CountryCode=TR)` → tenant 86 "Central (Arçelik Hitachi)"
2. `wo_header(TenantId=86, WO=22197255)` → 0 satır
3. "unknown, 0,2" ile pes etti (6 sorgu hakkının 2'sini kullanarak)

**Gerçek:** WO 22197255 **tenant 70 (Egypt)** altında (StatusId 2271). Koordinatör bunu yeni `wo_locate` şablonuyla buldu.

**Dersler**
- ⚠️ **Ticket'taki ülke, ticket'ı açanın ülkesi; iş emrinin tenant'ı değil.** Ülke kodu `tr` başka bir tenant'la da eşleşiyor. → Tenant her zaman iş emrinden öğrenilmeli.
- Eski şablonlar `TenantId`'yi zorunlu tutuyordu, yani tenant bilinmeden iş emri bulunamıyordu. → Yeni **`wo_locate`** şablonu: birincil anahtar veya harici ID ile tüm tenant'larda arar. Tenant kuralının bilinçli tek istisnası; testte `CROSS_TENANT_LOCATORS` olarak işaretli.
- LLM 0 satırı "bulunamadı" sonucu sayıp erken durdu. → Deterministik `wo_locate` ön adımı, prompt kuralları ve erken durma koruması eklendi (`325ec35`).

**Ön adım sonrası canlı çalıştırmalar (Sonnet doğruladı)**
- SM-2122480 → tenant 70 doğru bulundu. Sirius'ta `Financed(Accounted)`, ERP dokümanı "SAP'ye gönderildi". Atlas tarafı bizim DB'de yok, dolayısıyla `unknown` / 0,55 dürüst bir sonuç.
- SM-2139009 → `user_error` / 0,78 ("zaten iptal edilmiş") — **YANLIŞ**, çünkü yüzeysel kaldı. Koordinatörün manuel teşhisi aşağıda.

**SM-2139009: DB izinden gerçek kök neden (D13'ün ilk kanıtı)**
- Ticket: ZA, "Job 22811628 iptal edilemiyor, hata veriyor; statü *No Proof or Money*". Açılış 17.09 08:58, kapanış 17.09 14:42 (+03:00). Tek yorum: "resolved".
- `wo_status_timeline` (tenant 68): son geçmiş satırı 04.08 "No Proof or Money" (329). **Cancelled (314) için geçmiş satırı yok.**
- `Log.WorkOrderJsonLog`: **17.09 11:41:11'de 329 → 314**, ticket kapanmadan 1 dakika önce. `UpdateUserId` önceki değerinde kaldı (17970).
  → Uygulama akışı (geçmiş satırı ve kullanıcı güncellemesi) bypass edilmiş; **developer doğrudan DB'de güncellemiş**.
- `Catalog.StatusStep` (tenant 68): 329'dan izin verilen geçişler sadece 312, 315, 319 ve 335. **329 → 314 tanımlı değil.** Tenant 68'de 314'e giden hiçbir StatusStep satırı da yok.
- **Kök neden adayı:** tenant 68'de "No Proof or Money" statüsündeki işler UI'dan iptal edilemiyor. Workaround elle DB güncellemesi; kalıcı çözüm StatusStep'e 329 → 314 eklemek veya bunun bilinçli bir kısıt olduğunu teyit etmek. → **Bilinen sorun kartı adayı.** Benzer ZA ticket'ları taranmalı.

**Performans sorunu:** `wo_field_changes` prod'da 30 sn'yi aşıyor (`HYT00`). Literal WO ID ile yazılmış basit `Log.WorkOrderJsonLog` sorgusu 1,7 sn'de dönüyor, yani yavaşlık şablonun yapısından veya parametre bağlamadan kaynaklanıyor. Düzeltme alt agent'ta.

## 12. Temel sorgu setleri ve ticket penceresi izi (D13'ün uygulanması), 2026-10-16

**Performans**
- `wo_field_changes`'teki zaman aşımının kök sebebi: `WITH lang AS (… COALESCE(@LanguageId, (SELECT TOP 1 … Catalog.Tenant)))` CTE'si log tablosuyla cross join edilince optimizer `InxWorkOrderId` seek'ini terk ediyor. Parametre bağlama ve RECOMPILE denendi, sebep bunlar değildi.
- Düzeltme: CTE kaldırıldı, `Catalog.Tenant tn` join'i ve APPLY'lar içinde `COALESCE(@LanguageId, tn.LanguageId)` kullanıldı → 0,9 sn.
- ⚠️ Kural: **büyük log tablolarında `lang` CTE'si kullanma.**
- Tüm şablonlar prod EU'da 8 sn'nin altında. En yavaşları: `outbound_queue_backlog` 5,2 sn, `workcase_tree` 4,8 sn. Tablo: `knowledge/queries/README.md`.

**Yeni şablon:** `status_step_check(TenantId, FromStatusId, ToStatusId)` — "A → B geçişi bu tenant'ta tanımlı mı?" sorusunu cevaplar.

**Temel sorgu setleri** (`hibot/agent/playbooks.py`, sadece live modda): ön adımdan sonra ve LLM'den önce deterministik olarak çalışır.

| Ticket türü | Çalışan şablonlar |
|---|---|
| Statü / iptal / reddetme / kapatma | `wo_header`, `wo_status_timeline`, `wo_field_changes`, `wo_status_transition_check`, son değişiklik için `status_step_check` |
| Entegrasyon | kendi seti |
| Bildirim | kendi seti |
| Ana veri | kendi seti |

Bütçe: varsayılan `max_queries` 10, LLM'e her zaman 2 sorgu ayrılıyor. SM-2139009 için prompt 23,6k karakter.

**Ticket penceresi** (`hibot/agent/ticket_window.py` → `report.ticket_window_changes`)
- Zaman dilimleri: Jira `created` UTC+03:00 (3.790 XML ticket'ın hepsinde doğrulandı), `resolved` kendi offset'ini taşıyor, DB tarihleri UTC.
- Pencere: −5 / +15 dk.
- Manuel düzeltme sezgisi: JsonLog'daki statü değişikliğine karşılık gelen bir geçmiş satırı yoksa **medium**; buna ek olarak `UpdateUserId` de değişmemişse **high**.

**Koruma:** statü, iptal veya hata içeren ve iş emri bilinen bir ticket'ta statü geçmişine bakılmadan verilen `final_report` bir kez geri çevriliyor (güven skoru yüksek olsa bile).

**Canlı sonuçlar**
- **SM-2139009** → `config` / 0,86 ✅ (run `20261016-191209-SM-2139009-08c0`). Koordinatörün manuel teşhisiyle birebir aynı: tenant 68'de StatusStep 329 → 314 yok; developer 17.09 11:41:11 UTC'de DB'den elle güncellemiş (high). Çözüm: StatusStep satırını eklemek ve tenant'ın iptal akışını gözden geçirmek. 7 sorgu, 2 LLM çağrısı, ~30 sn.
- **SM-2122480** → `unknown` / 0,40 (run `…191148…e1ed`).
  - Ticket penceresinde değişiklik yok. Mevcut statü 2271 (Financed), ama geçmiş 2284 (Beko Approved) ile bitiyor ve **2271 için geçmiş satırı yok**. Bu muhtemelen ticket'tan *önce* olmuş, akış dışı bir değişiklik (incelenmeli).
  - Atlas tarafı bizim DB'de değil.

**Kalan zayıflıklar**
- Sadece EU'da test edildi.
- ±5 dk eşleşme sezgisi yüzünden geçmiş satırı yazmayan job ve trigger'lar da "manuel düzeltme" diye işaretlenebilir.
- Statü anahtar kelimeleri sadece TR ve EN.
- Ana veri setleri için ülke → tenant eşlemesi belirsiz.
- LLM bazen `status_step_check` bulgusunu `wo_status_transition_check`'e atfediyor.

## 13. Toplu DB izi öğrenmesi (`tools/learning` → `knowledge/learning/summary.md`), 2026-10-16

Ayrıntılar: `knowledge/learning/summary.md`. Deterministik çalışır, LLM kullanılmadı. Kapsam sadece EU.

**Huni:** 577 uygun ticket (kapanmış ve iş emri numarası olan)
- 555'i EU'da bulundu
- 186'sında (%34) ticket penceresinde iş emri değişmiş; 150'sinde statü değişmiş
- **40'ında manuel DB düzeltmesi şüphesi yüksek**
- 369'unda (%66) iş emri hiç değişmemiş (açıklama, konfigürasyon ya da çözüm başka yerde)

Çalışma süresi 43 dk, 2.602 sorgu, 0 hata.

**Taban oran kontrolü:** geçmiş satırı olmayan statü değişikliği ticket pencerelerinin içinde %10 (49/511), aynı iş emirlerinde pencere dışında %2 (82/4.322). Yani sinyal gerçek, ama her işaret manuel düzeltme değil.

**Gerçek ve tekrar eden kalıplar** (StatusStep izin vermiyor, developer elle zorluyor; ticket metinleriyle doğrulandı)

| Kalıp | Ticket | Ticket'taki talep |
|---|---|---|
| T70 Egypt 2271 *Financed(Accounted)* → 2257 *Transfer* | 6 | "change Status to transfer W.O …" |
| T68 South Africa 329 *No Proof or Money* → 314 *Cancelled* | 3 (SM-2139009, SM-2139002, SM-1840263) | "No proof of money, I cannot cancel" |
| T70 Egypt 2271 → 2260 *Work in progress* | 2 | "change state to work in progress" |
| T80 Bosnia 706 *Cancelled by Call Center* → 707 *New Application* | 2 | "change status to New application" |
| T81 Azerbaycan 897 → 888 *Ləğv edildi* (iptal) | 2 | "cancel statüsüne geçirilmesi" |

**Muhtemel gürültü** (job veya trigger davranışı)
- T49 Romania 818 → 821: StatusStep izin veriyor ve değişiklikler zincir halinde (8 sn arayla).
- T46 Poland 841 → 1892: hep aynı saniyede bir geri alma; pencere dışı oranı %32.

İkisi için de taslak kart oluştu ama CAUTION işareti var. **İnsan incelemesinde reddedilmesi önerilir.**

**Taslak kartlar:** `knowledge/cards/draft_footprint_*.json` (7 adet, status draft, güven ≤ 0,7). Her birinde `wo_locate`, `wo_status_timeline`, `wo_field_changes` ve `status_step_check` doğrulama sorguları var. Anahtar kelimeler zayıf, çünkü ticket başlıkları genel ("Sirius Job: …").

**Ürün içgörüsü:** Bu kalıplar, eksik veya kısıtlı statü geçişleri yüzünden developer'ların tekrar tekrar yaptığı elle DB işlerini sayısal olarak gösteriyor. Ya iş birimiyle konuşulup StatusStep'e geçiş eklenmeli, ya da kontrollü bir "admin statü düzeltme" fonksiyonu sunulmalı.

**Sınırlamalar**
- Sadece EU kapsandı; iş emri başına en yeni 200 log satırı okundu.
- Pencere payları sezgisel; takip edilmeyen tablolardaki düzeltmeler iz bırakmıyor.
- Ticket'taki numara başka bir iş emrine ait olabilir.

## 14. Kart incelemesi ve kartla analiz ("bilinen sorun kısa yolu"), 2026-10-16

**CLI:** `python -m hibot cards list | show | confirm | reject | annotate` (`hibot/agent/cardcli.py`).
- `confirm` güveni max(mevcut, 0,85) yapar.
- Her işlem `history`'ye `who: "user"` olarak yazılır.

**Şemaya eklenenler**
- `classification`
- `state_signature`: `{tenant_id, from_status_id, to_status_id, …, target_words[], transition_configured}`. `mine_patterns.py` artık bunu da üretiyor; mevcut taslaklar migrasyonla güncellendi.

**D17 uygulandı:** `draft_footprint_t70_2271_to_2257` → **confirmed**, `user_error`, Türkçe kök neden ve 3 çözüm adımı. İlk onaylı kart.

**Eşleşme** (`hibot/agent/known_issue.py`, sadece live modda, ön adım ve temel sorgu setinden sonra)

| Sinyal | Puan |
|---|---|
| Tenant eşleşmesi (zorunlu) | 0,40 |
| İş emrinin **ticket açıldığı andaki statüsü** = from | 0,35 |
| Ticket metninde hedef kelime geçiyor | 0,25 |
| Kart onaylı | +0,05 |

- Eşik 0,65; son iki sinyalden en az biri şart. Ticket açıldığı andaki statü, değişiklik logu geriye sarılarak hesaplanıyor.
- Eşleşen kartın doğrulama sorguları LLM'den önce çalışıyor; sonuç "destekliyor / çelişiyor / doğrulanamadı".
- Kart onaylı ve doğrulama destekliyorsa rapor kartın sınıflandırma, kök neden ve çözüm adımlarını aynen kullanıyor. LLM'in eklemeleri "[LLM ek önerisi]" etiketiyle ekleniyor; geçmişte elle zorlama yapıldıysa "SÜREÇ İHLALİ" satırı çıkıyor.

**Canlı sonuçlar** (ikisi de geliştirme setinden, yani bunlar bir isabet ölçümü değil)
- **SM-2139002** (ZA) → taslak t68 kartıyla eşleşti (skor 1,00), doğrulama destekliyor → `config` / 0,90. Run `20261016-201636-SM-2139002-657b`.
- **SM-2126600** (Egypt) → **onaylı** t70 kartıyla eşleşti (skor 1,00; açılıştaki statü 2271, sonraki 4 değişiklik geri sarıldı), doğrulama destekliyor → `user_error` / 0,90, kartın adımları aynen kullanıldı, SÜREÇ İHLALİ işaretlendi. Run `20261016-201713-SM-2126600-b628`.

**Zayıflıklar**
- Onaylı kartta LLM eklemeleri ve "Ek bulgular (LLM)" paragrafı çok uzun.
- LLM var olmayan bir kanıt referansı uydurdu (grounding bunu "çalıştırılmadı" diye işaretledi).
- Egypt 2257 ve 2260 kartları hedef kelime geçmeyen bir ticket'ta berabere kalır.
- Kart ID'si onaylandıktan sonra da `draft_` önekini taşıyor (kozmetik).

## 15. İlk isabet ölçümü (baseline_20261016), 2026-10-16

Rapor: `knowledge/eval/results_baseline_20261016.md`. Test seti 40 ticket, canlı (ArGPT + EU prod), 0 hata.

**Başlık metrikleri** (güvenilir etiketler = yüksek + orta, n=30)

| Metrik | Değer |
|---|---|
| İş emri çözümleme | **%95** |
| Tenant çözümleme | **%100** |
| Beklenen bulguların raporda bulunma oranı (78 kontrol) | **%92** |
| Kart eşleşme oranı | %70 |
| Sınıflandırma, kesin / esnek | %40 / %50 |
| **Emin ama yanlış** (güven ≥ 0,7) | **%23 (7 ticket)** |
| Dürüst "bilmiyorum" | %35 |
| Ticket başına ortalama | 23,7 sn · 3,8 sorgu · 3,0 LLM çağrısı |

**Kalibrasyon:** güven 0,7–1,0 aralığında isabet sadece %50 (aşırı özgüven). 0,4–0,7 aralığında %83. 0–0,4 aralığında %0 (bunlar zaten "unknown").

**Yorum**
1. **Kanıt toplama çok iyi, sınıflandırma ve kalibrasyon zayıf.** HiBot doğru iş emrini, tenant'ı ve DB izini neredeyse her zaman buluyor; sorun son etiketi seçmekte ve güven skorunda.
2. **Sınıflandırma şeması belirsiz.** Statü vakalarında kök neden "config" (geçiş yok) ama yapılan iş "data_correction" (elle düzeltme). 7 "emin ama yanlış"ın 3'ü bu belirsizlikten. → **İki eksenli sınıflandırma önerisi:** `root_cause_type` (config / code / master_data / integration / user / access / unknown) + `resolution_action` (data_fix / config_change / code_change / explain / grant_access / escalate).
3. **Pencerede değişiklik olmayan statü vakaları** (2 CW): HiBot "user_error" diyor, etiket "data_correction". Belki düzeltme başka bir tabloda yapıldı; etiketler de belirsiz.
4. **Yetki talepleri:** 4 ticket'ın 3'ü "unknown", **0 sorgu**. Yetki için temel sorgu seti ve kullanıcı kodu çıkarımı yok → en kolay iyileştirme.
5. **Entegrasyon vakaları:** %67 "unknown", iş emri yoksa sorgu yok. Ticket zamanı etrafında `job_health`, `outbound_queue_backlog` ve `db_errors_recent` gibi zaman penceresi sorguları eksik.
6. **Gerçek emin-yanlışlar:**
   - SM-2001523 (bildirim; config bekleniyordu, user_error 0,86 dedi)
   - SM-2128512 (user_error bekleniyordu, access 0,78 dedi)
   - SM-2122464 ve SM-2010200 (pencerede değişiklik yok)
7. **Kod hatası ölçülemiyor:** setteki gümüş etiketlerin hiçbiri güvenilir bir `code_defect` değil.

Etiket notu: 12 ticket "insan incelemesi gerekli" işaretli. Kullanıcı da gerçek çözümü bilmiyor (2026-10-16), etiketler gümüş kalıyor.

## 6. Kod ile API arasındaki uyumsuzluklar (potansiyel canlı hatalar)

Önyüzün çağırdığı ama backend'de karşılığı olmayan **109 benzersiz çağrı** var, liste: `knowledge/codemap/trace_index.json` → `unmatched_frontend_calls`. Örnekler:
- API'den kaldırılmış legacy `Artemis*` endpoint'leri
- Partner uygulamasının çağırdığı `HeadOffice/GetUserTypes` (API'de `Technician/GetUserTypes`)
- `Technician/payment/create-session` (API'de `Payment`)
- GET/POST uyuşmazlıkları
