"""Urun adiyla fiyat arama.

URL vermek yerine "BeSafe iZi Turn B i-Size" gibi bir sorgu yazilir; sistem
pazaryerlerinin arama sayfalarini gezip teklifleri toplar ve en ucuzunu takip
eder.

Isin zor kismi ayristirma degil, **eslestirme**. Arama motorlari alakasiz
sonuc dondurebiliyor: olcumde n11, bu sorgu icin hic urun bulamayip yerine
"onerilen urunler" listesinden bir psikoloji kitabi dondurdu. Bu yuzden her
teklif, basligi sorgunun tum anlamli kelimelerini icermedikce elenir; ayrica
kullanici `exclude` ve fiyat araligi verebilir.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote_plus, urljoin

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
})


def normalize(text: str) -> str:
    """Karsilastirma icin metni sadelestirir: harf/rakam disi her sey bosluk olur."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").translate(_FOLD).lower()).strip()


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


def token_hit(token: str, title_tokens: set[str]) -> bool:
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


@dataclass
class Offer:
    """Bir saticidaki tek bir fiyat teklifi."""
    site: str
    title: str
    price: Decimal
    url: str
    currency: str = "TRY"

    def __str__(self) -> str:
        return f"{self.site}: {self.price} — {self.title[:60]}"


# ----------------------------------------------------------------- ayristirma
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
        for key in ("discountedPrice", "current", "originalPrice"):
            if price_block.get(key) not in (None, "", 0):
                raw = price_block[key]
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

        url = p.get("url") or ""
        offers.append(Offer(
            site="trendyol.com", title=title, price=price,
            url=urljoin("https://www.trendyol.com", url),
            currency=(price_block.get("currency") or "TRY").replace("TL", "TRY"),
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

        offers.append(Offer(
            site="hepsiburada.com",
            title=title_node.get_text(" ", strip=True),
            price=price,
            url=urljoin("https://www.hepsiburada.com", link["href"]),
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


SOURCES: dict[str, tuple[str, callable]] = {
    "trendyol": ("https://www.trendyol.com/sr?q={q}", parse_trendyol),
    "hepsiburada": ("https://www.hepsiburada.com/ara?q={q}", parse_hepsiburada),
    "n11": ("https://www.n11.com/arama?q={q}", parse_n11),
}

DEFAULT_SITES = ["trendyol", "hepsiburada"]


# ----------------------------------------------------------------- eslestirme
@dataclass
class MatchRules:
    """Bir teklifin aranan urun olup olmadigina karar veren kurallar."""
    must_include: list[str] = None      # baslikta mutlaka gecmeli
    exclude: list[str] = None           # gecerse elenir
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_token_ratio: float = 1.0        # sorgu kelimelerinin kaci eslesmeli

    def __post_init__(self):
        self.must_include = self.must_include or []
        self.exclude = self.exclude or []


def match_reason(query: str, offer: Offer, rules: MatchRules) -> str | None:
    """Teklif elendiyse sebebini, kabul edildiyse None doner."""
    title = normalize(offer.title)

    for word in rules.exclude:
        if normalize(word) and normalize(word) in title:
            return f"dislanan kelime: {word!r}"

    for word in rules.must_include:
        if normalize(word) not in title:
            return f"zorunlu kelime yok: {word!r}"

    tokens = query_tokens(query)
    if tokens:
        title_tokens = set(title.split())
        missing = [t for t in tokens if not token_hit(t, title_tokens)]
        hit = len(tokens) - len(missing)
        if hit / len(tokens) < rules.min_token_ratio:
            return f"baslik sorguyla ortusmuyor ({hit}/{len(tokens)}, eksik: {', '.join(missing)})"

    if rules.min_price is not None and offer.price < rules.min_price:
        return f"fiyat alt sinirin altinda ({offer.price} < {rules.min_price})"
    if rules.max_price is not None and offer.price > rules.max_price:
        return f"fiyat ust sinirin uzerinde ({offer.price} > {rules.max_price})"
    return None


def search(query: str, fetcher, sites: list[str] | None = None,
           rules: MatchRules | None = None) -> tuple[list[Offer], list[str]]:
    """Sorguyu verilen sitelerde arar; (kabul edilen teklifler, notlar) doner.

    Teklifler fiyata gore artan siralidir, yani ilki en ucuzudur.
    """
    rules = rules or MatchRules()
    sites = sites or DEFAULT_SITES
    accepted: list[Offer] = []
    notes: list[str] = []

    for site in sites:
        entry = SOURCES.get(site)
        if entry is None:
            notes.append(f"{site}: bilinmeyen kaynak")
            continue
        template, parser = entry

        result = fetcher.get(template.format(q=quote_plus(query)))
        if not result.ok:
            notes.append(f"{site}: {result.block_reason or result.error}")
            continue

        try:
            soup = BeautifulSoup(result.html, "lxml")
            found = parser(result.html, soup)
        except Exception as exc:                  # bir kaynak digerlerini bozmasin
            log.warning("%s arama ayristirmasi hata verdi: %s", site, exc)
            notes.append(f"{site}: ayristirilamadi ({exc})")
            continue

        kept = 0
        for offer in found:
            if match_reason(query, offer, rules) is None:
                accepted.append(offer)
                kept += 1
        notes.append(f"{site}: {len(found)} sonuc, {kept} eslesti")

    accepted.sort(key=lambda o: o.price)
    return accepted, notes
