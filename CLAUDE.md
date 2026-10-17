# HiBot: Sirius Incident Root-Cause Agent

> **Yeni bir agent/oturum olarak buradan başla.** Bu dosya otomatik yüklenir. Devamını sırayla oku:
> 1. [project/PLAN.md](project/PLAN.md): fazlar, şu anki durum, **sıradaki iş**
> 2. [project/DECISIONS.md](project/DECISIONS.md): alınmış kararlar (tekrar tartışma)
> 3. [project/FINDINGS.md](project/FINDINGS.md): keşif bulguları (veri, önyüz, backend, DB)
> 4. [project/WORKLOG.md](project/WORKLOG.md): kronolojik iş günlüğü

## Amaç
Sirius (Arçelik/Beko saha servis platformu) incident'lerini analiz eden bir agent. Developer'a sorunun **tam olarak nereden** kaynaklandığını arkada çalıştırdığı **read-only SQL sorgularının kanıtlarıyla** iletir ve çözüm yolunu gösterir. Benzer vakalardan öğrenir: tekrar eden bir sorunda kök nedeni doğrudan bulur. Uzun vadede tam otomasyon hedefleniyor (Jira'ya otomatik yorum, proaktif log izleme).

## Çalışma şekli (kullanıcı talebi)
- Ana oturum (Opus) planlar, koordine eder, **denetler**. İşleri alt agent'lar yapar.
- **Alt agent'lar her zaman `model: "sonnet"` ile başlatılır** (token bütçesi kısıtlı). İş kapsamı daraltılmaz. İşe uygun agent tipi seçilir: read-only kod keşfi için Explore, script çalıştırılacaksa general-purpose.
- Alt agent raporları sohbette kaybolur. **Her raporun özünü project/FINDINGS.md'ye yaz, WORKLOG ve PLAN durumunu güncelle, commit et.** Kullanıcının git üzerinde tam onayı var.
- Kullanıcıyla Türkçe konuşulur. Kod ve modül README'leri İngilizce yazılır.

## Repo haritası
| Yol | İçerik |
|---|---|
| `docs/kronik_sorunlar_son_1_yil_data.json` | Kaynak veri: son 1 yıla ait 3.790 Jira incident'i (git dışında, PII içeriyor) |
| `docs/jira_export/` | Jira XML export talimatı + JQL parçaları; ham XML'ler `raw/` altına gelecek (git dışında) |
| `tools/schema_catalog/` | sqlproj → DB şema kataloğu üreteci |
| `tools/code_map/` | 4 önyüz + SiriusApi → ekran→endpoint→SP izlenebilirlik haritası üreteci |
| `tools/incidents/` | incident ön işleme (varlık çıkarımı, sınıflama, benzerlik, Jira XML ayrıştırıcı) |
| `tools/jira_export/make_jql.py` | Jira XML export için JQL parçaları |
| `tools/graph/` | codemap + şema → birleşik bilgi grafı üreteci |
| `hibot/db/` | güvenli read-only SQL: validator, maskeleme, şablonlar, runner |
| `hibot/knowledge/` | `CodeGraph`: ekran↔endpoint↔SP↔tablo↔trigger izleme, hata metni ve serbest metin araması |
| `hibot/tools/` + `hibot/mcp_server.py` | **araç katmanı** (D20): `code_search`, `read_file`, `sql`, `run_template`, `graph_*`, `column_writers`, `similar_tickets`, `ticket`, `cards_*`, `ticket_window`, `git_log/blame`. `python -m hibot.mcp_server` = MCP stdio sunucusu (Claude Code kaydı: `docs/mcp/README.md`). |
| `hibot/llm/` | LLM istemcisi (FakeLLM + HTTP; `HIBOT_LLM_URL/_API_KEY/_MODEL/_FORMAT`), JSON çıkarma, doğrulama ve onarım |
| `hibot/agent/` | orkestratör: intake → benzer vakalar → kartlar → kod bağlamı → soruşturma döngüsü → rapor → geri bildirim |
| `hibot/prompts/` | sistem ve aksiyon prompt'ları |
| `knowledge/` | `schema/`, `codemap/`, `graph/`, `incidents/` (üretilir), `queries/` (SQL şablonları), `cards/` (bilinen sorun kartları, **öğrenilen bilgi, git'te**), `feedback/` (git'te) |
| `runs/` | her analizin girdisi, adımları ve raporu (git dışında) |
| `knowledge/skills/` | **araştırma yöntemleri kitaplığı.** Her ticket'ta `investigation_protocol.md` + semptoma uygun skill izlenir. Elle çözülen her vakadan sonra güncellenir (D20). |
| `tests/` | unittest'ler: `python -m unittest discover tests` (~418 test) |

## Kullanım
```bash
python -m hibot analyze SM-1777386 --llm fake            # uçtan uca demo (LLM ve DB gerekmez, dry-run)
python -m hibot analyze --text "..." [--region EU|APAC] [--live] [--llm http]
python -m hibot feedback <run_id> --verdict correct|wrong|partial [--note "..."]
python -m hibot cards list|show|confirm|reject|annotate   # bilinen sorun kartlarını incele
```
`--live` için `HIBOT_DB_EU` / `HIBOT_DB_APAC`, `--llm http` için `HIBOT_LLM_*` gerekir. Bunlar repo kökündeki **git dışı `.env`** dosyasından otomatik yüklenir (şablon: `.env.example`). Şirket LLM'i ArGPT (`HIBOT_LLM_FORMAT=argpt`). Değişken yoksa sorgular dry-run çalışır ve rapor bunu açıkça belirtir.

Bağlantı testleri:
```bash
python -m hibot.llm.probe                 # ArGPT yanıt şekli + JSON uyumu
python -m hibot.db.probe --region EU      # login read-only mı + tenant_lookup duman testi
```
**Agent'lar `.env` dosyasını asla okumaz veya içeriğini yazdırmaz** (D16).

## Bilgi tabanını yeniden üretme
```bash
# repo kökünden, PYTHONIOENCODING=utf-8 ile
python tools/schema_catalog/build.py      # ~5 sn  -> knowledge/schema/
python tools/code_map/build.py            # ~10 sn -> knowledge/codemap/
python tools/incidents/build.py           # -> knowledge/incidents/
python tools/graph/build.py               # ~3 sn  -> knowledge/graph/  (schema + codemap'ten sonra)
```
`knowledge/**/*.json` git'te tutulmaz, bu komutlarla yeniden üretilir. README'ler ve `knowledge/queries/` git'tedir.

## İlgili repolar (read-only, değiştirme)
- Önyüzler (React 16, CRA): `d:\repos\SiriusHeadOffice` (=SIRIUS Portal), `d:\repos\SiriusCallCenter` (=SIRIUS CRM), `d:\repos\SiriusServiceShopCallCenter` (=SIRIUS Partner), `d:\repos\SiriusTechnician` (=SIRIUS Technician)
- Backend: `d:\repos\SiriusApi` (.NET 5, Dapper + SP ağırlıklı)
- DB (SSDT sqlproj): `d:\repos\sirius-eu\gfsm-europe` (EU, üst küme), `d:\repos\gfsm-apac\ahsiriusdbsrv.database.windows.net` (APAC)

## Güvenlik kuralları (kesin)
- DB erişimi **sadece read-only prod**. SQL her zaman `hibot/db/safe_sql.py` doğrulamasından geçer: sadece SELECT, TOP limiti, hassas ve PII kolon engeli. Bağlantı bilgisi ortam değişkenindedir (`HIBOT_DB_EU`, `HIBOT_DB_APAC`), **asla koda ya da loga yazılmaz**.
- Secret içeren dosyalar **indekslenmez ve LLM'e gönderilmez**: `appsettings*.json`, `.env*`, `.env-cmdrc.json`, `translate.js`, `web.config`, `*.pfx`.
- LLM olarak şirketin sağladığı AI endpoint'i kullanılır. **Tool calling desteği yok**, orkestrasyon kendi kodumuzda (bkz. DECISIONS).
