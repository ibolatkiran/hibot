# İş günlüğü

En yeni giriş en üstte. Her iş grubundan sonra güncellenir.

## 2026-10-16: Oturum 1 (koordinatör: Opus 5.5)

**Yapılanlar**
1. Keşif. Sonnet alt agent'larla 4 paralel iş yapıldı: incident veri analizi, 4 önyüz haritası, SiriusApi haritası, DB haritası. Sonuçlar → FINDINGS.md §1–4.
   - İlk deneme Opus alt agent'larla başlatılmıştı. Kullanıcı token nedeniyle durdurdu ve Sonnet ile yeniden başlatıldı (D1).
2. Kullanıcı kararları alındı (D4–D8): Jira API yok → XML export; read-only prod DB; HiBot ayrı servis; şirket AI endpoint'i, tool calling yok.
3. **Şema kataloğu** (`tools/schema_catalog`, Sonnet). Koordinatör kontrolü: `dbo.GetSiriusWorkOrderHistory`, `Operation.WorkOrder` ve `dbo.StatusUpdater` kaynak SQL ile tutarlı; çıktılarda secret yok, "Password" eşleşmeleri sadece kolon adı.
   - Alt agent 4 parser hatasını kendi düzeltti: CRLF GO ayırıcısı, `Function[dbo]` biçimi, köşeli parantez içindeki parantezler, uzun başlık yorumları.
4. **Kod haritası** (`tools/code_map`, Sonnet). %92,8 benzersiz çağrı eşleşmesi; 11 backend ve 4 saga zinciri elle doğrulandı. Koordinatör kontrolü: history zinciri `trace_index.json` içinde doğru; secret yok.
5. `tools/jira_export/make_jql.py`: 3.790 key için 19 JQL parçası ve XML export URL'leri (~2,9 KB). Talimat: `docs/jira_export/README.md`.
6. Git repo, `.gitignore`, CLAUDE.md, project/ dokümanları.

7. **Incident ön işleme** (`tools/incidents`, Sonnet). Bulgular → FINDINGS §7. Koordinatör kontrolü: 16 test geçiyor, `stats.md`'de PII yok (eşleşenler SKU kodu).
   - Kullanıcının ilk chunk_01.xml export'u (78 MB) doğruydu ve ayrıştırıcı onunla doğrulandı.
   - İkinci kayıt (18,9 MB) tarayıcıdan düz metin olarak kaydedilmiş, etiketsiz ve ayrıştırılamıyor. Kullanıcıya bildirildi, talimata uyarı eklendi.

8. Yeniden kaydedilen chunk_01.xml (24,6 MB, `<rss` ile başlıyor) doğrulandı: 200/200 ticket, 960 yorum, 198 resolved tarihi, zenginleştirme çalışıyor. Kullanıcıya diğer 18 parçayı aynı şekilde indirmesi söylendi.
9. Ground truth araştırması: Jira yorumları zayıf. Git'te sadece 14 ticket'a commit referansı var, DB sqlproj'larının git geçmişi yok. → **D13** (DB izinden öğrenme + HiBot'un ürettiği kartlar + tek tık geri bildirim) ve **D14**.

10. Jira XML toplu indirici (koordinatör yazdı): `download_snippet.js` (DevTools Console, 3'erli, XML ve bütünlük kontrolü, yeniden deneme) + `collect.py` (İndirilenler → raw, key doğrulaması). Snippet kullanıcının tarayıcısında henüz çalıştırılmadı.
11. **Güvenli SQL katmanı** (`hibot/db`, 24 şablon, 193 test; Sonnet). Koordinatör denetimi: 16/16 saldırı reddedildi, geçerli sorgular geçiyor, secret yok. → FINDINGS §8. Not: `WorkOrder.UpdateDate` kolonu yok, FINDINGS §4'teki ilk rapor yanlıştı.
12. Jira snippet'i kullanıcıda hata verdi: chunk 02–04 "XML değil" diye reddedildi. Sebep: Jira, `<rss`'ten önce ~500 karakterlik bir yorum bloğu yazıyor; kontrol ilk 300 bayta bakıyordu. Düzeltme: 16 KB'a bakılıyor, hata mesajına teşhis bilgisi eklendi (`d8f269c`). `collect.py`'da aynı düzeltme yapıldı.
13. **Birleşik bilgi grafı** (Sonnet). Koordinatör denetimi: history zinciri, serbest metin araması ve hata metni araması doğru. → FINDINGS §9.
14. **Faz 2 orkestratör iskeleti** (Sonnet). Koordinatör denetimi: 287 test geçiyor, demo uçtan uca çalışıyor, `read_source` 5/5 saldırıyı reddetti. → FINDINGS §10. `.gitignore`: `runs/` dışarıda; `knowledge/cards/*.json` ve `feedback/` git'te (öğrenilen bilgi).
15. Kullanıcı 19 Jira XML parçasını düzeltilmiş snippet'le indirdi (collect: hepsi 200/200, son parça 190/190). `tools/incidents/build.py` → 3.790/3.790 zenginleşti, ülke tespiti %99,2. → FINDINGS §7.
16. ArGPT endpoint bilgisi geldi (D15). Koordinatör yazdı:
    - `argpt` adaptörü (`hibot/llm/client.py`)
    - `.env` yükleyici (`hibot/envfile.py`, D16) ve `.env.example`
    - `hibot/llm/probe.py`, `hibot/db/probe.py` (yetki kontrolü + `tenant_lookup` duman testi)
    - `tests/test_argpt.py` (8 test)
    - pyodbc 5.3.0 kuruldu; makinede ODBC Driver 18 for SQL Server var.
17. Kullanıcı `.env`'i doldurdu.
    - ArGPT probu geçti.
    - DB probu: önce `IM002` / `08001` hataları alındı (bağlantı dizesinde `Driver=` eksikti), normalleştirme ile çözüldü. Login read-only doğrulandı.
    - Ek düzeltmeler: unittest altında `.env` yüklenmiyor (bir CLI testi `--live` ile prod'a gidebiliyordu); runner artık `pyodbc`'yi import etmiyor.
18. **İlk canlı analiz** (SM-2122480) yanlış tenant ile erken pes etti. Koordinatör `wo_locate` şablonunu yazdı; WO'nun tenant 70 (Egypt) altında olduğunu buldu. → FINDINGS §11.
19. Ön adım, prompt kuralları ve erken durma koruması (Sonnet, `325ec35`). Canlı çalıştırmada SM-2139009 "user_error" çıktı; koordinatör prod'da elle teşhis etti (FINDINGS §11): manuel DB düzeltmesi + eksik StatusStep 329 → 314.
20. Şablon performansı, temel sorgu setleri, ticket penceresi izi, yüzeysel teşhis koruması (Sonnet). Koordinatör denetimi: 329 test geçiyor; SM-2139009 raporu manuel teşhisle birebir (`config` / 0,86). → FINDINGS §12.
21. **Toplu DB izi öğrenmesi** (Sonnet, LLM yok): 577 ticket, 43 dk, 0 hata. 40 yüksek manuel düzeltme şüphesi, 7 taslak kart. Koordinatör denetimi: 341 test geçiyor; Egypt, ZA, BA ve AZ kalıpları ticket metinleriyle doğrulandı; T49 ve T46 gürültü. Alt agent `tests/test_agent.py`'de örnek kart testini `batch_footprint` kartları için gevşetti (kabul edildi). → FINDINGS §13.
22. Kullanıcı kararı D17: Egypt 2271 → 2257 bilinçli kısıt. Kullanıcı değerlendirme setini onayladı (öncelik sırası PLAN'da).
23. **Kart CLI'ı ve kartla analiz** (Sonnet). Egypt kartı onaylandı. SM-2139002 ve SM-2126600 canlıda doğru kartla eşleşti, doğrulama destekliyor. Koordinatör denetimi: 367 test geçiyor, Egypt raporu doğru. → FINDINGS §14.
24. Devam eden (Sonnet): değerlendirme seti (~40 ticket, ayrılmış test seti), çalıştırma aracı ve puanlama (`tools/eval`, `knowledge/eval`). Canlı ölçüm koordinatör tarafından sonra başlatılacak.

**Kullanıcıdan beklenen:** Jira XML export (19 parça), prod DB read-only erişimi, AI endpoint dokümanı.
