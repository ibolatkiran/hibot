# Jira XML Export Talimatı

Jira API token'ı olmadığı için (bkz. DECISIONS D4) incident'lerin tam açıklaması, yorumları, çözüm ve kapanış bilgisi Jira arayüzünden **XML export** ile alınır.

## Neden XML?
Elimizdeki `kronik_sorunlar_son_1_yil_data.json` dosyasında açıklama 400 karakterde kesilmiş, yorum ve çözüm alanı da yok. XML export ise tam açıklamayı, **tüm yorumları**, resolution alanını, resolved tarihini, component, label ve custom field'ları içerir.

## Adımlar (19 parça, her biri 200 key)

**Yöntem 0: Toplu indirici (önerilen, ~10–15 dk)**
1. digiport.arcelik.com'da oturum aç. Herhangi bir Jira sayfasında **F12 → Console** aç.
2. [`download_snippet.js`](download_snippet.js) dosyasının **tamamını** yapıştırıp Enter'a bas. Chrome yapıştırmayı engellerse önce `allow pasting` yaz.
3. Snippet parçaları 3'erli indirir, her dosyanın XML olduğunu ve tam geldiğini kontrol eder, hata olursa 3 kez dener. Tarayıcı "birden fazla dosya indirme" izni isterse **İzin ver** de.
4. Bitince Console'da bir özet tablo görünür. Başarısız parça varsa snippet'teki `ONLY = [...]` satırını onlarla doldurup tekrar çalıştır.
5. `python tools/jira_export/collect.py`: İndirilenler klasöründeki `chunk_*.xml` dosyalarını doğrular (beklenen key'ler tam mı) ve `raw/` altına taşır.

Snippet API token kullanmaz. Senin tarayıcı oturumunla, elle tıklamakla aynı export adresini çağırır. Hiçbir cookie veya secret dosyaya yazılmaz.

**Yöntem A: Doğrudan URL**
1. Tarayıcıda digiport.arcelik.com'a giriş yap.
2. `jql/urls.txt` dosyasındaki her URL'yi sırayla tarayıcıda aç. Her biri bir XML dosyası indirir ya da gösterir.
3. Gelen dosyayı `raw/chunk_01.xml`, `raw/chunk_02.xml`, … adıyla kaydet. Tarayıcıda açılırsa Ctrl+S ile kaydedebilirsin.

**Yöntem B: Arayüzden**
1. Issues → Search for issues → **Advanced** (JQL) moduna geç.
2. `jql/chunk_01.jql` dosyasının içeriğini yapıştırıp ara.
3. Sağ üstte **Export → XML** seç.
4. Dosyayı `raw/chunk_01.xml` olarak kaydet. Diğer 18 parça için tekrarla.

## ⚠️ Kaydetme tuzağı
Tarayıcı XML'i ekranda gösterdiğinde **Ctrl+S "sadece metin" kaydedebilir**. O zaman dosyada `<item>` gibi etiketler olmaz ve ayrıştırıcı 0 kayıt okur. 2026-10-16'daki ikinci `chunk_01.xml` (18,9 MB) böyleydi. Doğru yöntemler:
- URL'nin linkine sağ tıklayıp **"Bağlantıyı farklı kaydet"** seç, ya da
- Issue Navigator'da **Export → XML** seç. Dosya doğrudan indirilir.

Doğru dosya `<?xml` veya `<rss` ile başlar. 200 ticket için ~50–80 MB normaldir.

## Kontrol
- Dosyanın formatını kontrol et: `python tools/incidents/build.py` her dosya için okunan item sayısını yazar. `XML parse error after 0 items` görürsen dosya yanlış kaydedilmiştir.
- Her dosyada ~200 `<item>` olmalı. Son parça (chunk_19) daha küçük.
- Toplamda 3.790 ticket bekleniyor. Erişim yetkisi olmayan ticket'lar eksik gelebilir; ayrıştırıcı bunları raporlar.
- Yorumlar gelmiyorsa: Jira yöneticisi XML görünümünde yorumları kapatmış olabilir. O durumda bildir.

## Sonra
```bash
python tools/incidents/build.py   # raw/*.xml dosyalarını otomatik bulur ve ticket'ları zenginleştirir
```

## Yeni bir ticket'ı analiz etmek (veri setinde olmayan)
1. Tarayıcıda (digiport oturumu açıkken) şu adresi aç: `https://digiport.arcelik.com/si/jira.issueviews:issue-xml/SM-XXXXXXX/SM-XXXXXXX.xml`
2. Gelen dosyayı **"Farklı kaydet"** ile `docs/jira_export/inbox/SM-XXXXXXX.xml` olarak kaydet (metin olarak değil; `<rss` içermeli).
3. `python -m hibot analyze SM-XXXXXXX --live`: ticket veri setinde yoksa XML otomatik olarak inbox'tan okunur. Başka bir yerdeki dosya için `--xml <yol>` kullanılabilir.

`inbox/` git dışındadır (kişisel veri içerir).

## Notlar
- `raw/` klasörü **git dışında**. Kişisel veri (isim, e-posta, müşteri bilgisi) içerir.
- JQL parçalarını yeniden üretmek için: `python tools/jira_export/make_jql.py --chunk 200`. URL hata verirse (`414` / `400`) parça boyutunu küçült, örneğin `--chunk 100`.
