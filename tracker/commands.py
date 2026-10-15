"""Uygulamadan gelen JSON komutlarinin uygulanmasi (`python -m tracker apply`).

Telefondaki uygulama depoya dogrudan yazmaz; is akisini `komut` girdisiyle
tetikler. Komut bir islem listesidir:

    {"ops": [
      {"op": "add", "query": "BeSafe iZi Turn B i-Size", "target": 42000,
       "exclude_listings": ["trendyol:123"]},
      {"op": "update", "id": "maxi-cosi-mica", "set": {"target": 24000}},
      {"op": "update", "id": "maxi-cosi-mica", "reject": ["n11:60303290"]},
      {"op": "remove", "id": "eski-urun"},
      {"op": "scan", "ids": ["maxi-cosi-mica"]}
    ]}

Girdi kullanicidan gelse de dogrulanir: bilinmeyen alanlar yok sayilir,
uzunluklar sinirlanir, sayilar kontrol edilir. Sonucta hangi urunlerin
yeniden taranmasi gerektigi doner (yalnizca eslestirmeyi etkileyen
degisiklikler tarama gerektirir; hedef fiyat ya da ad degisikligi gerektirmez).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from .config import Config, Product, slugify
from .search import SOURCES, listing_key
from .store import now_iso

MAX_OPS = 30
MAX_TEXT = 200
MAX_LIST = 60


class CommandError(ValueError):
    """Komut gecersiz; mesaj kullaniciya gosterilecek kadar acik olmali."""


@dataclass
class ApplyResult:
    changed: bool = False
    scan_ids: list[str] = field(default_factory=list)
    scan_all: bool = False
    messages: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"changed": self.changed, "scan_ids": self.scan_ids, "scan_all": self.scan_all,
                "messages": self.messages, "added": self.added}


def _text(value, name: str, required: bool = False) -> str:
    if value is None or value == "":
        if required:
            raise CommandError(f"'{name}' bos olamaz")
        return ""
    if not isinstance(value, (str, int, float)):
        raise CommandError(f"'{name}' metin olmali")
    text = " ".join(str(value).split())
    return text[:MAX_TEXT]


def _price(value, name: str) -> Decimal | None:
    if value in (None, "", 0):
        return None
    try:
        d = Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise CommandError(f"'{name}' gecerli bir sayi degil: {value!r}")
    if d <= 0 or d > Decimal("100000000"):
        raise CommandError(f"'{name}' makul bir fiyat degil: {value!r}")
    return d.quantize(Decimal("0.01"))


def _words(value, name: str) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        raise CommandError(f"'{name}' liste olmali")
    out = []
    for v in value[:MAX_LIST]:
        t = _text(v, name)
        if t and t not in out:
            out.append(t)
    return out


def _keys(value, name: str) -> list[str]:
    out = []
    for v in _words(value, name):
        key = listing_key(v) if "://" in v else v
        if key and key not in out:
            out.append(key)
    return out


def _sites(value) -> list[str] | None:
    sites = _words(value, "sites")
    unknown = [s for s in sites if s not in SOURCES]
    if unknown:
        raise CommandError(f"bilinmeyen kaynak: {', '.join(unknown)}")
    return sites or None


def _url(value) -> str:
    url = _text(value, "url")
    if not url:
        return ""
    if url.startswith("www."):
        url = "https://" + url
    if not url.startswith(("http://", "https://")):
        raise CommandError("adres http(s):// ile baslamali")
    return url


def _product(cfg: Config, op: dict) -> Product:
    pid = _text(op.get("id"), "id", required=True)
    product = cfg.by_id(pid)
    if product is None:
        raise CommandError(f"urun bulunamadi: {pid}")
    return product


def _add(cfg: Config, op: dict, res: ApplyResult) -> None:
    url = _url(op.get("url"))
    query = "" if url else _text(op.get("query"), "query", required=True)
    if url and cfg.find(url):
        res.messages.append(f"zaten listede: {url}")
        return
    if query and any(p.query.lower() == query.lower() for p in cfg.products):
        res.messages.append(f"zaten listede: {query}")
        return
    name = _text(op.get("name"), "name") or query
    pid = cfg.unique_id(slugify(name or query or url))
    mode = (op.get("mode") or "http").lower()
    product = Product(
        id=pid, url=url, query=query, name=name,
        target_price=_price(op.get("target"), "target"),
        mode=mode if mode in ("http", "browser") else "http",
        selector=_text(op.get("selector"), "selector") or None,
        sites=_sites(op.get("sites")),
        exclude=_words(op.get("exclude"), "exclude"),
        must_include=_words(op.get("must_include"), "must_include"),
        min_price=_price(op.get("min_price"), "min_price"),
        max_price=_price(op.get("max_price"), "max_price"),
        include_listings=_keys(op.get("include_listings"), "include_listings"),
        exclude_listings=_keys(op.get("exclude_listings"), "exclude_listings"),
        added_at=now_iso(),
    )
    cfg.products.append(product)
    res.changed = True
    res.added.append(pid)
    res.scan_ids.append(pid)
    res.messages.append(f"eklendi: {name or url} [{pid}]")


# Bu alanlar degisirse urun yeniden taranir (eslestirme / kaynak degisti)
_RESCAN_FIELDS = {"query", "url", "sites", "exclude", "must_include", "min_price",
                  "max_price", "mode", "selector", "enabled"}


def _update(cfg: Config, op: dict, res: ApplyResult) -> None:
    p = _product(cfg, op)
    changes = op.get("set") or {}
    if not isinstance(changes, dict):
        raise CommandError("'set' bir nesne olmali")
    rescan = False

    for key, value in changes.items():
        if key == "target":
            p.target_price = _price(value, "target")
        elif key == "name":
            p.name = _text(value, "name") or p.name
        elif key == "query":
            if not p.is_search:
                raise CommandError("adresle takip edilen urunun sorgusu degistirilemez")
            p.query = _text(value, "query", required=True)
        elif key == "url":
            if p.is_search:
                raise CommandError("arama urunune adres verilemez")
            p.url = _url(value) or p.url
        elif key == "sites":
            p.sites = _sites(value)
        elif key in ("exclude", "must_include"):
            setattr(p, key, _words(value, key))
        elif key in ("min_price", "max_price"):
            setattr(p, key, _price(value, key))
        elif key == "enabled":
            p.enabled = bool(value)
        elif key == "mode":
            p.mode = value if value in ("http", "browser") else "http"
        elif key == "selector":
            p.selector = _text(value, "selector") or None
        else:
            continue                                   # bilinmeyen alan: yok say
        if key in _RESCAN_FIELDS:
            rescan = True

    def _apply_keys(field_name: str, add_key: str, remove_key: str) -> bool:
        current = getattr(p, field_name)
        before = list(current)
        for k in _keys(op.get(add_key), add_key):
            if k not in current:
                current.append(k)
        for k in _keys(op.get(remove_key), remove_key):
            if k in current:
                current.remove(k)
        return current != before

    # "Bu urun degil" bir ilani dahil edilenlerden de cikarir; tersi de gecerli
    for k in _keys(op.get("reject"), "reject"):
        if k in p.include_listings:
            p.include_listings.remove(k)
    for k in _keys(op.get("include"), "include"):
        if k in p.exclude_listings:
            p.exclude_listings.remove(k)
    if _apply_keys("exclude_listings", "reject", "unreject"):
        rescan = True
    if _apply_keys("include_listings", "include", "uninclude"):
        rescan = True

    res.changed = True
    if rescan and p.enabled and p.id not in res.scan_ids:
        res.scan_ids.append(p.id)
    res.messages.append(f"guncellendi: {p.name or p.id}")


def _remove(cfg: Config, op: dict, res: ApplyResult) -> None:
    p = _product(cfg, op)
    cfg.products.remove(p)
    if p.id in res.scan_ids:
        res.scan_ids.remove(p.id)
    res.changed = True
    res.messages.append(f"silindi: {p.name or p.id}")


def _scan(cfg: Config, op: dict, res: ApplyResult) -> None:
    ids = _words(op.get("ids"), "ids")
    if not ids:
        res.scan_all = True
        return
    for pid in ids:
        if cfg.by_id(pid) and pid not in res.scan_ids:
            res.scan_ids.append(pid)


HANDLERS = {"add": _add, "update": _update, "remove": _remove, "scan": _scan}


def parse_command(text: str) -> list[dict]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CommandError(f"komut JSON degil: {exc}")
    if isinstance(data, dict) and "op" in data:
        data = {"ops": [data]}
    ops = data.get("ops") if isinstance(data, dict) else data
    if not isinstance(ops, list) or not ops:
        raise CommandError("komutta islem yok")
    if len(ops) > MAX_OPS:
        raise CommandError(f"tek seferde en fazla {MAX_OPS} islem")
    return ops


def apply(cfg: Config, ops: list[dict]) -> ApplyResult:
    """Islemleri sirayla uygular. Biri hataliysa digerleri yine uygulanir."""
    res = ApplyResult()
    for op in ops:
        if not isinstance(op, dict):
            res.messages.append("hata: islem nesne olmali")
            continue
        handler = HANDLERS.get(op.get("op"))
        if handler is None:
            res.messages.append(f"hata: bilinmeyen islem {op.get('op')!r}")
            continue
        try:
            handler(cfg, op, res)
        except CommandError as exc:
            res.messages.append(f"hata: {exc}")
    # Silinen urunler taranacaklar listesinde kalmasin
    res.scan_ids = [pid for pid in res.scan_ids if cfg.by_id(pid)]
    return res
