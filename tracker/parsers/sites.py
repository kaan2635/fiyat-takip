"""Site ozel ayristiricilar.

Her site once kendi ozel yontemiyle denenir; basarisiz olursa generic zincire
duseriz. Bir site HTML'ini degistirdiginde genelde JSON-LD ayakta kaldigi icin
sistem tamamen kor kalmaz.

Yeni site eklemek icin: asagiya bir fonksiyon yaz, SITE_PARSERS'a alan adini ekle.
"""

from __future__ import annotations

import re
from decimal import Decimal

from bs4 import BeautifulSoup

from ..money import detect_currency, parse_machine_price, parse_price
from . import generic
from .base import ParseResult, first_match, json_after, text_of, walk


def _pick(soup: BeautifulSoup, selectors: list[str], method_prefix: str) -> ParseResult | None:
    node, sel = first_match(soup, selectors)
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
        method=f"{method_prefix}({sel})",
    )


# --------------------------------------------------------------------------- Trendyol
def trendyol(html: str, soup: BeautifulSoup, url: str) -> ParseResult | None:
    """Trendyol urun verisini sayfaya gomulu JSON state icinden okur."""
    for marker in (
        "__PRODUCT_DETAIL_APP_INITIAL_STATE__",
        "window.__envoy_flashSales__",
    ):
        state = json_after(html, marker)
        if not isinstance(state, (dict, list)):
            continue
        for node in walk(state):
            price_block = node.get("price")
            if not isinstance(price_block, dict):
                continue
            # discountedPrice indirim sonrasi odenecek tutardir, once o denenir
            for key in ("discountedPrice", "sellingPrice", "originalPrice"):
                entry = price_block.get(key)
                value = entry.get("value") if isinstance(entry, dict) else entry
                price = parse_machine_price(value)
                if price is None:
                    continue
                name = node.get("name") or node.get("productName")
                in_stock = None
                if isinstance(node.get("isSellable"), bool):
                    in_stock = node["isSellable"]
                elif isinstance(node.get("stock"), dict):
                    in_stock = bool(node["stock"].get("quantity", 0))
                return ParseResult(
                    price=price,
                    currency=(price_block.get("currency") or "TRY").upper(),
                    in_stock=in_stock,
                    title=name,
                    raw=str(value),
                    method=f"trendyol:state.{key}",
                )

    return _pick(soup, [".prc-dsc", ".prc-slg", ".product-price-container .prc-dsc"], "trendyol")


# ----------------------------------------------------------------------- Hepsiburada
def hepsiburada(html: str, soup: BeautifulSoup, url: str) -> ParseResult | None:
    result = _pick(
        soup,
        [
            '[data-test-id="price-current-price"]',
            "#offering-price",
            'span[data-bind*="currentPriceBeforePoint"]',
            ".product-price span",
        ],
        "hepsiburada",
    )
    if result and result.ok:
        # Stok bilgisi fiyat elemaninda yok, sayfa metninden tahmin ederiz
        result.in_stock = generic.stock_hint(soup)
        return result

    state = json_after(html, "window.__PRODUCT_DETAIL_APP_INITIAL_STATE__")
    if isinstance(state, dict):
        for node in walk(state):
            price = parse_machine_price(node.get("currentPrice") or node.get("price"))
            if price is not None:
                return ParseResult(
                    price=price, currency="TRY", title=node.get("name"),
                    raw=str(node.get("currentPrice") or node.get("price")),
                    method="hepsiburada:state",
                )
    return None


# ------------------------------------------------------------------------------- n11
def n11(html: str, soup: BeautifulSoup, url: str) -> ParseResult | None:
    return _pick(
        soup,
        [
            ".newPrice ins",
            'ins[itemprop="price"]',
            ".priceContainer .newPrice",
            "#displayPrice",
        ],
        "n11",
    )


# ------------------------------------------------------------------------ Amazon (TR)
_AMAZON_UNAVAILABLE = re.compile(r"mevcut de[gğ]il|stokta yok|currently unavailable", re.I)


def amazon(html: str, soup: BeautifulSoup, url: str) -> ParseResult | None:
    """Amazon'da gorunur fiyat `.a-offscreen` icinde ekran okuyucular icin durur."""
    result = _pick(
        soup,
        [
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            "#corePrice_feature_div .a-price .a-offscreen",
            "#apex_desktop .a-price .a-offscreen",
            "#priceblock_ourprice",
            "#priceblock_dealprice",
            ".a-price .a-offscreen",
        ],
        "amazon",
    )
    if not result:
        return None

    title_node = soup.select_one("#productTitle")
    if title_node:
        result.title = title_node.get_text(" ", strip=True)

    avail = soup.select_one("#availability")
    if avail is not None:
        text = avail.get_text(" ", strip=True)
        result.in_stock = not _AMAZON_UNAVAILABLE.search(text)
    return result


# ------------------------------------------------------------------------ Çiçeksepeti
def ciceksepeti(html: str, soup: BeautifulSoup, url: str) -> ParseResult | None:
    return _pick(
        soup,
        [".product-price__now", ".productPrice .priceNew", ".pr-new-price", ".product-price"],
        "ciceksepeti",
    )


SITE_PARSERS: dict[str, callable] = {
    "trendyol.com": trendyol,
    "hepsiburada.com": hepsiburada,
    "n11.com": n11,
    "amazon.com.tr": amazon,
    "amazon.com": amazon,
    "amazon.de": amazon,
    "ciceksepeti.com": ciceksepeti,
}
