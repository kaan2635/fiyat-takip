"""Urun listesi ve ayarlarin okunmasi/yazilmasi (products.yaml)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from .parsers import site_of

DEFAULT_PATH = Path("products.yaml")

DEFAULT_SETTINGS: dict = {
    # Fiyat bu yuzdeden fazla duserse bildirim gonderilir
    "drop_alert_percent": 5.0,
    # Hedef fiyatin altina inince bildirim
    "notify_on_target": True,
    # Stoga girince bildirim
    "notify_on_back_in_stock": True,
    # HTTP engellenirse gercek tarayiciya yukselt
    "browser_fallback": True,
    # Ayni siteye istekler arasi en az bekleme (saniye)
    "min_delay_seconds": 2.0,
    "timeout_seconds": 30,
}

_TR_MAP = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")


def slugify(text: str, fallback: str = "urun") -> str:
    """Basligi dosya/anahtar icin guvenli bir kimlige cevirir."""
    text = (text or "").translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:60] or fallback)


@dataclass
class Product:
    id: str
    url: str
    name: str = ""
    target_price: Decimal | None = None
    mode: str = "http"              # "http" | "browser"
    selector: str | None = None     # siteye ozel CSS secici (istege bagli)
    enabled: bool = True
    note: str = ""

    @property
    def site(self) -> str:
        return site_of(self.url)

    def to_dict(self) -> dict:
        data = {"id": self.id, "name": self.name, "url": self.url}
        if self.target_price is not None:
            data["target_price"] = float(self.target_price)
        if self.mode != "http":
            data["mode"] = self.mode
        if self.selector:
            data["selector"] = self.selector
        if not self.enabled:
            data["enabled"] = False
        if self.note:
            data["note"] = self.note
        return data


@dataclass
class Config:
    settings: dict = field(default_factory=lambda: dict(DEFAULT_SETTINGS))
    products: list[Product] = field(default_factory=list)
    path: Path = DEFAULT_PATH

    # ------------------------------------------------------------------ okuma
    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Config":
        path = Path(path)
        if not path.exists():
            return cls(path=path)

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}

        products: list[Product] = []
        for entry in raw.get("products") or []:
            if isinstance(entry, str):          # sadece URL yazilmis olabilir
                entry = {"url": entry}
            url = (entry.get("url") or "").strip()
            if not url:
                continue
            name = entry.get("name") or ""
            pid = entry.get("id") or slugify(name or url)
            target = entry.get("target_price")
            products.append(
                Product(
                    id=str(pid),
                    url=url,
                    name=name,
                    target_price=Decimal(str(target)) if target not in (None, "") else None,
                    mode=(entry.get("mode") or "http").lower(),
                    selector=entry.get("selector"),
                    enabled=entry.get("enabled", True),
                    note=entry.get("note", ""),
                )
            )
        return cls(settings=settings, products=products, path=path)

    # ------------------------------------------------------------------ yazma
    def save(self) -> None:
        data = {
            "settings": self.settings,
            "products": [p.to_dict() for p in self.products],
        }
        self.path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8",
        )

    # --------------------------------------------------------------- yardimci
    def get(self, key: str):
        return self.settings.get(key, DEFAULT_SETTINGS.get(key))

    def find(self, needle: str) -> Product | None:
        needle = needle.strip().lower()
        for p in self.products:
            if p.id.lower() == needle or p.url.lower() == needle:
                return p
        for p in self.products:           # kismi isim eslesmesi
            if needle in p.name.lower() or needle in p.id.lower():
                return p
        return None

    def unique_id(self, base: str) -> str:
        existing = {p.id for p in self.products}
        if base not in existing:
            return base
        i = 2
        while f"{base}-{i}" in existing:
            i += 1
        return f"{base}-{i}"

    @property
    def active(self) -> list[Product]:
        return [p for p in self.products if p.enabled]
