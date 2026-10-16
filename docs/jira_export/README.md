# Jira XML Export Talimatı

Jira API token'ı olmadığı için (bkz. DECISIONS D4) incident'lerin tam açıklaması, yorumları, çözüm ve kapanış bilgisi Jira arayüzünden **XML export** ile alınır.

## Neden XML?
Elimizdeki `kronik_sorunlar_son_1_yil_data.json` dosyasında açıklama 400 karakterde kesilmiş, yorum ve çözüm alanı da yok. XML export ise tam açıklamayı, **tüm yorumları**, resolution alanını, resolved tarihini, component, label ve custom field'ları içerir.

## Adımlar (19 parça, her biri 200 key)

**Yöntem A: Doğrudan URL (en hızlısı)**
1. Tarayıcıda digiport.arcelik.com'a giriş yap.
2. `jql/urls.txt` dosyasındaki her URL'yi sırayla tarayıcıda aç. Her biri bir XML dosyası indirir ya da gösterir.
3. Gelen dosyayı `raw/chunk_01.xml`, `raw/chunk_02.xml`, … adıyla kaydet. Tarayıcıda açılırsa Ctrl+S ile kaydedebilirsin.

**Yöntem B: Arayüzden**
1. Issues → Search for issues → **Advanced** (JQL) moduna geç.
2. `jql/chunk_01.jql` dosyasının içeriğini yapıştırıp ara.
3. Sağ üstte **Export → XML** seç.
4. Dosyayı `raw/chunk_01.xml` olarak kaydet. Diğer 18 parça için tekrarla.

## Kontrol
- Her dosyada ~200 `<item>` olmalı. Son parça (chunk_19) daha küçük.
- Toplamda 3.790 ticket bekleniyor. Erişim yetkisi olmayan ticket'lar eksik gelebilir; ayrıştırıcı bunları raporlar.
- Yorumlar gelmiyorsa: Jira yöneticisi XML görünümünde yorumları kapatmış olabilir. O durumda bildir.

## Sonra
```bash
python tools/incidents/build.py   # raw/*.xml dosyalarını otomatik bulur ve ticket'ları zenginleştirir
```

## Notlar
- `raw/` klasörü **git dışında**. Kişisel veri (isim, e-posta, müşteri bilgisi) içerir.
- JQL parçalarını yeniden üretmek için: `python tools/jira_export/make_jql.py --chunk 200`. URL hata verirse (`414` / `400`) parça boyutunu küçült, örneğin `--chunk 100`.
