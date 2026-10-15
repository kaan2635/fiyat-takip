"""Urun adiyla fiyat arama.

URL vermek yerine "BeSafe iZi Turn B i-Size" gibi bir sorgu yazilir; sistem
pazaryerlerinin arama sayfalarini gezip teklifleri toplar ve en ucuzunu takip
eder.

Isin zor kismi ayristirma degil, **eslestirme**. Arama motorlari alakasiz
sonuc dondurebiliyor: olcumde n11, bir oto koltugu sorgusu icin hic urun
bulamayip yerine "onerilen urunler" listesinden bir psikoloji kitabi dondurdu.
Bu yuzden her teklif birkac kontrolden gecer:

1. Kullanicinin elle reddettigi ilanlar (exclude_urls) elenir, onayladiklari
   (include_urls) dogrudan kabul edilir.
2. Baslik sorgunun tum anlamli kelimelerini icermeli.
3. Aksesuar / yenilenmis gibi kelimeler sorguda yoksa elenir ("kilif").
4. Model ayrimi: sorguda "Pro" yokken baslikta varsa bu farkli bir modeldir.
   Sorguyla birebir eslesen teklif varsa onlar tercih edilir.
5. Diger tekliflerin yaninda cok ucuz kalan teklif aksesuar ya da yanlis urun
   olabilir; elenir ama "yakin sonuc" olarak gosterilir.

Elenen her teklif sebebiyle birlikte dondurulur ki uygulama "bu dogru urun"
dugmesiyle kullaniciya karari geri verebilsin.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from .money import parse_machine_price, parse_price
from .parsers.base import json_after, walk

log = logging.getLogger("tracker.search")

# Turkce harfleri sadelestirip karsilastirmayi bagisikliyoruz:
# "İ-Size" / "i-size" / "I-Size" hepsi ayni forma iner.
_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i",
    "ğ": "g", "Ğ": "g", "ş": "s", "Ş": "s",
    "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "ç": "c", "Ç": "c",
    "â": "a", "Â": "a", "î": "i", "û": "u",
})


# Rakama bitisik birimler ayrilir: "128GB" -> "128 gb", "0-18kg" -> "0 18 kg".
# Boylece "128 GB" aramasi "128GB" yazan saticiyi da bulur. Tek harfli
# birimler (g, l, m) bilerek yok: "5G" gibi model adlarini bozuyorlardi.
_UNIT_SPLIT = re.compile(r"(?<=\d)(?=(?:gb|tb|mb|kg|gr|ml|lt|cm|mm|w|kw|mah|hz|inc|inch|adet|watt|mp)\b)")


def normalize(text: str) -> str:
    """Karsilastirma icin metni sadelestirir: harf/rakam disi her sey bosluk olur."""
    folded = (text or "").translate(_FOLD).lower()
    folded = re.sub(r"[^a-z0-9]+", " ", folded)
    return _UNIT_SPLIT.sub(" ", folded).strip()


def query_tokens(query: str) -> list[str]:
    """Sorgudan anlamli kelimeleri cikarir.

    Tek harfli parcalar ("B", "i") gurultu oldugu icin atilir; bunlar
    baslikta farkli yazilabiliyor ve eslesmeyi bosuna bozuyor. Ama tek
    haneli **rakamlar** kalir: "MX Master 4" ile "MX Master 3S" arasindaki
    tek fark odur, atarsak yanlis urunu takip etmeye baslariz.
    """
    return [t for t in normalize(query).split() if len(t) >= 2 or t.isdigit()]


# Turkce unsuz yumusamasi: kelime ek alinca son sessiz degisiyor
# (koltuk -> koltugu, kitap -> kitabi, kanat -> kanadi).
_SOFTEN = {"k": "g", "p": "b", "t": "d"}


def _prefixes(token: str) -> set[str]:
    """Kelimenin baslikta gecebilecek govde bicimleri."""
    forms = {token}
    if len(token) >= 4 and token[-1] in _SOFTEN:
        forms.add(token[:-1] + _SOFTEN[token[-1]])
    return forms


def token_hit(token: str, title_tokens) -> bool:
    """Sorgu kelimesi baslikta geciyor mu?

    Kelime sinirina bakariz, alt dizgeye degil: "4" aranirken "8000" icindeki
    4'e takilmamak gerekiyor. Uzun kelimelerde on-ek eslesmesine izin veririz
    ki Turkce ekleri ("koltuk" -> "koltugu") eslesmeyi bozmasin.
    """
    if token in title_tokens:
        return True
    if len(token) <= 2 or token.isdigit():
        return False                      # kisa/sayisal kodlarda tam eslesme sart
    forms = _prefixes(token)
    return any(t.startswith(f) for t in title_tokens for f in forms)


# ------------------------------------------------------------------ ilan kimligi
_KEY_PATTERNS = [
    ("trendyol.com", re.compile(r"-p-(\d+)")),
    ("hepsiburada.com", re.compile(r"-pm?-([A-Za-z0-9]{6,})")),
    ("n11.com", re.compile(r"-(\d{6,})(?:[/?#]|$)")),
    ("amazon.com.tr", re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")),
    ("pazarama.com", re.compile(r"-p-([A-Za-z0-9]+)$")),
    ("teknosa.com", re.compile(r"-p-(\d+)")),
    ("mediamarkt.com.tr", re.compile(r"-(\d{5,})\.html")),
]


def _host(url: str) -> str:
    host = (urlparse(url or "").netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def listing_key(url: str) -> str:
    """Bir ilani site icinde tekil olarak tanimlayan kisa anahtar.

    Ayni urun farkli saticidan farkli sorgu parametresiyle gelebilir
    (?merchantId=...). Kullanici bir ilani "bu degil" diye isaretlediginde
    bu anahtari sakliyoruz; parametre degisse de ilan yine taninir.
    """
    if not url:
        return ""
    host = _host(url)
    for domain, pattern in _KEY_PATTERNS:
        if host == domain or host.endswith("." + domain):
            m = pattern.search(urlparse(url).path)
            if m:
                return f"{domain.split('.')[0]}:{m.group(1)}"
    parsed = urlparse(url)
    return f"{host}{parsed.path.rstrip('/')}"


@dataclass
class Offer:
    """Bir saticidaki tek bir fiyat teklifi."""
    site: str
    title: str
    price: Decimal
    url: str
    currency: str = "TRY"
    image: str = ""
    seller: str = ""
    key: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = listing_key(self.url)

    def __str__(self) -> str:
        return f"{self.site}: {self.price} — {self.title[:60]}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["price"] = float(self.price)
        return {k: v for k, v in d.items() if v not in ("", None)}


@dataclass
class Rejected:
    """Elenen bir teklif ve sebebi."""
    offer: Offer
    code: str          # tokens | exclude | default_exclude | must_include | price | variant | outlier | user
    reason: str
    near: bool = False  # kullaniciya "yakin sonuc" olarak gosterilmeye deger mi

    def to_dict(self) -> dict:
        return {**self.offer.to_dict(), "code": self.code, "reason": self.reason, "near": self.near}


# ----------------------------------------------------------------- ayristirma
_TY_CDN = "https://cdn.dsmcdn.com"


def parse_trendyol(html: str, soup: BeautifulSoup) -> list[Offer]:
    """Trendyol arama sonuclarini sayfaya gomulu JSON'dan okur."""
    data = json_after(html, '"products":')
    if not isinstance(data, list):
        return []

    offers = []
    for p in data:
        if not isinstance(p, dict):
            continue
        price_block = p.get("price") or {}
        raw = None
        for key in ("discountedPrice", "current", "sellingPrice", "originalPrice"):
            val = price_block.get(key)
            if isinstance(val, dict):
                val = val.get("value")
            if val not in (None, "", 0):
                raw = val
                break
        price = parse_machine_price(raw)
        if price is None:
            continue

        # Trendyol'un `name` alaninda marka yok, ayri alanda duruyor.
        # Marka eklemezsek "besafe" kelimesi baslikta bulunamiyor ve
        # dogru urun yanlislikla eleniyor.
        brand = p.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        title = " ".join(x for x in (brand, p.get("name")) if x)

        image = ""
        images = p.get("images") or []
        if images and isinstance(images[0], str):
            image = images[0] if images[0].startswith("http") else _TY_CDN + images[0]
        elif isinstance(p.get("image"), str):
            image = p["image"] if p["image"].startswith("http") else _TY_CDN + p["image"]

        merchant = p.get("merchantName") or ""
        if not merchant and isinstance(p.get("merchant"), dict):
            merchant = p["merchant"].get("name") or ""

        url = p.get("url") or ""
        offers.append(Offer(
            site="trendyol.com", title=title, price=price,
            url=urljoin("https://www.trendyol.com", url),
            currency=(price_block.get("currency") or "TRY").replace("TL", "TRY"),
            image=image, seller=str(merchant or ""),
        ))
    return offers


def parse_hepsiburada(html: str, soup: BeautifulSoup) -> list[Offer]:
    """Hepsiburada sonuclari sunucuda uretiliyor; kartlari DOM'dan okuruz.

    Sinif adlari derlenmis (CSS modules) oldugu icin tam ada degil
    on-ekine bakiyoruz; boylece site yeni bir derleme yayinlayinca kirilmiyor.
    """
    offers = []
    for card in soup.select('li[class*="productListContent-"]'):
        title_node = card.select_one('[data-test-id^="title-"]') or card.select_one("h3")
        price_node = (card.select_one('[class*="price-module_finalPrice"]')
                      or card.select_one('[class*="price-module_priceInfo"]'))
        link = card.find("a", href=True)
        if not (title_node and price_node and link):
            continue

        # Ayirici koymadan okuyoruz: fiyat "43.424" ve ",25" diye iki
        # parcaya bolunmus durumda, araya bosluk girerse kurus kayboluyor.
        price = parse_price(price_node.get_text("", strip=True))
        if price is None:
            continue

        img = card.find("img")
        image = ""
        if img is not None:
            image = img.get("src") or img.get("data-src") or ""
            if image.startswith("data:"):
                image = img.get("data-src") or ""

        offers.append(Offer(
            site="hepsiburada.com",
            title=title_node.get_text(" ", strip=True),
            price=price,
            url=urljoin("https://www.hepsiburada.com", link["href"]),
            image=image,
        ))
    return offers


# n11 icin urun nesnesinde aranacak alan adlari
_N11_TITLE_KEYS = ("title", "productName", "name", "displayName", "seoName")
_N11_URL_KEYS = ("seoUrl", "url", "productUrl", "link")


def parse_n11(html: str, soup: BeautifulSoup) -> list[Offer]:
    """n11 sonuclari gomulu JSON'da; dizi adi sorguya gore degisiyor.

    Bazen `suggestedProductListingItems`, bazen reklam listesi geliyor. Anahtar
    adina bagli kalmak yerine urun gorunumlu (fiyat + baslik tasiyan) tum
    nesneleri topluyoruz; alakasizlari eslestirme adimi eliyor.
    """
    offers, seen = [], set()

    for key in dict.fromkeys(re.findall(r'"(\w*[Pp]roduct\w*|\w*[Ii]tems)"\s*:\s*\[', html)):
        if "suggest" in key.lower() or "recommend" in key.lower():
            continue                       # onerilen urunler aranan urun degildir
        data = json_after(html, f'"{key}":')
        if not isinstance(data, list):
            continue
        for node in walk(data):
            raw = node.get("displayPrice") or node.get("price")
            price = parse_machine_price(raw)
            if price is None:
                continue
            title = next((str(node[k]) for k in _N11_TITLE_KEYS if node.get(k)), None)
            if not title:
                continue
            url = next((str(node[k]) for k in _N11_URL_KEYS if node.get(k)), "")
            full = urljoin("https://www.n11.com", url) if url else ""
            if (title, str(price)) in seen:
                continue
            seen.add((title, str(price)))
            offers.append(Offer(site="n11.com", title=title, price=price, url=full))
    return offers


def parse_jsonld_list(site: str, base: str):
    """schema.org ItemList / Product listesi yayinlayan arama sayfalari icin."""

    def _parse(html: str, soup: BeautifulSoup) -> list[Offer]:
        offers = []
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or tag.get_text() or "")
            except (json.JSONDecodeError, TypeError):
                continue
            for node in _walk_in_order(data):
                if node.get("@type") not in ("Product", ["Product"]):
                    continue
                offer = node.get("offers") or {}
                if isinstance(offer, list):
                    offer = offer[0] if offer else {}
                price = parse_machine_price(offer.get("price") or offer.get("lowPrice"))
                name = node.get("name")
                url = node.get("url") or offer.get("url") or ""
                if price is None or not name or not url:
                    continue
                image = node.get("image") or ""
                if isinstance(image, list):
                    image = image[0] if image else ""
                if isinstance(image, dict):
                    image = image.get("url", "")
                brand = node.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                if brand and normalize(str(brand)) in _NO_BRAND:
                    brand = None
                title = name if not brand or normalize(str(brand)) in normalize(name) \
                    else f"{brand} {name}"
                offers.append(Offer(site=site, title=title, price=price,
                                    url=urljoin(base, url), image=str(image or "")))
        return offers

    return _parse


def _walk_in_order(obj):
    """walk() gibi ama belgedeki sirayi korur (arama sonuc sirasi anlamli)."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk_in_order(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_in_order(v)


# Markasiz urunlerde marka alanina yazilan yer tutucular
_NO_BRAND = {"diger", "other", "genel", "markasiz", "marka yok"}

_parse_n11_jsonld = parse_jsonld_list("n11.com", "https://www.n11.com/")


def parse_n11_search(html: str, soup: BeautifulSoup) -> list[Offer]:
    """n11 arama sayfasi artik sonuclari ItemList JSON-LD olarak yayinliyor.

    Yalnizca o liste "aranan urunler"dir; eski yontem (gomulu JSON) onerilen
    urunleri de topluyordu. JSON-LD bossa eski yonteme duseriz.
    """
    return _parse_n11_jsonld(html, soup) or parse_n11(html, soup)


def parse_amazon(html: str, soup: BeautifulSoup) -> list[Offer]:
    """Amazon.com.tr arama sonuc kartlari (s-search-result)."""
    offers = []
    for item in soup.select('div[data-component-type="s-search-result"]'):
        asin = item.get("data-asin") or ""
        price_node = item.select_one(".a-price:not(.a-text-price) .a-offscreen")
        if not asin or price_node is None:
            continue                       # fiyati olmayan (stokta yok / secenekli) kartlar
        price = parse_price(price_node.get_text("", strip=True))
        if price is None:
            continue
        recipe = item.select_one('[data-cy="title-recipe"]') or item.select_one("h2")
        title = recipe.get_text(" ", strip=True) if recipe else ""
        title = re.sub(r"^Sponsorlu\s*(Reklam)?\s*", "", title).strip()
        if not title:
            continue
        img = item.select_one("img.s-image")
        offers.append(Offer(
            site="amazon.com.tr", title=title, price=price,
            url=f"https://www.amazon.com.tr/dp/{asin}",
            image=(img.get("src") if img else "") or "",
        ))
    return offers


_SEPETTE = re.compile(r"Sepette\s*([\d.]+(?:,\d{1,2})?)\s*TL", re.I)
_TL_PRICE = re.compile(r"([\d.]+(?:,\d{1,2})?)\s*TL")


def parse_pazarama(html: str, soup: BeautifulSoup) -> list[Offer]:
    """Pazarama kartlari: sunucuda uretilen HTML, test kimlikleriyle isaretli.

    Kartta birden fazla fiyat olabiliyor ("25.499 TL Sepette 24.899 TL Plus
    ile 24.849 TL"). Herkesin odedigi sepet fiyatini aliriz; "Plus ile"
    uyelik fiyatidir, "Son 10 gunun en dusuk fiyati" ise rozet.
    """
    offers = []
    for card in soup.select('[data-testid="listing-product-card-grid"], [data-testid="listing-product-card"]'):
        title_node = card.select_one('[data-testid="product-card-title"]')
        price_box = card.select_one('[data-testid="base-product-card-price-container"]')
        link = card.select_one('a[href*="-p-"]') or card.find("a", href=True)
        if not (title_node and price_box and link):
            continue
        text = price_box.get_text(" ", strip=True)
        text = re.sub(r"Plus ile.*$", "", text, flags=re.I)
        text = re.sub(r"Son \d+ G[üu]n[üu]n En D[üu][şs][üu]k Fiyat[ıi]\s*[\d.,]+\s*TL", "", text, flags=re.I)
        m = _SEPETTE.search(text) or _TL_PRICE.search(text)
        price = parse_price(m.group(1)) if m else None
        if price is None:
            continue
        img = card.select_one("img[src^='http']") or card.select_one("img[data-src]")
        image = (img.get("src") if img and str(img.get("src", "")).startswith("http")
                 else (img.get("data-src") if img else "")) or ""
        offers.append(Offer(
            site="pazarama.com",
            title=link.get("title") or title_node.get_text(" ", strip=True),
            price=price, url=urljoin("https://www.pazarama.com", link["href"]),
            image=image,
        ))
    return offers


# Kaynak tablosu: ad -> (arama adresi, ayristirici)
# Her biri GitHub Actions IP'sinden gercek arama sayfasinda olculdu (2026-10).
# Olculup ACILMAYANLAR: e-bebek (CloudFront 403), Vatan, Akakce, Cimri
# (Cloudflare), idefix (511), Ciceksepeti (arama ana sayfaya yonleniyor).
SOURCES: dict[str, tuple[str, callable]] = {
    "trendyol": ("https://www.trendyol.com/sr?q={q}", parse_trendyol),
    "hepsiburada": ("https://www.hepsiburada.com/ara?q={q}", parse_hepsiburada),
    "amazon": ("https://www.amazon.com.tr/s?k={q}", parse_amazon),
    "n11": ("https://www.n11.com/arama?q={q}", parse_n11_search),
    "pazarama": ("https://www.pazarama.com/arama?q={q}", parse_pazarama),
    "teknosa": ("https://www.teknosa.com/arama/?s={q}",
                parse_jsonld_list("teknosa.com", "https://www.teknosa.com/")),
    "mediamarkt": ("https://www.mediamarkt.com.tr/tr/search.html?query={q}",
                   parse_jsonld_list("mediamarkt.com.tr", "https://www.mediamarkt.com.tr/")),
}

SOURCE_LABELS = {
    "trendyol": "Trendyol",
    "hepsiburada": "Hepsiburada",
    "amazon": "Amazon",
    "n11": "n11",
    "pazarama": "Pazarama",
    "teknosa": "Teknosa",
    "mediamarkt": "MediaMarkt",
}

# Genel pazaryerleri her aramada; elektronik magazalari da ucuz ve hizli
# oldugu icin varsayilana dahil (alakasiz sonuclari eslestirme eliyor).
DEFAULT_SITES = ["trendyol", "hepsiburada", "amazon", "n11", "pazarama", "teknosa", "mediamarkt"]


# ----------------------------------------------------------------- eslestirme
# Sorguda gecmiyorsa farkli model sayilan kelimeler. "iPhone 15" ararken
# "iPhone 15 Pro" en ucuz sonuc olmamali; ikisi ayri urunler.
VARIANT_WORDS = frozenset({
    "pro", "max", "plus", "ultra", "mini", "lite", "fe", "air", "se", "neo",
    "prime", "edge", "slim", "note", "xl", "xs", "xr", "promax",
})

# Sorguda yoksa elenen kelimeler. Kelime siniri aranir: "kapak" -> "kapakli"
# eslesmez. Kullanici bu kelimeyi sorgusuna yazarsa kural devreden cikar.
DEFAULT_EXCLUDE = (
    "kılıf", "kilif", "koruyucu", "aksesuar", "yedek", "yenilenmiş", "yenilenmis",
    "ikinci el", "2 el", "teşhir", "a kalite", "b kalite", "c kalite",
)

# Diger tekliflerin medyaninin bu katindan ucuz teklif supheli sayilir
OUTLIER_RATIO = Decimal("0.45")


@dataclass
class MatchRules:
    """Bir teklifin aranan urun olup olmadigina karar veren kurallar."""
    must_include: list[str] = None      # baslikta mutlaka gecmeli
    exclude: list[str] = None           # gecerse elenir
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_token_ratio: float = 1.0        # sorgu kelimelerinin kaci eslesmeli
    include_keys: list[str] = None      # kullanicinin onayladigi ilanlar
    exclude_keys: list[str] = None      # kullanicinin reddettigi ilanlar
    default_exclude: tuple = DEFAULT_EXCLUDE
    variants: bool = True               # model ayrimi kontrolu
    outliers: bool = True               # asiri ucuz teklif kontrolu

    def __post_init__(self):
        self.must_include = self.must_include or []
        self.exclude = self.exclude or []
        self.include_keys = [listing_key(k) if "://" in k else k for k in (self.include_keys or [])]
        self.exclude_keys = [listing_key(k) if "://" in k else k for k in (self.exclude_keys or [])]


def _phrase_in(phrase: str, tokens: list[str]) -> bool:
    """Normalize edilmis ifade baslikta kelime siniriyla geciyor mu?"""
    words = normalize(phrase).split()
    if not words:
        return False
    n = len(words)
    return any(tokens[i:i + n] == words for i in range(len(tokens) - n + 1))


def match_reason(query: str, offer: Offer, rules: MatchRules) -> str | None:
    """Teklif elendiyse sebebini, kabul edildiyse None doner (tekil kontrol)."""
    result = _check(query, offer, rules)
    return result[1] if result else None


def _check(query: str, offer: Offer, rules: MatchRules) -> tuple[str, str, bool] | None:
    """(kod, sebep, yakin_mi) ya da None."""
    title = normalize(offer.title)
    tokens = title.split()
    qnorm = normalize(query)

    for word in rules.exclude:
        w = normalize(word)
        if w and w in title:
            return "exclude", f"dislanan kelime: {word!r}", False

    for word in rules.default_exclude or ():
        w = normalize(word)
        if w and w not in qnorm and _phrase_in(w, tokens):
            return "default_exclude", f"{word!r} iceriyor (aksesuar/ikinci el olabilir)", False

    # "Apple Uyumlu iPhone 15 ..." / "Samsung icin ..." kaliplari aksesuardir:
    # uyumluluk kelimesi urun adindan ONCE geliyorsa satilan sey o urun degildir.
    if rules.default_exclude and not {"uyumlu", "icin"} & set(qnorm.split()):
        qtok = query_tokens(query)
        first = next((i for i, t in enumerate(tokens) if any(token_hit(q, (t,)) for q in qtok)), None)
        if first is not None and {"uyumlu", "icin"} & set(tokens[max(0, first - 3):first]):
            return "default_exclude", "uyumlu aksesuar gibi gorunuyor", False

    for word in rules.must_include:
        if normalize(word) not in title:
            return "must_include", f"zorunlu kelime yok: {word!r}", True

    qtokens = query_tokens(query)
    if qtokens:
        tset = set(tokens)
        missing = [t for t in qtokens if not token_hit(t, tset)]
        hit = len(qtokens) - len(missing)
        if hit / len(qtokens) < rules.min_token_ratio:
            near = hit / len(qtokens) >= 0.6
            return ("tokens",
                    f"baslik sorguyla ortusmuyor ({hit}/{len(qtokens)}, eksik: {', '.join(missing)})",
                    near)

    if rules.min_price is not None and offer.price < rules.min_price:
        return "price", f"fiyat alt sinirin altinda ({offer.price} < {rules.min_price})", True
    if rules.max_price is not None and offer.price > rules.max_price:
        return "price", f"fiyat ust sinirin uzerinde ({offer.price} > {rules.max_price})", True
    return None


def variant_tokens(query: str, title: str) -> set[str]:
    """Baslikta olup sorguda olmayan model ayrimi kelimeleri.

    Yalnizca sorgu kelimelerinin baslikta kapladigi bolgeye (ve hemen
    sonrasina) bakariz: "iPhone 15 Pro" -> {"pro"}. Basligin sonundaki
    "2000W max guc" gibi aciklamalara takilmamak icin bolge dar tutulur.
    Bolgenin icindeki tek harfler de ayrim sayilir: sorgu "iZi Turn i-Size",
    baslik "iZi Turn B i-Size" -> {"b"}.
    """
    qall = set(normalize(query).split())
    qtokens = query_tokens(query)
    toks = normalize(title).split()
    # Her sorgu kelimesinin baslikta ILK gectigi yer. Basliklar kelimeleri
    # tekrarlayabiliyor ("... 360 Pro ... 360 derece"); son gecisi alirsak
    # bolge basligin tamamina yayilir ve alakasiz harfler ayrim sanilir.
    positions = []
    for q in qtokens or list(qall):
        first = next((i for i, t in enumerate(toks) if t == q or token_hit(q, (t,))), None)
        if first is not None:
            positions.append(first)
    if not positions:
        return set()
    lo, hi = min(positions), max(positions)
    found = set()
    for i in range(lo, min(len(toks), hi + 3)):
        t = toks[i]
        if t in qall or token_hit_any(qtokens, t):
            continue
        if t in VARIANT_WORDS:
            found.add(t)
        elif len(t) == 1 and t.isalpha() and lo < i < hi:
            found.add(t)
    return found


def token_hit_any(qtokens: list[str], title_token: str) -> bool:
    return any(token_hit(q, (title_token,)) for q in qtokens)


@dataclass
class SearchResult:
    """Bir aramanin sonucu: kabul edilenler (ucuzdan pahaliya) ve elenenler."""
    offers: list[Offer] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    per_site: dict[str, dict] = field(default_factory=dict)

    def __iter__(self):            # eski kullanim: offers, notes = search(...)
        yield self.offers
        yield self.notes

    @property
    def near(self) -> list[Rejected]:
        return [r for r in self.rejected if r.near]

    def to_dict(self, limit: int = 40, rejected_limit: int = 25) -> dict:
        rej = sorted(self.rejected, key=lambda r: (not r.near, r.offer.price))
        return {
            "offers": [o.to_dict() for o in self.offers[:limit]],
            "rejected": [r.to_dict() for r in rej[:rejected_limit]],
            "notes": self.notes,
            "warnings": self.warnings,
            "per_site": self.per_site,
            "count": len(self.offers),
        }


def _median(values: list[Decimal]) -> Decimal:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def evaluate(query: str, found: list[Offer], rules: MatchRules | None = None) -> SearchResult:
    """Toplanan teklifleri kurallara gore ayiklar (ag erisimi yok, test edilebilir)."""
    rules = rules or MatchRules()
    result = SearchResult()

    # 1) Ayni ilan birden fazla gelirse (reklam + organik) en ucuzunu tut
    unique: dict[str, Offer] = {}
    for o in found:
        key = o.key or f"{o.site}:{o.title}:{o.price}"
        if key not in unique or o.price < unique[key].price:
            unique[key] = o

    pinned, candidates = [], []
    for o in unique.values():
        if o.key and o.key in rules.exclude_keys:
            result.rejected.append(Rejected(o, "user", "bu ilani 'bu urun degil' diye isaretledin"))
            continue
        if o.key and o.key in rules.include_keys:
            pinned.append(o)              # kullanici onayladi: kurallar uygulanmaz
            continue
        problem = _check(query, o, rules)
        if problem:
            code, reason, near = problem
            result.rejected.append(Rejected(o, code, reason, near))
        else:
            candidates.append(o)

    # 2) Model ayrimi: sorguyla birebir eslesen varsa, fazladan model kelimesi
    #    tasiyanlari ele. Hepsi tasiyorsa (ornegin sorgu eski model adi) kabul
    #    et ama uyar; sessizce yanlis urun takip etmektense kullanici gorsun.
    if rules.variants and candidates:
        tagged = [(o, variant_tokens(query, o.title)) for o in candidates]
        exact = [o for o, v in tagged if not v]
        if exact and len(exact) < len(tagged):
            for o, v in tagged:
                if v:
                    result.rejected.append(Rejected(
                        o, "variant", f"farkli model olabilir (sorguda yok: {', '.join(sorted(v))})", True))
            candidates = exact
        elif not exact:
            common = sorted(set.intersection(*(v for _, v in tagged)))
            extra = common or sorted(set.union(*(v for _, v in tagged)))
            result.warnings.append(
                f"Bulunan ürünlerin adında sorguda olmayan \"{', '.join(extra)}\" geçiyor; "
                "farklı bir model olabilir. Doğru ürün olduğunu kontrol et.")

    # 3) Asiri ucuz teklif: medyanin cok altindaysa aksesuar/yanlis urun olabilir
    if rules.outliers and len(candidates) >= 3:
        med = _median([o.price for o in candidates])
        floor = med * OUTLIER_RATIO
        kept = []
        for o in candidates:
            if o.price < floor:
                result.rejected.append(Rejected(
                    o, "outlier",
                    f"diger tekliflerden cok ucuz (medyan {med:.0f}); aksesuar ya da yanlis urun olabilir",
                    True))
            else:
                kept.append(o)
        candidates = kept

    result.offers = sorted(pinned + candidates, key=lambda o: o.price)
    return result


def _search_site(site: str, query: str, fetcher_factory) -> tuple[str, list[Offer], str]:
    entry = SOURCES.get(site)
    if entry is None:
        return site, [], f"{site}: bilinmeyen kaynak"
    template, parser = entry
    fetcher = fetcher_factory()
    try:
        result = fetcher.get(template.format(q=quote_plus(query)))
    finally:
        if getattr(fetcher, "_owned", False):
            fetcher.close()
    if not result.ok:
        return site, [], f"{site}: {result.block_reason or result.error}"
    try:
        soup = BeautifulSoup(result.html, "lxml")
        found = parser(result.html, soup)
    except Exception as exc:                  # bir kaynak digerlerini bozmasin
        log.warning("%s arama ayristirmasi hata verdi: %s", site, exc)
        return site, [], f"{site}: ayristirilamadi ({exc})"
    return site, found, ""


def search(query: str, fetcher, sites: list[str] | None = None,
           rules: MatchRules | None = None, parallel: bool = True) -> SearchResult:
    """Sorguyu verilen sitelerde arar ve teklifleri ayiklar.

    Siteler paralel taranir; her site kendi alan adina gittigi icin birbirini
    beklemesi gerekmiyor. `fetcher` ya bir Fetcher ya da Fetcher ureten bir
    fonksiyondur (test icin sahte indirici verilebilir).
    """
    rules = rules or MatchRules()
    sites = [s for s in (sites or DEFAULT_SITES) if s]

    def factory():
        return fetcher() if callable(fetcher) and not hasattr(fetcher, "get") else fetcher

    collected: list[Offer] = []
    raw_notes: dict[str, str] = {}
    counts: dict[str, int] = {}

    if parallel and len(sites) > 1 and hasattr(fetcher, "get") and hasattr(fetcher, "fork"):
        # Her site icin ayri oturum: curl oturumlari is parcaciklari arasinda paylasilmaz
        forks = {s: fetcher.fork() for s in sites}
        with ThreadPoolExecutor(max_workers=len(sites)) as pool:
            futures = [pool.submit(_search_site, s, query, lambda s=s: forks[s]) for s in sites]
            outputs = [f.result() for f in futures]
        for f in forks.values():
            fetcher.absorb(f)
    else:
        outputs = [_search_site(s, query, factory) for s in sites]

    for site, found, note in outputs:
        counts[site] = len(found)
        if note:
            raw_notes[site] = note
        collected.extend(found)

    result = evaluate(query, collected, rules)
    kept_by_site: dict[str, int] = {}
    for o in result.offers:
        name = next((s for s in sites if o.site.startswith(s) or s in o.site), o.site)
        kept_by_site[name] = kept_by_site.get(name, 0) + 1
    for site in sites:
        if site in raw_notes:
            result.notes.append(raw_notes[site])
            result.per_site[site] = {"ok": False, "found": 0, "kept": 0, "note": raw_notes[site]}
        else:
            kept = kept_by_site.get(site, 0)
            result.notes.append(f"{site}: {counts.get(site, 0)} sonuc, {kept} eslesti")
            result.per_site[site] = {"ok": True, "found": counts.get(site, 0), "kept": kept}
    return result
