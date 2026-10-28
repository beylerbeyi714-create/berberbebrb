# Hata giderme

Önce: Panel → **Hatalar** sekmesi ve `docker compose logs <servis> --tail 100`.

| Belirti | Neden | Çözüm |
|---|---|---|
| API `424 provider_not_configured` | Seçili sağlayıcının anahtarı `.env`'de yok | Anahtarı ekleyin → `docker compose up -d factory-api`, ya da Panel → Ayarlar'dan başka sağlayıcı seçin |
| API `402 budget_exceeded` | Günlük harcama sınırı doldu | Ertesi gün otomatik açılır veya Panel'den sınırı artırın |
| API `409 blocked` "kuyrukta fikir yok" | İçerik kuyruğu boş | `01 Daily Content Research` çalıştırın veya Panel'den fikir ekleyin |
| `422 aynı senaryo zaten var` / "benzer konu var" | Tekrar önleme | Beklenen davranış; farklı konu/senaryo kullanın |
| Hatalar'da "YouTube bağlantısı yok veya yetkisiz" | OAuth bağlanmamış veya token süresi dolmuş | `docs/YOUTUBE_OAUTH.md`; Testing modunda token 7 günde düşer → uygulamayı "In production"a alın |
| YouTube `403 quotaExceeded` | Günlük kota | Ertesi gün (Pasifik saatiyle gece yarısı) sıfırlanır; Console'dan kota artışı isteyin |
| YouTube `403 forbidden` / video "kilitli gizli" | API projesi denetlenmemiş | YouTube API denetim formunu doldurun |
| n8n/panel günlüğünde `password authentication failed for user "n8n"` (veya `factory`) | Veritabanı kullanıcıları eksik ya da `.env` yeniden üretilmiş | `docker compose up -d` — `db-init` servisi her başlangıçta kullanıcıları oluşturur ve şifreleri `.env` ile eşitler; sonra `docker compose restart n8n factory-api` |
| factory-api günlüğünde `PermissionError: ... '/data/media/...'` | `data` klasörüne konteyner yazamıyor (özellikle macOS) | macOS: `sudo chmod -R a+rwX data` · Linux: `sudo chown -R 1000:1000 data` |
| n8n girişinde "Set up owner account" ekranı çıkıyor | Sahip hesabı oluşmamış | Formu doldurun veya `scripts/n8n_bootstrap.sh`'i e-posta/şifreyle tekrar çalıştırın |
| n8n açılmıyor, günlükte `address '::' is not available` | Docker ağında IPv6 yok | Compose'da `N8N_LISTEN_ADDRESS: 0.0.0.0` (zaten ayarlı) |
| Video `rendering` aşamasında takıldı | video-worker yeniden başladı, iş kayboldu | `05 Video Renderer` bir sonraki çalışmada işi yeniden kuyruğa alır; `10 Error Monitoring` 45 dk sonra takılanları kurtarır |
| Video `uploading` aşamasında takıldı | Yükleme yarıda kesildi | Monitor 45 dk sonra işi `failed` yapıp videoyu `approved`a döndürür (YouTube kimliği yoksa) |
| QC `no_duplicate_scenes` başarısız | İki sahne görseli aynı/çok benzer | Sahneyi yeniden üretin (asset satırını `pending` yapın) veya senaryoyu düzeltin |
| QC `duration_range` başarısız | Seslendirme 20–45 sn dışında | Senaryoyu kısaltın/uzatın; Panel → Ayarlar'dan süre aralığı değiştirilebilir |
| QC `licenses_complete` başarısız | Mod B'de doğrulanmamış lisans | `licenses` kaydına `proof_url` ve `verified=true` ekleyin (yalnızca gerçekten doğruladıysanız) |
| `docker compose build` apt/pip hatası (kurumsal proxy) | TLS denetimli ağ | `HTTPS_PROXY=... EXTRA_CA_FILE=/yol/ca.crt scripts/build_behind_proxy.sh` |
| Docker Hub `429 Too Many Requests` | Anonim çekme sınırı | Bekleyip tekrar deneyin veya `docker login` |
| Panel girişte 429 | 15 dk içinde 10 hatalı deneme | 15 dk bekleyin |
| Panelde grafik yok | Chart.js CDN'i (cdnjs) engelli | Tablolar çalışmaya devam eder; ağ izni verin |

## Elle adım çalıştırma

```bash
KEY=$(grep ^FACTORY_API_TOKEN= .env | cut -d= -f2)
curl -s -X POST localhost:8000/api/steps/media/run  -H "X-API-Key: $KEY"   # media | voice | render | qc
curl -s localhost:8000/api/monitor -H "X-API-Key: $KEY"
```

## Yedekten dönme

```bash
docker compose exec -T postgres pg_restore -U postgres -d factory --clean < data/backups/factory-YYYYMMDD-HHMM.dump
```
`N8N_ENCRYPTION_KEY` kaybolursa n8n kimlik bilgileri geri getirilemez; `.env`'i ayrıca yedekleyin.
