"""Ayristirici secimi: once siteye ozel yontem, sonra genel zincir."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from . import generic
from .base import ParseResult
from .sites import SITE_PARSERS

log = logging.getLogger("tracker.parsers")

__all__ = ["ParseResult", "parse_page", "site_of", "SITE_PARSERS"]


def _make_soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # lxml kurulu degilse stdlib ayristiricisina duseriz
        return BeautifulSoup(html, "html.parser")


def site_of(url: str) -> str:
    """URL'den sade alan adi: 'https://www.trendyol.com/x' -> 'trendyol.com'."""
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _site_parser(host: str):
    for domain, parser in SITE_PARSERS.items():
        if host == domain or host.endswith("." + domain):
            return parser, domain
    return None, None


def parse_page(html: str, url: str, selectors: list[str] | None = None) -> ParseResult:
    """Bir urun sayfasindan fiyat, stok ve basligi cikarir."""
    soup = _make_soup(html)
    host = site_of(url)
    parser, domain = _site_parser(host)

    result: ParseResult | None = None
    if parser is not None:
        try:
            result = parser(html, soup, url)
        except Exception as exc:  # tek bir sitenin hatasi taramayi durdurmasin
            log.warning("%s ayristiricisi hata verdi: %s", domain, exc)
            result = None

    if result is None or not result.ok:
        fallback = generic.parse(html, soup, url, selectors=selectors)
        if parser is not None and fallback.ok:
            fallback.notes.append(f"{domain} ozel ayristirici tutmadi, genel yontem kullanildi")
        result = fallback

    if not result.title:
        result.title = generic.page_title(soup)
    if result.in_stock is None:
        result.in_stock = generic.stock_hint(soup)
    return result
