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
14. Devam eden: Faz 2 orkestratör iskeleti (Sonnet): `hibot/llm`, `hibot/agent`, `hibot/prompts`, CLI, FakeLLM demosu.

**Kullanıcıdan beklenen:** Jira XML export (19 parça), prod DB read-only erişimi, AI endpoint dokümanı.
