"""Ayristiricilarin ortak veri tipi ve yardimcilari."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class ParseResult:
    price: Decimal | None = None
    currency: str = "TRY"
    in_stock: bool | None = None
    title: str | None = None
    raw: str | None = None          # ham fiyat metni (hata ayiklama icin)
    method: str = "?"               # fiyati hangi yontem buldu
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.price is not None


def text_of(node) -> str | None:
    """Bir BeautifulSoup dugumunden fiyat metnini cikarir.

    Onceligi `content`/`value` gibi makine alanlarina verir, yoksa metne bakar.
    """
    if node is None:
        return None
    for attr in ("content", "value", "data-price", "datetime"):
        val = node.get(attr) if hasattr(node, "get") else None
        if val:
            return str(val).strip()
    text = node.get_text(" ", strip=True) if hasattr(node, "get_text") else str(node)
    return text or None


def first_match(soup, selectors: list[str]):
    """Verilen CSS seciciler icinde ilk eslesen dugumu (ve seciciyi) doner."""
    for sel in selectors:
        try:
            node = soup.select_one(sel)
        except Exception:
            continue
        if node is not None:
            return node, sel
    return None, None


_JSON_OBJ = re.compile(r"[{\[]")


def json_after(html: str, marker: str) -> dict | list | None:
    """`marker` ifadesinden sonra gelen ilk JSON nesnesini dengeli parantezle ayirir.

    Trendyol gibi siteler urun verisini `window.__X__ = {...};` seklinde
    sayfaya gomuyor; duz regex ic ice suslu parantezlerde kirildigi icin
    parantez sayarak dogru biten yeri buluyoruz.
    """
    idx = html.find(marker)
    if idx == -1:
        return None
    start_match = _JSON_OBJ.search(html, idx + len(marker))
    if not start_match:
        return None

    start = start_match.start()
    opener = html[start]
    closer = {"{": "}", "[": "]"}[opener]

    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def walk(obj):
    """Ic ice JSON yapisindaki tum dict ve list dugumlerini dolasir."""
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
