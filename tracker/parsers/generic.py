"""Siteden bagimsiz ayristirma: JSON-LD, microdata, Open Graph meta, CSS secici.

Cogu e-ticaret sitesi schema.org/Product yapisini sayfaya gomuyor. Once onu
deneriz; bu en guvenilir kaynaktir cunku sitenin kendi makine-okunur beyanidir.
Tutmazsa sirasiyla microdata, meta etiketleri ve yaygin fiyat secicilerine ineriz.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..money import detect_currency, parse_machine_price, parse_price
from .base import ParseResult, first_match, text_of, walk

# Yaygin fiyat CSS secicileri (en spesifikten en genele)
PRICE_SELECTORS = [
    '[itemprop="price"]',
    '[data-test-id*="price" i]',
    '[data-testid*="price" i]',
    ".product-price .price",
    ".prc-dsc",
    ".price-current",
    ".current-price",
    ".sale-price",
    ".product-price",
    ".price",
]

AVAILABILITY_SELECTORS = ['[itemprop="availability"]', 'meta[property="product:availability"]']

_OUT_OF_STOCK_WORDS = re.compile(
    r"t[uü]kendi|stokta yok|out\s*of\s*stock|soldout|sold\s*out|temin edilemiyor|"
    r"[uü]r[uü]n bulunamad|sat[ıi][sş]a kapal",
    re.IGNORECASE,
)


def _availability_to_bool(value) -> bool | None:
    if not value:
        return None
    text = str(value).lower()
    if "outofstock" in text.replace(" ", "") or "soldout" in text.replace(" ", ""):
        return False
    if "instock" in text.replace(" ", "") or "limitedavailability" in text.replace(" ", ""):
        return True
    if "backorder" in text.replace(" ", "") or "preorder" in text.replace(" ", ""):
        return False
    return None


def iter_jsonld(soup: BeautifulSoup):
    """Sayfadaki tum JSON-LD bloklarini okunabilir dict'ler olarak uretir."""
    for tag in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Bazi siteler JSON-LD icine kacislanmamis yeni satir birakiyor
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]+", " ", raw))
            except json.JSONDecodeError:
                continue
        yield from walk(data)


def _offer_price(offer: dict) -> tuple[object, str | None]:
    """Bir Offer/AggregateOffer nesnesinden fiyat ve para birimini cikarir."""
    for key in ("price", "lowPrice", "highPrice"):
        if key in offer and offer[key] not in (None, "", []):
            return offer[key], offer.get("priceCurrency")
    spec = offer.get("priceSpecification")
    if isinstance(spec, dict):
        for key in ("price", "minPrice"):
            if spec.get(key) not in (None, "", []):
                return spec[key], spec.get("priceCurrency") or offer.get("priceCurrency")
    return None, offer.get("priceCurrency")


def from_jsonld(soup: BeautifulSoup) -> ParseResult | None:
    """schema.org Product/Offer verisinden fiyat okur."""
    best: ParseResult | None = None

    for node in iter_jsonld(soup):
        types = node.get("@type")
        types = [types] if isinstance(types, str) else (types or [])
        types_lower = {str(t).lower() for t in types}

        offers = node.get("offers")
        is_product = "product" in types_lower or "productgroup" in types_lower
        is_offer = bool(types_lower & {"offer", "aggregateoffer"})

        if not (is_product and offers) and not is_offer:
            continue

        candidates = []
        if is_offer:
            candidates.append(node)
        if offers:
            candidates.extend(offers if isinstance(offers, list) else [offers])

        for offer in candidates:
            if not isinstance(offer, dict):
                continue
            raw_price, currency = _offer_price(offer)
            price = parse_machine_price(raw_price)
            if price is None:
                continue

            result = ParseResult(
                price=price,
                currency=(currency or "TRY").upper(),
                in_stock=_availability_to_bool(offer.get("availability")),
                title=node.get("name") if is_product else None,
                raw=str(raw_price),
                method="jsonld",
            )
            # Product icindeki teklif, tek basina duran Offer'dan daha guveniliridir
            if is_product:
                return result
            best = best or result

    return best


def from_microdata(soup: BeautifulSoup) -> ParseResult | None:
    node, sel = first_match(soup, ['[itemprop="price"]'])
    if node is None:
        return None
    raw = text_of(node)
    # itemprop=price icerigi genelde makine formatindadir
    price = parse_machine_price(raw) or parse_price(raw)
    if price is None:
        return None

    currency_node = soup.select_one('[itemprop="priceCurrency"]')
    currency = (text_of(currency_node) or detect_currency(raw or "")).upper()

    avail_node, _ = first_match(soup, AVAILABILITY_SELECTORS)
    avail = avail_node.get("href") or text_of(avail_node) if avail_node is not None else None

    return ParseResult(
        price=price,
        currency=currency if len(currency) == 3 else "TRY",
        in_stock=_availability_to_bool(avail),
        raw=raw,
        method=f"microdata({sel})",
    )


def from_meta(soup: BeautifulSoup) -> ParseResult | None:
    meta_price = soup.select_one(
        'meta[property="product:price:amount"], meta[property="og:price:amount"], '
        'meta[name="product:price:amount"], meta[itemprop="price"]'
    )
    if meta_price is None:
        return None
    raw = meta_price.get("content")
    price = parse_machine_price(raw)
    if price is None:
        return None

    meta_cur = soup.select_one(
        'meta[property="product:price:currency"], meta[property="og:price:currency"]'
    )
    currency = (meta_cur.get("content") if meta_cur else None) or "TRY"

    meta_avail = soup.select_one(
        'meta[property="product:availability"], meta[property="og:availability"]'
    )
    return ParseResult(
        price=price,
        currency=currency.upper(),
        in_stock=_availability_to_bool(meta_avail.get("content") if meta_avail else None),
        raw=str(raw),
        method="meta",
    )


def from_selectors(soup: BeautifulSoup, selectors: list[str] | None = None) -> ParseResult | None:
    node, sel = first_match(soup, selectors or PRICE_SELECTORS)
    if node is None:
        return None
    raw = text_of(node)
    price = parse_price(raw)
    if price is None:
        return None
    return ParseResult(
        price=price,
        currency=detect_currency(raw or ""),
        raw=raw,
        method=f"selector({sel})",
    )


def page_title(soup: BeautifulSoup) -> str | None:
    for sel in ('meta[property="og:title"]', 'meta[name="title"]'):
        node = soup.select_one(sel)
        if node and node.get("content"):
            return node["content"].strip()
    h1 = soup.select_one("h1")
    if h1:
        return h1.get_text(" ", strip=True)[:200] or None
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:200]
    return None


def stock_hint(soup: BeautifulSoup) -> bool | None:
    """Sayfa metninde "tukendi" gibi ifade ariyarak stok durumunu tahmin eder."""
    body = soup.body
    if body is None:
        return None
    text = body.get_text(" ", strip=True)[:6000]
    return False if _OUT_OF_STOCK_WORDS.search(text) else None


def parse(html: str, soup: BeautifulSoup, url: str, selectors: list[str] | None = None) -> ParseResult:
    """Genel ayristirma zinciri: JSON-LD -> microdata -> meta -> CSS secici."""
    result = None
    # Kullanici kendi secicisini verdiyse once onu deneriz
    if selectors:
        result = from_selectors(soup, selectors)
    for step in (from_jsonld, from_microdata, from_meta, from_selectors):
        if result and result.ok:
            break
        result = step(soup)

    result = result or ParseResult(method="bulunamadi")
    if not result.title:
        result.title = page_title(soup)
    if result.in_stock is None:
        result.in_stock = stock_hint(soup)
    return result
