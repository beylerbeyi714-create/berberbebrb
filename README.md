# AI Shorts Factory

n8n merkezli, kendi sunucunuzda çalışan YouTube Shorts üretim sistemi: içerik araştırma → senaryo →
yapay zekâ görsel/video → seslendirme + altyazı → FFmpeg montaj → kalite kontrol → **onay** →
YouTube'a gizli yükleme → analitik → performansa göre yeni fikirler.

## Mimari

```
Panel (factory-api :8000, oturum + bcrypt)          n8n (:5678) — orkestrasyon
        │  onay/ret, ayarlar, maliyet, hatalar          │  zamanlayıcılar, koşullar, yoklama döngüleri,
        ▼                                               │  YouTube OAuth2, hata iş akışı
factory-api (FastAPI)  ◀──── X-API-Key ─────────────────┘
  • sağlayıcı modülleri (LLM / görsel / video / TTS)     ──▶ Anthropic, OpenAI, Gemini, fal, ElevenLabs, Google TTS
  • durum makinesi, tekrar önleme, bütçe koruması, QC
  • PostgreSQL (factory DB)
        │ X-API-Key (yalnızca iç ağ)
        ▼
video-worker (FastAPI + FFmpeg) — 9:16 montaj, Ken Burns, xfade geçiş, loudnorm, gömülü altyazı, ffprobe QC
```

Ağır iş n8n'de değil, servislerde yapılır; n8n iş akışları ince ve okunabilir kalır.
Sağlayıcı değiştirmek tek bir ayardır (Panel → Ayarlar → Sağlayıcılar); kod değişmez.
Yeni sağlayıcı: `services/factory/app/providers/` altında bir sınıf + `@register(...)`.

| Servis | Port (host) | Not |
|---|---|---|
| postgres 17 | — | `n8n` ve `factory` için ayrı kullanıcı/veritabanı |
| n8n 2.45.4 | 127.0.0.1:5678 | sahip hesabı ile giriş; HTTPS için Caddy |
| factory-api | 127.0.0.1:8000 | REST API + panel |
| video-worker | — | yalnızca iç ağ |
| backup | — | günlük `pg_dump` → `data/backups`, 14 gün saklama |
| caddy (`--profile https`) | 80/443 | Let's Encrypt, HSTS |

## Kurulum

```bash
git clone <repo> ai-shorts-factory && cd ai-shorts-factory
scripts/setup.sh                 # .env üretir (rastgele gizli değerler), data/ hazırlar
scripts/set_panel_password.sh    # panel şifresi (bcrypt)
docker compose up -d --build
N8N_OWNER_EMAIL=siz@ornek.com N8N_OWNER_PASSWORD='GüçlüŞifre123' scripts/n8n_bootstrap.sh
```

`n8n_bootstrap.sh`: n8n sahip hesabını oluşturur, 3 kimlik bilgisini (Factory API Key, n8n Webhook Key,
YouTube OAuth2) ve 11 iş akışını içe aktarır, hepsini yayınlar (aktif eder). Tekrar çalıştırılabilir.

Sonra:
1. `.env` içine kullanacağınız sağlayıcı anahtarlarını girin → `docker compose up -d factory-api`
2. Panel (http://localhost:8000) → Ayarlar → Sağlayıcılar'ı seçin
3. YouTube bağlantısı: **[docs/YOUTUBE_OAUTH.md](docs/YOUTUBE_OAUTH.md)**
4. Testler geçince Panel → **Otomasyonu başlat**

**Sunucuda HTTPS:** DNS'te `N8N_DOMAIN` ve `PANEL_DOMAIN`'i sunucuya yönlendirin, `.env`'de
`N8N_PROTOCOL=https`, `N8N_PUBLIC_URL=https://n8n.alanadi`, `N8N_SECURE_COOKIE=true`, `N8N_PROXY_HOPS=1`,
`COOKIE_SECURE=true`, `ACME_EMAIL` ayarlayın → `docker compose --profile https up -d`.
Güvenlik duvarında yalnızca 22, 80, 443 açık kalsın.

TLS denetimli bir proxy arkasında derleme yapıyorsanız: `HTTPS_PROXY=... EXTRA_CA_FILE=ca.crt scripts/build_behind_proxy.sh`.

## n8n iş akışları

| # | Ad | Tetikleyici | Ne yapar |
|---|---|---|---|
| 00 | E2E Pipeline (test) | webhook `asf-e2e` | senaryo (elle veya LLM) → medya → yoklama → ses/altyazı → render → yoklama → QC → özet |
| 01 | Daily Content Research | her gün 07:10 | trend başlıkları + LLM ile 10 fikir, puanlama, tekrar kontrolü, kuyruğa ekleme |
| 02 | Script Generator | 2 saatte bir | en yüksek puanlı fikirden senaryo + sahneler + görsel talimatları + başlık/açıklama/kaynak |
| 03 | AI Media Generator | 5 dk | görsel/video üretimi, iş kimlikleri, yoklama, sınırlı tekrar deneme |
| 04 | Voice And Subtitles | 5 dk | sahne başına TTS, süre ölçümü, zamanlamalı SRT |
| 05 | Video Renderer | 3 dk | video-worker'a render işi, durum takibi |
| 06 | Quality Control | 5 dk | 13 kontrol (aşağıda) |
| 07 | YouTube Publisher | 30 dk + webhook `asf-publish` | kanal bağlantı kontrolü → onaylı videoyu kilitle → resumable upload (gizli) → sonucu kaydet |
| 08 | Analytics Collector | her gün 03:20 | Data API istatistikleri + Analytics API (izlenme süresi, tutma, abone, trafik) |
| 09 | Performance Optimizer | Pazartesi 04:40 | kategori/başlık/giriş karşılaştırması, yeterli veri varsa yeni fikirler |
| 11 | Series Episode Planner | saatlik | aktif serilerde yayın bekleyen bölüm tamponunu doldurur |
| 10 | Error Monitoring | hata tetikleyici + saatlik | tüm iş akışı hatalarını kaydeder; maliyet/kota/takılan iş uyarıları |

Zamanlanmış akışlar her çalışmada `GET /api/should-run` sorar: **otomasyon kapalıysa (varsayılan) hiçbir şey üretmez**;
araştırma/senaryo günlük video hedefine ulaşınca durur. Webhook ile elle tetikleme bu kapıyı atlar.

## Güvenlik ve politika kuralları (kodda zorunlu)

- **Yayın öncesi onay:** onay/ret yalnızca panel oturumuyla yapılır; n8n anahtarı onay veremez (403).
- **Varsayılan gizli yükleme.** Herkese açık/zamanlanmış yayın yalnızca Panel'de *Herkese açık yayına izin ver*
  açıkken ve QC `public_eligible` ise yapılır. Test varlıklarıyla (yerel kart/espeak) üretilen videolar asla herkese açık yayınlanmaz.
- **QC'den kalan video yayınlanmaz**; QC kontrolleri: açılabilirlik, tüm karelerin hatasız çözülmesi (bozuk görüntü taraması), ses varlığı/seviyesi, altyazı, 9:16, görüntü-ses senkronu,
  süre aralığı, siyah kare, tekrar eden sahne (kaynak görsel hash'i), benzersiz çıktı, lisans kayıtları, kaynaklar, metadata.
- **Tekrar önleme:** fikir (normalize hash + benzerlik), senaryo (içerik hash'i), video (senaryo başına bir), render (iş tekilleştirme),
  yükleme (`youtube_video_id` UNIQUE + aktif iş için kısmi UNIQUE indeks + `FOR UPDATE SKIP LOCKED`).
- **Bütçe:** her ücretli çağrıdan önce günlük sınır kontrolü; tüm çağrılar `api_usage`'a.
- **Sahte başarı yok:** anahtarı olmayan sağlayıcı `424 provider_not_configured` döner, adım durur ve hata kaydedilir.
- **Telif:** Mod A (özgün yapay zekâ) varsayılandır. Mod B'de (lisanslı kaynak dönüşümü) her üçüncü taraf varlığın
  `licenses` kaydında `verified=true` ve `proof_url` olmadan QC geçmez. Sistem başka kanalların videolarını indirmez.
  Herkese açık olmak yeniden kullanım izni değildir; kırpma/altyazı/müzik değişikliği tek başına özgünlük sağlamaz.
- **Sentetik içerik beyanı:** `status.containsSyntheticMedia=true` + açıklamada yapay zekâ notu.
- Etkileşim/izlenme satın alma, yapay etkileşim veya platform denetimlerini aşma özelliği **yoktur**.
- Riskli işlemler (silme, yeniden yayınlama, gizlilik değiştirme) otomatik yapılmaz; OAuth kapsamları bunlara izin vermez.
- Gizli değerler: `.env` (600 izin, git dışında); n8n kimlik bilgileri `N8N_ENCRYPTION_KEY` ile şifreli; panel anahtar
  değerlerini göstermez, yalnızca var/yok bilgisini gösterir. CSRF: panel istekleri `X-Panel` başlığı + `SameSite=Strict` çerez.

## Testler

```bash
scripts/run_tests.sh unit    # birim testleri (8)
scripts/run_tests.sh e2e     # n8n webhook → ... → QC (mevcut sağlayıcı ayarlarıyla)
```

Ücretsiz uçtan uca test için Panel → Sağlayıcılar: görsel `local_textcard`, ses `espeak`, video `yok`.
Bu sağlayıcılar **yapay zekâ değildir**; yalnızca zinciri test eder ve QC bunu uyarı olarak işaretler.

## Ayarlar (Panel → Genel)

Günlük video hedefi (3), dil (tr), kategoriler, süre aralığı (20–45 sn), günlük maliyet sınırı (5 $),
tekrar deneme (3), içerik modu (A/B), yükleme gizliliği (gizli), herkese açık yayın izni (kapalı),
manuel onay (açık), sağlayıcılar. Hepsi `settings` tablosunda.

## Seriler (ardışık bölümler)

Her video bir serinin bölümü olabilir: "Bölüm N" girişi, "devamı ▶" kapanışı, açıklamada önceki bölüm bağlantısı,
otomatik oynatma listesi ve sıralı yayın. Kamu malı / lisanslı filmler sahne geçişlerinden bölümlere ayrılır;
sessiz filmler seslendirmesiz, Türkçe altyazı + lisanslı müzikle yayınlanabilir. Ayrıntılar: [docs/SERIES.md](docs/SERIES.md).

## Belgeler

- [docs/YOUTUBE_OAUTH.md](docs/YOUTUBE_OAUTH.md) — Google Cloud + n8n OAuth adımları, kota ve denetim
- [docs/SERIES.md](docs/SERIES.md) — seriler, kamu malı sessiz çizgi filmler, Türkçe altyazı, müzik, Remix
- [docs/COSTS.md](docs/COSTS.md) — tahmini maliyet raporu
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — hata giderme
- `db/001_schema.sql` — veritabanı şeması (11 tablo + settings)
- `n8n/build_workflows.py` — iş akışı JSON üreticisi (`n8n/workflows/*.json` içe aktarılabilir)
