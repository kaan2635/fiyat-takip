# Fiyat Takip Sistemi

Türk pazaryerlerindeki ürünlerin fiyatını izler. Üç yerden kullanılır:

- **iPhone'da uygulama gibi** — ana ekrana eklenir, tam ekran açılır. İçinden ürün eklersin, **Tara**'ya basınca tarama başlar, bitince ekran kendini günceller.
- **Otomatik** — GitHub Actions belirlediğin aralıkla (varsayılan: günde 3 kez) kendi tarar.
- **Terminalden** — `python -m tracker scan`.

Ürünü **iki şekilde** takip edebilirsin:

- **Ürün adı yaz** — sistem pazaryerlerinde arar, en ucuz satıcıyı bulur ve onu izler. Satıcı değişirse otomatik olarak yeni en ucuza geçer.
- **Adres yapıştır** — belirli bir satıcının o sayfadaki fiyatı izlenir.

Fiyat düştüğünde Telegram'dan haber verir, her taramada grafikli bir rapor üretir ve tüm geçmişi `data/history.csv` içinde tutar.

> **Tarama neden telefonda çalışmıyor?** Trendyol/Hepsiburada/n11 bot koruması ve tarayıcıların
> CORS kuralı yüzünden bir web uygulaması bu siteleri doğrudan okuyamaz — telefondan atılan
> istek de aynı 403'ü yer. Bu yüzden tarama sunucu tarafında (GitHub Actions) çalışır;
> telefondaki uygulama onu tetikler ve sonucu gösterir.

---

## Hangi siteler çalışıyor

Aşağıdaki tablo **gerçek ürün sayfalarında ölçülmüş** sonuçlardır, tahmin değil:

| Site | Durum | Nasıl okuyor |
|---|---|---|
| Trendyol | ✅ çalışıyor | JSON-LD + sayfaya gömülü ürün state'i |
| Hepsiburada | ✅ çalışıyor | JSON-LD |
| n11 | ✅ çalışıyor | JSON-LD |
| Çiçeksepeti | ⚠️ test edilmedi | JSON-LD / CSS seçici |
| Amazon.com.tr | ⚠️ tarayıcı modu gerekir | Düz HTTP isteklerini captcha ile karşılıyor |
| Diğer siteler | 🔄 genel yöntem | JSON-LD → microdata → meta → CSS seçici |

Listede olmayan bir site de büyük ihtimalle çalışır: sistem önce sayfanın kendi
makine-okunur ürün verisini (schema.org JSON-LD) arar, bunu neredeyse her
e-ticaret sitesi yayınlar. Eklemeden önce `python -m tracker test <url>` ile dene.

### Bot koruması hakkında

Trendyol, Hepsiburada ve n11 düz `requests` isteklerini TLS parmak izinden tanıyıp
**403** döndürüyor. Bu yüzden sistem `curl_cffi` ile gerçek tarayıcı TLS parmak izini
taklit eder ve profilleri sırayla dener — ölçümde tek bir profil hepsini açmıyor:
Trendyol `chrome116` ile açılıyor ama `chrome131` ülke kapısına atıyor, Hepsiburada
`chrome120`'yi reddedip `chrome116`/`edge101`/`safari17_0`'ı kabul ediyor. Bir site
için çalışan profil hatırlanır, sonraki taramada tek istekte sonuç alınır.

Trendyol yurt dışı IP'lerde (GitHub Actions dahil) ürün sayfası yerine ülke seçim
ekranı gösteriyor; sistem Türkiye vitrini çerezlerini otomatik gönderir.

**En önemli garanti:** sayfa bot duvarı, captcha veya yönlendirme ise sistem bunu
tanır ve o ürünü `blocked` olarak işaretler — **yanlış bir fiyat kaydetmez.**

---

## Kurulum

### 1. Yerelde çalıştır (en hızlı yol)

```bash
pip install -r requirements.txt

python -m tracker add "https://www.trendyol.com/..." --target 45000
python -m tracker scan
```

`docs/index.html` dosyasını tarayıcıda aç — rapor orada.

### 2. GitHub'da otomatiğe bağla

1. Bu klasörü bir GitHub reposuna gönder:

   ```bash
   git init && git add . && git commit -m "Fiyat takip sistemi"
   git branch -M main
   git remote add origin https://github.com/<kullanici>/<repo>.git
   git push -u origin main
   ```

2. **Actions'ı aç:** repo → Settings → Actions → General → "Allow all actions",
   ve aşağıda **Workflow permissions → Read and write permissions** seç.
   (Tarama sonuçlarını repoya geri yazabilmesi için gerekli.)

3. **Telegram için secret ekle** (aşağıdaki bölüme bak):
   Settings → Secrets and variables → Actions → New repository secret
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

4. **Raporu yayınla (isteğe bağlı):** Settings → Pages → Source: `Deploy from a branch`,
   Branch: `main`, klasör: `/docs`. Rapor şu adreste yayınlanır:
   `https://<kullanici>.github.io/<repo>/`

5. **Manuel tarama:** Actions sekmesi → "Fiyat taraması" → **Run workflow**.
   İstersen sadece belirli ürünleri tarat ya da bildirimi kapat.

### Tarama sıklığını değiştirme

`.github/workflows/scan.yml` içindeki cron satırı (UTC saatiyle):

```yaml
- cron: "0 6,12,18 * * *"   # 09:00 / 15:00 / 21:00 TR
```

| İstediğin | Yaz |
|---|---|
| Saat başı | `0 * * * *` |
| 3 saatte bir | `0 */3 * * *` |
| Günde 1 kez (09:00 TR) | `0 6 * * *` |
| Sadece hafta içi sabah | `0 6 * * 1-5` |

> GitHub'ın zamanlanmış işleri yoğunlukta birkaç dakika gecikebilir; bu normaldir.

---

## Ürün adıyla takip

URL bulmakla uğraşmadan, ürünün adını yazman yeterli:

```bash
python -m tracker add "BeSafe iZi Turn B i-Size" --target 42000
```

Sistem her taramada Trendyol ve Hepsiburada'da arar, eşleşen teklifleri toplar ve **en ucuzunu** kaydeder. Raporda "3 teklif bulundu · en ucuz hepsiburada.com" satırını açınca bütün satıcıları fiyatlarıyla görürsün.

### Sorgunu önce dene

Kaydetmeden ne bulacağını görmek için:

```bash
python -m tracker search iPhone 15 128 GB --exclude yenilenmiş plus kılıf
```

### Yanlış eşleşmeyi önleme

Arama motorları alakasız sonuç döndürebiliyor — ölçümde n11, bir oto koltuğu sorgusuna hiç ürün bulamayıp "önerilen ürünler" listesinden **psikoloji kitabı** döndürdü. Bu yüzden her teklif, başlığı sorgunun bütün anlamlı kelimelerini içermedikçe elenir.

Eşleştirme kelime sınırına bakar, alt dizgeye değil: `MX Master 4` ararken `MX Master 3S` elenir, çünkü "4" başlıkta ayrı bir kelime olarak geçmiyor (ve "8000" içindeki 4'e takılmaz). Türkçe ekler de hesaba katılır — "koltuk" yazınca "koltuğu" eşleşir.

Yine de gerekirse daraltabilirsin:

| Ayar | Ne yapar |
|---|---|
| `exclude` | Başlığında bu kelimeler geçen sonuçları eler (`kılıf`, `aksesuar`, `yenilenmiş`) |
| `must_include` | Başlıkta mutlaka geçmesi gereken kelimeler (`anthracite`) |
| `min_price` / `max_price` | Fiyat aralığı dışındakileri eler |
| `sites` | Hangi kaynaklarda aransın (`trendyol`, `hepsiburada`, `n11`) |

```yaml
- id: besafe-oto-koltugu
  name: BeSafe Oto Koltuğu
  query: BeSafe iZi Turn B i-Size
  target_price: 42000
  exclude: [kılıf, aksesuar]
  min_price: 20000
```

Hiçbir teklif eşleşmezse ürün `no_match` olarak işaretlenir ve hangi kaynakta kaç sonuç bulunup kaçının elendiği not edilir — sorguyu ona bakarak daraltabilirsin. **Sessizce yanlış ürünü takip etmektense hiçbir şey takip etmemeyi tercih eder.**

### Arama kaynakları

| Kaynak | Durum |
|---|---|
| Hepsiburada | ✅ ölçüldü, çalışıyor |
| Trendyol | ✅ ölçüldü, çalışıyor (gömülü JSON'dan) |
| n11 | ⚠️ varsayılan kapalı — çoğu sorguda alakasız sonuç veriyor; `sites` ile açabilirsin |
| Akakçe, Cimri | ❌ Cloudflare engeli, hiçbir tarayıcı profiliyle geçilemedi |
| Google Shopping | ❌ fiyatları JavaScript ile yüklüyor |

## iPhone'da uygulama olarak kullanma

Rapor aynı zamanda bir **PWA**'dır: ana ekrana eklenince Safari çubuğu olmadan, kendi ikonuyla,
tam ekran açılır. App Store, Xcode, Mac ya da geliştirici hesabı gerekmez.

### 1. Raporu yayınla

Repo → Settings → Pages → Source: **Deploy from a branch**, Branch: `main`, klasör: **`/docs`**.
Adresin: `https://<kullanici>.github.io/<repo>/`

> Depo **private** ise GitHub Pages ücretli plan ister. Public yaparsan Pages ücretsizdir —
> erişim anahtarın sayfanın kodunda **değil**, yalnızca kendi telefonunun hafızasında durur,
> yani depo herkese açık olsa bile anahtarın açığa çıkmaz. Takip listen ve fiyat geçmişin
> görünür olur; bunu istemiyorsan depoyu private tutup Pages yerine raporu yerelde aç.

### 2. Erişim anahtarı oluştur

[github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens/new)
→ **Only select repositories** ile sadece bu depoyu seç → Repository permissions altında
tek yetki ver: **Actions: Read and write**.

Bu kadarı yeterli; uygulama depo içeriğine hiç dokunmuyor. Ürün ekleme/silme dahil her şeyi
iş akışını tetikleyerek yapıyor, dosyaları Actions'ın kendi izniyle Python kodu yazıyor.

### 3. Ana ekrana ekle

iPhone'da Safari ile Pages adresini aç → **Paylaş** → **Ana Ekrana Ekle**.
(Bu adım yalnızca Safari'de çalışır; Chrome'da "Ana Ekrana Ekle" seçeneği yoktur.)

### 4. Uygulamayı bağla

Aç → **⚙** → depo adını (`kullanici/repo`) ve anahtarı yapıştır → **Kaydet ve dene**.
Bağlantı doğrulanınca ürün ekleme ve silme düğmeleri görünür olur.

### Kullanım

| Düğme | Ne yapar |
|---|---|
| **⟳ Tara** | Taramayı hemen başlatır, ilerlemeyi gösterir, bitince yeni raporu yükler |
| **＋** | Ürün adresi (+ istersen isim ve hedef fiyat) alır, ekler ve tarar |
| **sil** | Ürünü takipten çıkarır — fiyat geçmişi silinmez |
| **⚙** | Depo/anahtar ayarları; "Bu cihazdaki bilgileri sil" ile çıkış |

Tarama tipik olarak 1-2 dakika sürer; ardından GitHub Pages'in yeni raporu yayınlaması
biraz daha alabilir. Uygulama bunu kendi takip eder ve yayın gerçekleşince ekranı yeniler —
beklerken kapatabilirsin, bildirim zaten Telegram'dan gelir.

Çevrimdışıyken uygulama son taramanın raporunu gösterir (servis çalışanı önbelleği).

## Telegram bildirimi (2 dakika)

1. Telegram'da **@BotFather**'a yaz → `/newbot` → bot adını ver → sana bir **token** verir.
2. Kendi botunla sohbet başlat ve ona bir mesaj yaz (bot önce mesaj alamaz).
3. Sohbet kimliğini öğren:

   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```

   Çıktıdaki `"chat":{"id":123456789}` değeri senin `TELEGRAM_CHAT_ID`'in.

4. Yerelde kullanmak için:

   ```bash
   export TELEGRAM_BOT_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="123456789"
   python -m tracker scan
   ```

   GitHub'da ise bunları repo secret'ı olarak ekle.

Bildirim gelen durumlar (`products.yaml` → `settings` ile ayarlanır):

- hedef fiyatın altına inince (yalnızca **ilk** inişte, her taramada değil)
- `drop_alert_percent` kadar ucuzlayınca (varsayılan %5)
- gördüğü en düşük fiyata inince
- stokta olmayan ürün tekrar stoğa girince

Her taramanın sonunda ayrıca kısa bir özet mesajı gelir (`--no-summary` ile kapatılır).

---

## Komutlar

```bash
python -m tracker add "Ürün Adı" [--target 45000] [--exclude kılıf aksesuar] [--sites trendyol hepsiburada]
python -m tracker add <url> [--name ...] [--target 45000] [--mode browser] [--selector ".fiyat"]
python -m tracker search Ürün Adı           # kaydetmeden ara, sorguyu dene
python -m tracker list                      # takip listesi + güncel fiyatlar
python -m tracker remove <kimlik|isim>
python -m tracker scan                      # tara, kaydet, rapor üret, bildir
python -m tracker scan --only iphone-15     # sadece bazı ürünler
python -m tracker scan --dry-run            # kaydetme/bildirme, sadece göster
python -m tracker report                    # geçmişten raporu yeniden üret
python -m tracker test <url>                # kaydetmeden dene (hata ayıklama)
python -m tracker test <url> --browser      # gerçek tarayıcıyla dene
```

### Bir ürün okunamıyorsa

```bash
python -m tracker test "https://site.com/urun"
```

Çıktı hangi aşamada takıldığını söyler:

- **`blocked`** → `--browser` ile dene. İşe yararsa `products.yaml` içinde o ürüne
  `mode: browser` ekle.
- **fiyat bulunamadı** → sayfada fiyatın CSS seçicisini bul (tarayıcıda sağ tık →
  İncele) ve `--selector ".urun-fiyat"` ile dene, sonra ürüne `selector:` olarak yaz.

### Tarayıcı modu

Zor siteler için gerçek Chromium kullanılır:

```bash
pip install playwright && playwright install chromium
```

GitHub Actions bunu, `products.yaml` içinde `mode: browser` gören bir ürün varsa
ya da "Run workflow" ekranında kutuyu işaretlediğinde otomatik kurar.

---

## Dosyalar

| Dosya | İçerik |
|---|---|
| `products.yaml` | Takip listesi ve ayarlar — **elle düzenleyebilirsin** |
| `data/history.csv` | Tüm ölçümlerin geçmişi (Excel'de açılır, git ile takip edilir) |
| `data/latest.json` | Son durumun makine-okunur özeti |
| `docs/index.html` | Grafikli rapor + iPhone uygulaması (GitHub Pages ile yayınlanır) |
| `docs/app.js`, `sw.js`, `manifest.webmanifest` | PWA parçaları — her raporla yeniden üretilir |
| `docs/icons/` | Uygulama ikonları — `python tools/make_icons.py` ile yeniden üretilir |
| `tracker/parsers/sites.py` | Siteye özel okuyucular — yeni site buraya eklenir |
| `tracker/search.py` | Ürün adıyla arama ve eşleştirme kuralları |

### Ayarlar (`products.yaml` → `settings`)

```yaml
settings:
  drop_alert_percent: 5.0        # bu orandan fazla düşerse bildir
  notify_on_target: true         # hedef fiyatın altına inince bildir
  notify_on_back_in_stock: true  # tekrar stoğa girince bildir
  browser_fallback: true         # HTTP engellenirse tarayıcıya yükselt
  min_delay_seconds: 2.0         # aynı siteye istekler arası bekleme
  timeout_seconds: 30
```

### Ürün alanları

```yaml
products:
  # Ürün adıyla takip (en ucuz satıcı otomatik bulunur)
  - id: besafe-oto-koltugu
    name: BeSafe Oto Koltuğu
    query: BeSafe iZi Turn B i-Size
    target_price: 42000
    exclude: [kılıf, aksesuar]     # isteğe bağlı eşleştirme kuralları
    min_price: 20000
    sites: [trendyol, hepsiburada]

  # Belirli bir satıcı sayfasını takip
  - id: iphone-15-128-gb-siyah   # otomatik üretilir
    name: iPhone 15 128 GB Siyah
    url: https://www.trendyol.com/...
    target_price: 50000          # isteğe bağlı
    mode: browser                # isteğe bağlı: http (varsayılan) | browser
    selector: ".fiyat"           # isteğe bağlı: özel CSS seçici
    enabled: false               # geçici olarak takibi durdur
```

---

## Yeni site ekleme

Çoğu site için bir şey yapmana gerek yok. Gerekirse `tracker/parsers/sites.py`
içine bir fonksiyon yaz ve `SITE_PARSERS` sözlüğüne alan adını ekle:

```python
def yeni_site(html, soup, url):
    return _pick(soup, [".urun-fiyat", "#fiyat"], "yeni_site")

SITE_PARSERS = {
    ...
    "yenisite.com": yeni_site,
}
```

Özel yöntem tutmazsa sistem kendiliğinden genel zincire (JSON-LD → microdata →
meta → CSS) düşer, yani hiçbir zaman tamamen kör kalmaz.

---

## Testler

```bash
pip install pytest
python -m pytest tests/ -q
```

Testler ağ erişimi gerektirmez; fiyat çözümleme, engel tespiti, istatistik,
uyarı mantığı ve yapılandırmayı kapsar.

---

## Sınırlar ve dürüst notlar

- **Amazon.com.tr** veri merkezi IP'lerinden düz HTTP isteklerini captcha ile
  karşılıyor. Tarayıcı modu genelde geçer ama garanti değil.
- Siteler HTML'ini değiştirebilir. JSON-LD genelde ayakta kalır, o yüzden sistem
  önce onu okur; yine de zamanla bir ürün `parse_failed` olabilir — rapor ve
  Telegram özeti bunu açıkça gösterir.
- Sistem yanlış fiyat kaydetmemek için temkinlidir: şüpheli bir değer (geçmiş
  medyanın 1/5'inden küçük veya 5 katından büyük) `suspicious` olarak işaretlenir
  ve bildirime yol açmaz.
- Siteler nazik davranılmasını bekler: aynı siteye istekler arasında varsayılan
  2 saniye beklenir. Çok sık tarama (örn. dakikada bir) hem gereksizdir hem de
  engellenme riskini artırır.
- Fiyatlar bilgi amaçlıdır; alışverişten önce ürün sayfasından doğrula.
