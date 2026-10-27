# Tahmini maliyet raporu

**Uyarı:** Aşağıdaki birim fiyatlar, kurulum sırasında (Ekim 2026) sağlayıcıların fiyat sayfalarına bu
ortamdan erişilemediği için doğrulanamamıştır; Anthropic fiyatları hariç (Anthropic'in güncel model
tablosundan alınmıştır). Tüm değerler `services/factory/app/config.py → PRICING` içindedir ve
`.env` dosyasında `PRICING_JSON` ile ezilebilir. Gerçek faturayı her zaman sağlayıcı panelinden kontrol edin.
Sistem, her çağrıyı `api_usage` tablosuna bu tahminlerle kaydeder ve **günlük harcama sınırını**
(varsayılan 5 $) çağrıdan *önce* denetler.

## Varsayımlar (video başına)

- 6 sahne (giriş + 5), ~600 karakter anlatım, ~30 sn
- Senaryo: Claude Opus 5.5 (`claude-opus-5-5`, 4 $/MTok giriş, 20 $/MTok çıkış), ~3k giriş + ~2k çıkış token
- Araştırma: günde 1 çağrı (10 fikir), videolara paylaştırılmış

| Kalem | Sağlayıcı | Birim fiyat (tahmini) | Video başına |
|---|---|---|---|
| Araştırma | Claude Opus 5.5 | 4 $/20 $ MTok | ~0,02 $ |
| Senaryo | Claude Opus 5.5 | 4 $/20 $ MTok | ~0,05 $ |
| Görsel (6) | fal FLUX schnell | ~0,006 $/görsel | ~0,04 $ |
| Görsel (6) | OpenAI gpt-image-1 (1024x1536, medium) | ~0,063 $/görsel | ~0,38 $ |
| Video klip (6×5 sn) | fal Kling standard i2v | ~0,056 $/sn | ~1,68 $ |
| Seslendirme | Google Cloud TTS WaveNet | ~16 $/1M karakter | ~0,01 $ |
| Seslendirme | ElevenLabs multilingual v2 | ~0,22 $/1k karakter (plana göre değişir) | ~0,13 $ |
| Montaj, altyazı, QC | FFmpeg (kendi sunucunuz) | — | 0 $ |
| YouTube yükleme/analitik | YouTube Data/Analytics API | ücretsiz (kota) | 0 $ |

## Senaryolar (günde 3 video ≈ ayda 90 video)

| Profil | Bileşim | Video başına | Aylık API |
|---|---|---|---|
| Ekonomik | Opus 5.5 + FLUX schnell + Google TTS | ~0,12 $ | ~11 $ |
| Standart | Opus 5.5 + gpt-image-1 + ElevenLabs | ~0,58 $ | ~52 $ |
| Hareketli | Standart + 6 Kling klibi | ~2,26 $ | ~205 $ |

**Sunucu:** 4 vCPU / 8 GB RAM / 80 GB disk bir VPS (Hetzner, DigitalOcean vb.) ~10–40 $/ay.
Render tek video için ~20–40 sn CPU sürer; günde 3 video için 2 vCPU da yeterlidir.

**Test modu:** `local_textcard` + `espeak` ile maliyet 0 $'dır ancak bu çıktı yayına uygun değildir
(QC uyarı verir ve herkese açık yayını engeller).

## Maliyeti düşürme

- Senaryo kalitesi korunarak efor seviyesi `ANTHROPIC_EFFORT=low` ile denenebilir (ölçerek karar verin).
- Kling klipleri yalnızca giriş sahnesi için kullanmak (kodda `generate_media` içinde sahne filtresi) maliyetin çoğunu keser.
- Günlük sınır: Panel → Ayarlar → Günlük maliyet sınırı.
