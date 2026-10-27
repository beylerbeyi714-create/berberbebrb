# YouTube hesabını n8n'e bağlama (OAuth 2.0)

Bu adımlar sizin Google hesabınızla yapılmalıdır; kimse sizin adınıza Google Cloud
Console'da işlem yapamaz. Toplam süre: ~15 dakika.

İstemci sırrını (client secret) ve token'ları sohbetlere, e-postalara veya kaynak koduna
yapıştırmayın. Yalnızca n8n arayüzüne veya sunucudaki `.env` dosyasına girin.

## 0. Önkoşul: n8n'in herkese açık adresi

OAuth yönlendirmesi n8n'e geri döner. Yönlendirme adresi:

```
{N8N_PUBLIC_URL}/rest/oauth2-credential/callback
```

- Yerel test: `http://localhost:5678/rest/oauth2-credential/callback` (Google localhost için HTTP'ye izin verir)
- Sunucu: `https://n8n.alanadiniz.com/rest/oauth2-credential/callback` (HTTPS zorunlu — `docker compose --profile https up -d`)

`.env` içindeki `N8N_PUBLIC_URL` bu adresle birebir aynı olmalıdır.

## 1. Google Cloud projesi

1. https://console.cloud.google.com/ → üst çubuk proje seçici → **New Project** → ad: `ai-shorts-factory` → Create.
2. Proje seçiliyken **APIs & Services → Library**:
   - **YouTube Data API v3** → Enable  (yükleme, video listesi, istatistik)
   - **YouTube Analytics API** → Enable  (izlenme süresi, tutma, abone, trafik kaynağı)

## 2. OAuth onay ekranı (Google Auth Platform)

**APIs & Services → OAuth consent screen** (yeni arayüzde *Google Auth Platform*):

1. **Branding**: Uygulama adı `AI Shorts Factory`, destek e-postası = sizin adresiniz.
2. **Audience**: User type **External**. *Test users* bölümüne YouTube kanalınızın bağlı olduğu Gmail adresini ekleyin.
3. **Data access → Add or remove scopes**, şu üçünü ekleyin:
   - `https://www.googleapis.com/auth/youtube.upload` — video yükleme
   - `https://www.googleapis.com/auth/youtube.readonly` — kanal kontrolü, video istatistikleri
   - `https://www.googleapis.com/auth/yt-analytics.readonly` — YouTube Analytics raporları

   Sistem bilinçli olarak `youtube` (tam yönetim) kapsamını **istemez**: video silme/düzenleme yetkisi yoktur.

> **Önemli — Testing modu:** Uygulama "Testing" durumundayken Google, yenileme
> token'larını **7 gün** sonra geçersiz kılar; yayıncı haftada bir "yetkisiz" hatası verir.
> Kalıcı kullanım için *Audience → Publish app* ile "In production"a alın. Yalnızca kendi
> hesabınızla kullanacaksanız Google doğrulaması olmadan da çalışır; giriş sırasında
> "Google bu uygulamayı doğrulamadı" uyarısı çıkar → *Advanced → Go to AI Shorts Factory*.

## 3. OAuth istemcisi

**APIs & Services → Credentials → Create credentials → OAuth client ID**

- Application type: **Web application**
- Name: `n8n`
- Authorized redirect URIs: 0. adımdaki adres (birden fazla ekleyebilirsiniz: localhost + sunucu)
- Create → **Client ID** ve **Client secret** görüntülenir.

## 4. n8n'e girme

İki yoldan biri:

**A) n8n arayüzü (önerilen)**
1. n8n → **Credentials** → `YouTube OAuth2 (AI Shorts Factory)` (kurulum betiği bunu oluşturdu).
2. Client ID ve Client Secret'ı yapıştırın. *Custom Scopes* açık ve yukarıdaki 3 kapsam dolu olmalı.
3. **Sign in with Google** → kanalın bağlı olduğu hesabı seçin. Birden fazla kanalınız (Brand Account) varsa,
   istenen kanalı seçtiğinizden emin olun → izinleri onaylayın → "Connection successful".

**B) .env üzerinden (yalnızca ilk kurulumda; kimlik zaten varsa betik onu korur ve değiştirmez)** — `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` değerlerini girip
`scripts/n8n_bootstrap.sh` çalıştırın, sonra A-3 adımını (Sign in with Google) n8n arayüzünde yapın.

## 5. Bağlantıyı doğrulama

```bash
curl -s -X POST http://localhost:5678/webhook/asf-publish -H "X-API-Key: $N8N_WEBHOOK_TOKEN" -d '{}'
```

- Onaylı video yoksa: `07 YouTube Publisher` yalnızca kanal kontrolünü yapar. Panel → Hatalar'da
  *"YouTube bağlantısı yok"* kaydı **oluşmuyorsa** bağlantı çalışıyor demektir.
- Test yüklemesi: Panel → Videolar → QC'den geçmiş bir videoyu **Onayla** → yukarıdaki komutu tekrar çalıştırın.
  Video **gizli** (private) yüklenir; Panel'de YouTube bağlantısı ve video kimliği görünür,
  YouTube Studio → İçerik'te "Gizli" olarak listelenir.

## Kota ve politika notları (Ekim 2026 itibarıyla — Console'dan doğrulayın)

- Google'ın Data API revizyon geçmişine göre `videos.insert` maliyeti Aralık 2025'te ~1600'den ~100 birime
  düştü; Haziran 2026'dan itibaren `videos.insert` kendi kota kovasına taşındı. Varsayılan genel kota
  10.000 birim/gün. Gerçek limitinizi **APIs & Services → YouTube Data API v3 → Quotas** sayfasında görün.
  Günde 3 video bu sınırların çok altındadır.
- **Denetim (audit):** 28 Temmuz 2020'den sonra oluşturulan ve YouTube API denetiminden geçmemiş
  projelerden yüklenen videolar YouTube tarafından **gizli** modda kilitlenir. İlk aşamada zaten gizli
  yüklediğimiz için test etkilenmez; herkese açık yayına geçmeden önce YouTube API Services denetim
  formunu doldurun (https://support.google.com/youtube/contact/yt_api_form).
- **Sentetik içerik beyanı:** Yüklemelerde `status.containsSyntheticMedia` alanı gönderilir (varsayılan
  `true`); açıklamaya ayrıca yapay zekâ kullanım notu eklenir.
- `selfDeclaredMadeForKids: false` gönderilir; çocuklara yönelik içerik üretecekseniz bunu değiştirin.
