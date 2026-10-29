# Seriler — ardışık izlenen Shorts

Her video bir serinin bölümüdür. Bölümler birbirinin devamıdır, sırayla yüklenir ve serinin YouTube
oynatma listesine eklenir. İzleyici bir bölümü bitirince "Bölüm N+1 ▶" kartını ve açıklamadaki
bağlantıları görür; bu, kanalda ardışık izlenmeyi artırır.

| Yol | Seri türü | Ne yapar | Otomatik mi? |
|---|---|---|---|
| 1 | **Kaynak film kesitleri** (`pd_clips`) | Kamu malı / lisanslı bir film sahne geçişlerinden 18–34 sn'lik bölümlere ayrılır, baştan sona sırayla yayınlanır | Evet |
| 2 | **Özgün seri** (`original`) | Yapay zekâ her bölümü önceki bölümlerin özetiyle yazar; bölüm "devamı" kartıyla biter | Evet (LLM anahtarı gerekir) |
| 3 | **YouTube Remix** | YouTube uygulamasında başka bir videodan birkaç saniyelik kesitle Short yapılır | Hayır — YouTube yalnızca uygulama içinden izin verir |

## Bölümün yapısı (Yol 1)

```
[Giriş kartı  "BÖLÜM 3"  ~3 sn] → [film kesiti 18–34 sn, kırpılmadan 9:16, kenarlar bulanık] → [Kapanış kartı "BÖLÜM 4 ▶"  ~3 sn]
```

- Her serinin kendine özgü, sabit bir renk teması vardır.
- **Seslendirmeli** seride kartlar Türkçe okunur; **sessiz** seride kartlar yalnızca yazıdır.
- Film kesitine düşen **Türkçe altyazılar** otomatik olarak kesilir ve bölüm zamanına kaydırılır.
- Açıklamaya: "◀ Önceki bölüm", "▶ Tüm bölümler (oynatma listesi)", kaynak ve lisans eklenir.
- Bölüm N, bölüm N-1 YouTube'a yüklenmeden yüklenmez. Her seride yayın bekleyen en fazla
  "tampon" kadar (varsayılan 2) bölüm hazır tutulur.

## Sessiz klasik çizgi filmler (seslendirmesiz, Türkçe altyazılı)

### Hangi filmler güvenli?

Bir filmin **ABD'de kamu malı olması Türkiye'de de serbest olduğu anlamına gelmez.** Türkiye'de (FSEK)
koruma, eserin **son yaşayan yaratıcısının ölümünden 70 yıl sonra** biter. 2026 itibarıyla bu,
yaratıcıları **1956'dan önce ölmüş** filmler demektir. Aşağıdakiler hem ABD'de kamu malı hem de bu
kurala uyan, sessiz dönem öncülerinin filmleridir:

| Yaratıcı (ölüm) | Film (yıl) | Not |
|---|---|---|
| Winsor McCay (1934) | *Little Nemo* (1911) | Başında canlı çekim bölüm var |
| Winsor McCay (1934) | *How a Mosquito Operates* (1912) | |
| Winsor McCay (1934) | *Gertie the Dinosaur* (1914) | ~12 dk → ~25 bölüm |
| Winsor McCay (1934) | *The Sinking of the Lusitania* (1918) | Savaş/ölüm içerir — çocuk serisine **uygun değil** |
| Émile Cohl (1938) | *Fantasmagorie* (1908) | Ara yazı yok, ~2 dk |
| Émile Cohl (1938) | *Le Cauchemar du fantoche*, *Un drame chez les fantoches* (1908) | |
| J. Stuart Blackton (1941) | *The Enchanted Drawing* (1900), *Humorous Phases of Funny Faces* (1906) | Çok kısa |

**Bunlar hukuki görüş değildir.** Her filmi yayınlamadan önce kendiniz doğrulayın ve kanıt bağlantısını
kaydedin. Dikkat edilecekler:

- **Sonradan eklenmiş müzik:** İnternetteki kopyalara yakın zamanda müzik eklenmiş olabilir ve o müzik
  telifli olabilir. Bu yüzden sessiz serilerde **"Filmin kendi sesini kullan" kapalı** olmalı; yerine
  YouTube Ses Kitaplığı'ndan müzik seçin.
- **Restorasyon şirketleri:** Bazı restore edilmiş kopyalar için Content ID hak talebi gelebilir.
  Gelirse, kaydettiğiniz kanıt bağlantısıyla itiraz edebilirsiniz.
- Felix the Cat, Oswald, erken Mickey Mouse (*Steamboat Willie*) gibi filmler ABD'de kamu malı olsa da
  yaratıcıları 1956'dan sonra öldüğü için **Türkiye'de hâlâ korunuyor olabilir** — bunları kullanmayın.

### Nereden indirilir?

- [Public Domain Review — Gertie the Dinosaur](https://publicdomainreview.org/collection/gertie-the-dinosaur-1914):
  MP4 indirme bağlantısı ve kamu malı bilgisi aynı sayfada (bu sayfa "kanıt bağlantısı" olarak kullanılabilir).
- [archive.org](https://archive.org): yalnızca "Public Domain Mark" veya açık kamu malı notu olan kayıtları kullanın.

### Türkçe altyazı dosyası nasıl hazırlanır?

Filmin tamamı için **tek bir** `.srt` dosyası hazırlayın; sistem her bölüme düşen kısmı kendisi keser.
Sessiz filmlerde yalnızca ara yazıların (intertitle) çevirisi yeterlidir:

```
1
00:00:42,000 --> 00:00:46,500
Gertie, dünyadaki tek eğitimli dinozor!

2
00:01:15,000 --> 00:01:18,000
Gertie, eğil ve selam ver!
```

- Zaman biçimi: `saat:dakika:saniye,milisaniye`. Ara yazının ekranda göründüğü anı film oynatıcınızda görebilirsiniz.
- Dosyayı UTF-8 olarak kaydedin (Türkçe karakterler için) ve filmle aynı klasöre koyun: `data/sources/gertie.tr.srt`.
- İngilizce ara yazıları bir listede bana gönderirseniz çevirip SRT'ye dönüştürebilirim.

### Arka plan müziği (YouTube Ses Kitaplığı)

1. [YouTube Studio](https://studio.youtube.com) → sol menü **Ses Kitaplığı** (Audio Library).
2. Lisans türü **"Atıf gerekmez"** olan, çocuk içeriğine uygun neşeli bir parça seçin ve **İndir**'e basın.
3. Dosyayı `data/music/` klasörüne koyun (ör. `data/music/neseli_piyano.mp3`).
4. Seri oluştururken müzik dosya adını, lisans bağlantısı olarak Ses Kitaplığı sayfasını girin ve onay kutusunu işaretleyin.

## Panelden seri kurma (Yol 1)

1. Filmi `data/sources/` klasörüne koyun (ve varsa Türkçe `.srt` dosyasını).
2. **Panel → Seriler → Kaynak filmler**: dosya adı, film adı, lisans türü, kaynak sayfası, **kanıt bağlantısı**,
   altyazı dosyası; "kendim kontrol ettim" kutusunu işaretleyin → **Kaydet**. Sistem sahne geçişlerini bulup
   bölüm sayısını gösterir.
3. **Yeni seri**: seri adı, tür "Kaynak film kesitleri", filmi seçin. Sessiz film için: seslendirme **kapalı**,
   filmin sesi **kapalı**, müzik **seçili**. Çocuklara yönelikse **"Çocuklara özel"** kutusunu işaretleyin
   (YouTube'da yasal zorunluluk).
4. **Sonraki bölüm** düğmesi bir bölüm üretir; otomasyon açıksa `11 Series Episode Planner` her saat tamponu doldurur.
5. Bölümler QC'den geçince **Videolar** sekmesinden onaylayın; yayıncı onları sırayla yükler ve oynatma listesine ekler.

## Yol 3 — YouTube Remix (elle)

YouTube, uygun videolardan kısa kesit alıp Short yapmaya **yalnızca kendi uygulaması içinden** izin verir;
bu yüzden otomatikleştirilemez.

1. Telefonda YouTube uygulamasında videoyu açın → **Remix** (veya **Paylaş → Remix**) → **Kes** (Cut).
2. 1–5 saniyelik kesiti seçin, ardından kendi çekiminizi/yorumunuzu ekleyin (ör. "Bu sahnenin hikâyesi…").
3. Short otomatik olarak orijinal videoya bağlantı verir. Videonun sahibi Remix'i kapattıysa seçenek görünmez.
4. Seri devamlılığı için başlığa "Seri adı | Bölüm N" yazın ve kendi oynatma listenize ekleyin.

Remix'te asıl değer sizin eklediğiniz yorumdur; yalnızca kesiti yayınlamak "yeniden kullanılmış içerik"
sayılır ve para kazanmaya uygun olmaz.
