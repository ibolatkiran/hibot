# Kararlar (ADR günlüğü)

Yeni karar en alta eklenir. Format: tarih · karar · gerekçe · kim karar verdi.

| # | Tarih | Karar | Gerekçe | Karar veren |
|---|---|---|---|---|
| D1 | 2026-10-16 | Koordinasyonu Opus yapar, işi alt agent'lar yapar. Alt agent'lar **Sonnet** ile çalışır, kapsam daraltılmaz. | Token bütçesi kısıtlı. Kullanıcı Opus alt agent'ları durdurdu. | Kullanıcı |
| D2 | 2026-10-16 | Kapsamdaki repolar: 4 React önyüz (HeadOffice/Portal, CallCenter/CRM, ServiceShopCallCenter/Partner, Technician), SiriusApi, gfsm-europe + gfsm-apac sqlproj | Kullanıcının "4 önyüz + 1 backend + 1 DB" tarifiyle eşleşiyor, itiraz gelmedi. | Koordinatör (zımni onay) |
| D3 | 2026-10-16 | Mevcut 233 cluster ve `root_cause` metinleri **ground truth değil**. Kendi sınıflamamızı ve gruplamamızı yapıyoruz. | Sadece 19 şablon cümle var, anahtar kelime eşleşmesiyle oluşmuş; büyük cluster'lar tutarsız. | Koordinatör (veri analizine dayanarak) |
| D4 | 2026-10-16 | **Jira API kullanılamıyor.** Tam açıklama, yorum ve çözüm bilgisi **Jira UI → Export → XML** ile çekilecek (19 parça × 200 key). | Kullanıcı API token alamıyor, XML export alabiliyor. | Kullanıcı |
| D5 | 2026-10-16 | DB erişimi **read-only PROD** olacak (kullanıcı yetki aldıracak). | Kanıt sorguları gerçek veride çalışmalı. | Kullanıcı |
| D6 | 2026-10-16 | HiBot **ayrı bir servis**. SiriusApi'deki `TicketController`/`TicketManager/AI`, `dbo.DiagnosticHistory` ve `AITicketAnalyzer` üzerine inşa edilmiyor. | Kullanıcı: "orası bu kadar kapsamlı bir iş değil". İleride çıktı entegrasyonu için bu tablo ve ekran kullanılabilir. | Kullanıcı |
| D7 | 2026-10-16 | LLM olarak **şirket AI endpoint'i** kullanılacak. | Şirket politikası. | Kullanıcı |
| D8 | 2026-10-16 | Endpoint'te **tool calling yok** → orkestrasyon bizim kodumuzda. LLM yalnızca yapılandırılmış JSON üretir, örneğin `{"query_id":..., "params":...}`. Kod bunu doğrular ve çalıştırır. Serbest SQL istisnadır ve sıkı doğrulamadan geçer. | Kullanıcı: "sanırım tool kullanma desteği yok". Ayrıca bu yaklaşım daha deterministik ve güvenli. | Kullanıcı (kısıt) + Koordinatör (tasarım) |
| D9 | 2026-10-16 | Uygulama dili **Python**. Üreteçler sadece stdlib kullanır; runtime'da yalnızca `pyodbc` (lazy import). | LLM ve veri ekosistemi; mevcut üreteçler Python. Kullanıcı itiraz etmedi. **Kullanıcı .NET isterse yeniden değerlendir.** | Koordinatör (öneri, zımni onay) |
| D10 | 2026-10-16 | Embedding desteği belirsiz → benzerlik için önce **BM25 (stdlib)** ve varlık eşleşmesi kullanılır. Endpoint embedding destekliyorsa sonra eklenir. | Bağımlılık yok, çok dilli metinde makul bir başlangıç. | Koordinatör |
| D11 | 2026-10-16 | Git: HiBot yerel repo. `knowledge/**/*.json` (yeniden üretilebilir) ve ham veri (`docs/*.json`, `*.html`, `docs/jira_export/raw/`, PII içerir) **git dışında**. | Büyük ve üretilebilir dosyalar; PII. | Koordinatör (kullanıcı git'e tam onay verdi) |
| D12 | 2026-10-16 | Her iş adımı repoda dokümante edilir: CLAUDE.md → project/*. | Kullanıcı: "contextin dolduğunda senden sonra gelecek agent ne yapacağını bilmeli". | Kullanıcı |

## Açık sorular
- Şirket AI endpoint'i: hangi model, API formatı (OpenAI uyumlu mu?), **embedding** desteği, bağlam penceresi ve oran limitleri. Dokümanı veya örnek istek/yanıtı kullanıcıdan istenecek.
- Prod DB bağlantısı: EU ve APAC için ayrı mı, hangi login, HiBot hangi makinede çalışacak?
- Ülke → TenantId eşlemesi: `Catalog.Tenant` verisi DB erişimiyle doğrulanacak.
