# Fiyat Takip

Türk pazaryerlerinde ürün arar, en ucuz teklifi bulur ve fiyatı takip eder. Fiyat düşünce,
hedefin altına inince ya da görülen en düşük fiyata gelince Telegram'dan haber verir.

Her şey telefondan yapılır: ana ekrana eklenen uygulamada ürünün adını yazarsın, birkaç
yarım dakika içinde 7 mağazadaki teklifler listelenir, doğru olanları seçip **Takip et**'e basarsın.
Tarama GitHub Actions'ta çalışır; sunucu, uygulama mağazası ya da ücret gerekmez.

**Uygulama:** <https://kaan2635.github.io/fiyat-takip/>

---

## Telefonda kurulum (bir kez, 3 dakika)

1. **Uygulamayı aç ve ana ekrana ekle.** iPhone'da Safari ile adresi aç → **Paylaş** →
   **Ana Ekrana Ekle**. Uygulama kendi ikonuyla, tam ekran açılır.
2. **Erişim anahtarı oluştur.** Uygulamada **Ayarlar** → **Anahtar oluşturma sayfasını aç**.
   Sayfa izinler hazır olarak açılır (Actions: yazma, Contents: okuma). Yalnızca
   **Repository access → Only select repositories → fiyat-takip** seçip **Generate token**'a bas.
3. **Anahtarı yapıştır** → **Kaydet ve bağlantıyı dene**. Depo adı kendiliğinden doludur.

Anahtar yalnızca telefonda saklanır, sayfanın kodunda yer almaz. Yetkisi iş akışlarını
başlatmakla sınırlıdır; depodaki dosyaları değiştiremez.

## Kullanım

| Sekme | Ne yapar |
|---|---|
| **Takip** | Takip edilen ürünler, güncel en ucuz fiyat, değişim, küçük grafik. **Fırsatlar** süzgeci hedefe inenleri, en düşük fiyattakileri ve ucuzlayanları gösterir. **Tara** hepsini hemen tarar. |
| **Ara** | Ürün adını yaz → tüm mağazalarda aranır (genelde 20-40 sn). Sonuçlardan yanlış olanların işaretini kaldır, elenen ama doğru olan varsa işaretle, **Takip et**. Ürün **adresi** yapıştırırsan o satıcının sayfası izlenir. |
| **Ayarlar** | Bağlantı, tarama zamanı, mağazalar. |

Bir ürüne dokununca ayrıntı ekranı açılır:

- **Fiyat konumu:** şu anki fiyatın son 90 gündeki en düşük ve en yüksek fiyat arasındaki yeri.
  "İyi fiyat", "Ortalama civarında", "Pahalı dönem: beklemek mantıklı olabilir" gibi yorumlar
  gerçekten dalgalanmış bir geçmişe dayanır; fiyat hiç oynamadıysa yorum yapılmaz.
- **Fiyat geçmişi:** 7 gün / 30 gün / 90 gün / tümü. Grafiğe dokununca o anki fiyat görünür.
- **Hedef fiyat:** altına inince bildirim gelir.
- **Teklifler:** bulunan tüm satıcılar. Yanlış bir ürün varsa **Bu ürün değil** de; o ilan bir
  daha hesaba katılmaz. Elenen yakın sonuçlarda doğru ürün varsa **Bu da doğru ürün** de.
- **Eşleştirme ayarları:** aranan ad, hariç kelimeler, mağazalar, fiyat aralığı.

Değişiklikler (hedef, "bu ürün değil", ekleme, silme) GitHub Actions'ta uygulanır. Uygulama
işlemi takip eder ve bitince ekranı kendisi yeniler; beklerken uygulamayı kapatabilirsin,
açınca kaldığı yerden sürer. Arka arkaya yapılan değişiklikler sıraya girer, birbirini bozmaz.

## Hangi mağazalarda aranıyor

Aşağıdakiler GitHub Actions'ın kendi sunucusundan, gerçek arama sayfalarında ölçüldü (Ekim 2026):

| Mağaza | Durum | Nasıl okunuyor |
|---|---|---|
| Trendyol | ✅ | Sayfaya gömülü ürün listesi (JSON) |
| Hepsiburada | ✅ | Ürün kartları |
| Amazon.com.tr | ✅ | Arama sonuç kartları |
| n11 | ✅ | schema.org ürün listesi (yalnızca aranan ürünler, "önerilenler" değil) |
| Pazarama | ✅ | Ürün kartları, sepet fiyatı |
| Teknosa | ✅ | schema.org ürün listesi |
| MediaMarkt | ✅ | schema.org ürün listesi |
| e-bebek | ❌ | CloudFront 403, hiçbir tarayıcı profiliyle açılmadı |
| Vatan, Akakçe, Cimri | ❌ | Cloudflare engeli |
| idefix | ❌ | 511 |

Bot korumasını aşmak için `curl_cffi` gerçek tarayıcı TLS parmak izini taklit eder. Her site
için ilk denemede kabul edilen profil ölçüldü ve sistemde kayıtlı (`tracker/fetch.py →
SITE_PROFILES`); siteler paralel taranır. Sayfa bot duvarı ya da captcha ise sistem bunu tanır
ve **yanlış fiyat kaydetmez**.

## Doğru ürünü bulma (eşleştirme)

Arama motorları alakasız sonuç döndürür: "iPhone 15" aramasında kılıflar, "iPhone 15 Pro"
ve flash bellekler de gelir. Her teklif şu kontrollerden geçer:

1. **Ad tutmalı:** sorgunun bütün anlamlı kelimeleri başlıkta geçmeli. Kelime sınırına bakılır
   ("MX Master 4" ararken "3S" elenir), Türkçe ekler hesaba katılır ("koltuk" → "koltuğu"),
   birimler ayrıştırılır ("128 GB" = "128GB").
2. **Aksesuar ve ikinci el elenir:** kılıf, koruyucu, aksesuar, yedek, yenilenmiş, ikinci el,
   teşhir, A/B kalite ve "… uyumlu iPhone 15" kalıbı. Bu kelimeyi sorguna yazarsan kural devreden çıkar.
3. **Model ayrımı:** sorguda "Pro" yokken başlıkta varsa farklı modeldir. Sorguyla birebir eşleşen
   teklif varsa onlar tercih edilir. Bütün sonuçlar aynı farkı taşıyorsa (ör. eski model artık
   satılmıyor) kabul edilir ama uygulamada uyarı çıkar.
4. **Şüpheli ucuzluk:** diğer tekliflerin ortanca fiyatının yarısından ucuz olan teklif
   aksesuar ya da yanlış ürün olabilir; elenir ama "yakın sonuç" olarak gösterilir.
5. **Senin kararın:** "Bu ürün değil" ve "Bu da doğru ürün" bütün kurallardan önce gelir.

Elenen her teklif sebebiyle saklanır; uygulamada "Elenen yakın sonuçlar" altında görünür.
Hiçbir şey eşleşmezse ürün `no_match` olur. **Sessizce yanlış ürünü takip etmektense hiçbir
şey takip etmemeyi tercih eder.**

## Telegram bildirimi

1. Telegram'da **@BotFather** → `/newbot` → bir **token** alırsın.
2. Kendi botuna bir mesaj yaz, sonra `https://api.telegram.org/bot<TOKEN>/getUpdates`
   adresinden `"chat":{"id":…}` değerini al.
3. Depo → Settings → Secrets and variables → Actions → `TELEGRAM_BOT_TOKEN` ve `TELEGRAM_CHAT_ID`.

Bildirim gelen durumlar: hedefin altına ilk iniş, `drop_alert_percent` kadar düşüş (varsayılan
%5), görülen en düşük fiyat, tekrar stoğa giriş. Zamanlı taramalar ayrıca kısa bir özet atar;
uygulamadan yapılan değişikliklerde yalnızca uyarılar gelir.

## Otomatik tarama sıklığı

`.github/workflows/scan.yml` içindeki cron satırı (UTC):

```yaml
- cron: "0 6,12,18 * * *"   # 09:00 / 15:00 / 21:00 TR
```

| İstediğin | Yaz |
|---|---|
| 3 saatte bir | `0 */3 * * *` |
| Günde 1 kez (09:00 TR) | `0 6 * * *` |
| Sadece hafta içi sabah | `0 6 * * 1-5` |

Uygulamanın Ayarlar ekranı bu satırı okuyup Türkiye saatiyle gösterir. GitHub zamanlı işleri
yoğunlukta gecikebilir.

---

## Nasıl çalışıyor

```
Telefon (PWA, GitHub Pages)
  │  Ara ───────► ara.yml   → mağazalarda arar → sonucu ft-veri dalına yazar ─┐
  │  Ekle/değiştir/tara ─► scan.yml → products.yaml'ı günceller → tarar → main'e yazar
  │                                                                          │
  └─◄── sonuçları GitHub API'den okur (Pages yayınını beklemeden) ◄──────────┘
```

- Uygulama depoya doğrudan yazmaz; iş akışlarını girdilerle tetikler, değişikliği Python kodu yapar.
- Her iş akışı başlığına istek kimliğini yazar (`run-name`); uygulama kendi koşusunu bununla bulur.
- `scan.yml` aynı anda tek koşu çalıştırır (geçmiş dosyası çakışmasın diye). Uygulama da kendi
  isteklerini sırayla gönderir; arada başlayan zamanlı tarama bekleyen isteği iptal ettirirse bir kez yeniden dener.
- `docs/data.json` uygulamanın tek veri kaynağıdır; aynı paket `index.html`'e de gömülüdür,
  böylece uygulama çevrimdışı ve ilk açılışta bekletmeden çalışır.

## Komut satırı

```bash
pip install -r requirements.txt

python -m tracker search maxi cosi mica              # kaydetmeden ara, sonuçları ve elenenleri gör
python -m tracker add "BeSafe iZi Turn B i-Size" --target 42000
python -m tracker add "https://www.trendyol.com/..." # belirli satıcı sayfası
python -m tracker list
python -m tracker scan                               # tara, kaydet, uygulamayı üret, bildir
python -m tracker scan --only maxi-cosi-mica --dry-run
python -m tracker remove <kimlik|isim>
python -m tracker report                             # docs/ altını geçmişten yeniden üret
python -m tracker test <url> [--browser]             # bir ürün sayfası neden okunmuyor?
python -m tracker apply '{"ops":[{"op":"update","id":"maxi-cosi-mica","set":{"target":24000}}]}'
```

`apply` uygulamanın kullandığı komut biçimidir: `add`, `update` (`set`, `reject`, `unreject`,
`include`, `uninclude`), `remove`, `scan`. Hatalı bir işlem diğerlerini durdurmaz.

### Ürün alanları (`products.yaml`)

```yaml
products:
  - id: maxi-cosi-mica
    name: Maxi Cosi Mica
    query: Maxi Cosi Mica 360 Pro      # adla arama (en ucuz satıcı otomatik)
    target_price: 24000
    sites: [trendyol, hepsiburada, amazon]   # boş = varsayılan 7 mağaza
    exclude: [kılıf, minder]
    must_include: [graphite]
    min_price: 15000
    exclude_listings: [n11:60303290]   # "Bu ürün değil" dediklerin
    include_listings: [amazon:B0F28Z3CQG]  # "Bu da doğru ürün" dediklerin

  - id: iphone-15-trendyol             # belirli satıcı sayfası
    url: https://www.trendyol.com/...
    mode: browser                      # isteğe bağlı: zor siteler için gerçek tarayıcı
    selector: ".fiyat"                 # isteğe bağlı: özel CSS seçici
    enabled: false                     # geçici olarak durdur
```

Ayarlar (`settings`): `drop_alert_percent`, `notify_on_target`, `notify_on_back_in_stock`,
`browser_fallback`, `min_delay_seconds`, `timeout_seconds`, `default_sites`.

## Dosyalar

| Dosya | İçerik |
|---|---|
| `products.yaml` | Takip listesi ve ayarlar (elle de düzenlenebilir) |
| `data/history.csv` | Bütün ölçümler (Excel'de açılır) |
| `data/latest.json` | Son durum: teklifler, elenenler, uyarılar |
| `docs/` | Telefondaki uygulama + `data.json` (her taramada yeniden üretilir, GitHub Pages yayınlar) |
| `tracker/web/` | Uygulamanın kaynak dosyaları (HTML, CSS, JS) — tasarım buradan değişir |
| `tracker/search.py` | Mağaza ayrıştırıcıları ve eşleştirme kuralları |
| `tracker/commands.py` | Uygulamadan gelen komutlar |
| `tracker/webdata.py` | Uygulamanın veri paketi (fiyat konumu, grafik noktaları) |
| `ft-veri` dalı | Arama sonuçları (son 40 arama; elle dokunmaya gerek yok) |

## Testler

```bash
pip install pytest
python -m pytest tests/ -q
```

Testler ağ erişimi gerektirmez: fiyat çözümleme, engel tespiti, mağaza ayrıştırıcıları,
eşleştirme kuralları, uygulama komutları, veri paketi ve uygulama üretimi.

## Sınırlar

- Siteler HTML'ini değiştirebilir. Ayrıştırıcılar önce makine-okunur veriyi (JSON, schema.org)
  okur, o yüzden genelde ayakta kalır; kırılırsa uygulamada o mağaza "açılmadı" ya da "0 sonuç" görünür.
- Şüpheli bir ölçüm (geçmiş ortancanın 1/5'inden küçük, 5 katından büyük) `suspicious` olarak
  işaretlenir ve bildirime yol açmaz.
- Aynı siteye istekler arasında en az 2 saniye beklenir. Dakikada bir gibi sık tarama hem
  gereksizdir hem de engellenme riskini artırır.
- Fiyatlar bilgi amaçlıdır; satın almadan önce mağaza sayfasından doğrula.
