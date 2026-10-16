// HiBot — Jira XML toplu indirici.
// Kullanım: digiport.arcelik.com'da oturum açıkken F12 → Console → bu dosyanın TAMAMINI yapıştır → Enter.
// Tarayıcı "birden fazla dosya indirme" izni isterse "İzin ver" de.
// Sonra: python tools/jira_export/collect.py  (İndirilenler → docs/jira_export/raw, doğrulama)
// Bu dosya tools/jira_export/make_jql.py tarafından üretilir; elle düzenleme.
(async () => {
  const CHUNKS = /*__CHUNKS__*/[];
  const CONCURRENCY = 3;           // aynı anda kaç parça
  const SKIP = ["chunk_01.xml"];   // zaten inmiş olanlar (chunk_01 2026-10-16'da indi)
  const ONLY = [];                 // doluysa sadece bunlar (başarısızları tekrar indirmek için)

  const todo = CHUNKS.filter(c => !SKIP.includes(c.name) && (ONLY.length === 0 || ONLY.includes(c.name)));
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function download(c) {
    for (let attempt = 1; attempt <= 3; attempt++) {
      try {
        const t0 = performance.now();
        const res = await fetch(c.url, { credentials: "include" });
        if (!res.ok) throw new Error("HTTP " + res.status);
        const blob = await res.blob();
        const head = await blob.slice(0, 300).text();
        if (!/<rss|<\?xml/.test(head)) throw new Error("XML değil (oturum düşmüş / login sayfası dönmüş olabilir)");
        const tail = await blob.slice(Math.max(0, blob.size - 100)).text();
        if (!/<\/rss>\s*$/.test(tail)) throw new Error("dosya yarım geldi (</rss> yok)");
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = c.name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 120000);
        const mb = (blob.size / 1e6).toFixed(1);
        const sec = ((performance.now() - t0) / 1000).toFixed(0);
        console.log(`✅ ${c.name}  ${mb} MB  ${sec} sn`);
        return { dosya: c.name, durum: "OK", MB: mb, deneme: attempt };
      } catch (e) {
        console.warn(`⚠️ ${c.name} deneme ${attempt}/3: ${e.message}`);
        await sleep(5000 * attempt);
      }
    }
    return { dosya: c.name, durum: "HATA", MB: "-", deneme: 3 };
  }

  console.log(`HiBot: ${todo.length} parça, ${CONCURRENCY}'erli indiriliyor...`);
  const results = [];
  for (let i = 0; i < todo.length; i += CONCURRENCY) {
    results.push(...await Promise.all(todo.slice(i, i + CONCURRENCY).map(download)));
    await sleep(1500); // Jira'ya nefes aldır
  }
  console.table(results);
  const failed = results.filter(r => r.durum !== "OK").map(r => r.dosya);
  console.log(failed.length
    ? `❌ Başarısız: ${JSON.stringify(failed)} → ONLY = ${JSON.stringify(failed)} yapıp tekrar çalıştır.`
    : "🎉 Hepsi indi. Şimdi: python tools/jira_export/collect.py");
})();
