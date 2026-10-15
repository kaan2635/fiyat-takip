"""Telefondaki uygulamanin okudugu veri paketi (docs/data.json).

Uygulama artik HTML'i sunucuda cizilmis bir rapor degil; bu paketi okuyup
ekrani kendisi ciziyor. Boylece:

- Tarama bittiginde GitHub Pages yayinini (dakikalar) beklemeden, yeni paketi
  dogrudan depodan (GitHub API) okuyup ekrani aninda yenileyebiliyor.
- Hedef fiyat, elenen teklifler, gecmis grafigi gibi her sey ayni veriden
  geliyor; tek bir kaynak, tutarsizlik yok.

Paket bilerek "duz" tutuldu: hesaplanmis istatistikler (en dusuk, ortalama,
fiyat konumu) Python'da uretilir, uygulama yalnizca gosterir. Bu mantik
burada test edilebilir.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .config import Config, Product
from .search import DEFAULT_SITES, SOURCE_LABELS, listing_key
from .store import stats_for

PAYLOAD_VERSION = 2
MAX_POINTS = 500          # urun basina grafik noktasi siniri
DEAL_WINDOW_DAYS = 90     # "iyi fiyat mi" karsilastirmasi icin bakilan donem


def _ts(iso: str) -> int | None:
    try:
        return int(datetime.fromisoformat((iso or "").replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _f(value) -> float | None:
    return float(value) if value is not None else None


def compact_history(points: list[dict], limit: int = MAX_POINTS) -> list[list]:
    """Olcumleri [unix_saniye, fiyat] ciftlerine indirir.

    Cok uzun gecmislerde gun basina son olcum tutulur; en dusuk fiyatin
    gorulmesi onemli oldugu icin o nokta her zaman kalir.
    """
    pairs = []
    for p in points:
        ts = _ts(p.get("scanned_at", ""))
        if ts is None or p.get("price") is None:
            continue
        pairs.append([ts, round(float(p["price"]), 2)])
    if len(pairs) <= limit:
        return pairs

    lowest = min(pairs, key=lambda x: x[1])
    by_day: dict[str, list] = {}
    for pair in pairs:
        day = datetime.fromtimestamp(pair[0], timezone.utc).strftime("%Y-%m-%d")
        by_day[day] = pair                     # gunun son olcumu
    reduced = list(by_day.values())
    if lowest not in reduced:
        reduced.append(lowest)
    reduced.sort(key=lambda x: x[0])
    return reduced[-limit:]


def deal_rating(points: list[dict], now: datetime | None = None) -> dict | None:
    """Guncel fiyatin son donemdeki yerini soyler.

    Yalnizca gercekten dalgalanmis gecmiste karar verilir; fiyat hic
    oynamadiysa "iyi fiyat" demek yaniltici olur.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DEAL_WINDOW_DAYS)
    recent = []
    for p in points:
        ts = _ts(p.get("scanned_at", ""))
        if ts is not None and p.get("price") is not None and ts >= cutoff.timestamp():
            recent.append(Decimal(str(p["price"])))
    if len(recent) < 4:
        return None
    current, low, high = recent[-1], min(recent), max(recent)
    if high <= 0 or (high - low) / high < Decimal("0.01"):
        return None
    position = float((current - low) / (high - low))
    avg = sum(recent) / len(recent)
    if current <= low:
        level = "lowest"
    elif position <= 0.25:
        level = "good"
    elif position >= 0.75:
        level = "high"
    else:
        level = "normal"
    return {
        "level": level,
        "position": round(position, 3),
        "low": float(low), "high": float(high),
        "average": round(float(avg), 2),
        "vs_average_percent": round(float((current - avg) / avg * 100), 1),
        "window_days": DEAL_WINDOW_DAYS,
    }


_CRON = re.compile(r'cron:\s*"([^"]+)"')


def schedule_text(workflow: Path = Path(".github/workflows/scan.yml"), offset_hours: int = 3) -> str:
    """scan.yml icindeki cron satirini Turkiye saatiyle okunur hale getirir."""
    try:
        m = _CRON.search(workflow.read_text(encoding="utf-8"))
    except OSError:
        return ""
    if not m:
        return ""
    parts = m.group(1).split()
    if len(parts) != 5:
        return m.group(1)
    minute, hour, dom, month, dow = parts
    if not minute.isdigit() or dom != "*" or month != "*":
        return f"cron: {m.group(1)} (UTC)"

    def local(h: int) -> str:
        return f"{(h + offset_hours) % 24:02d}:{int(minute):02d}"

    if hour.startswith("*/") and hour[2:].isdigit():
        text = f"{int(hour[2:])} saatte bir"
    elif hour == "*":
        text = "Saat başı"
    elif all(h.isdigit() for h in hour.split(",")):
        text = "Her gün " + ", ".join(local(int(h)) for h in hour.split(","))
    else:
        return f"cron: {m.group(1)} (UTC)"
    if dow == "1-5":
        text += " (hafta içi)"
    elif dow != "*":
        text += f" (gün: {dow})"
    return text


def _product_entry(p: Product, latest: dict, points: list[dict]) -> dict:
    st = stats_for(points)
    status = latest.get("status") or ("disabled" if not p.enabled else "pending")
    offers = [dict(o) for o in (latest.get("offers") or [])]
    for o in offers:                       # eski dosyalarda ilan anahtari yoktu
        if not o.get("key") and o.get("url"):
            o["key"] = listing_key(o["url"])
    best_title = offers[0]["title"] if offers else ""
    image = next((o.get("image") for o in offers if o.get("image")), "")
    lowest_at = None
    if st.lowest is not None:
        for row in points:
            if row.get("price") == st.lowest:
                lowest_at = _ts(row.get("scanned_at", ""))
                break

    # Taramada okunamadiysa son bilinen fiyat gosterilir, ama "eski" diye isaretlenir
    live = latest.get("price")
    price = live if live is not None else _f(st.current)

    best_url = latest.get("url") or p.url
    return {
        "id": p.id,
        "name": p.name or latest.get("name") or p.query or p.id,
        "kind": "search" if p.is_search else "url",
        "query": p.query,
        "url": p.url,
        "enabled": p.enabled,
        "mode": p.mode,
        "target": _f(p.target_price),
        "rules": {
            "sites": p.sites or [],
            "exclude": p.exclude,
            "must_include": p.must_include,
            "min_price": _f(p.min_price),
            "max_price": _f(p.max_price),
            "include_listings": p.include_listings,
            "exclude_listings": p.exclude_listings,
        },
        "added_at": p.added_at,
        "status": status,
        "note": latest.get("note", ""),
        "scanned_at": _ts(latest.get("scanned_at", "")) or (_ts(st.last_seen) if st.last_seen else None),
        "price": price,
        "price_is_stale": live is None and price is not None,
        "currency": latest.get("currency") or "TRY",
        "in_stock": latest.get("in_stock"),
        "best": {
            "site": latest.get("site") if latest.get("site") not in (None, "arama") else "",
            "url": best_url,
            "title": best_title,
            "key": listing_key(best_url) if best_url else "",
        },
        "image": image,
        "stats": {
            "current": _f(st.current),
            "previous": _f(st.previous),
            "lowest": _f(st.lowest),
            "highest": _f(st.highest),
            "lowest_at": lowest_at,
            "change_percent": round(st.change_percent, 2) if st.change_percent is not None else None,
            "points": st.points,
            "first_seen": _ts(st.first_seen) if st.first_seen else None,
            "is_lowest_ever": st.is_lowest_ever,
        },
        "deal": deal_rating(points),
        "history": compact_history(points),
        "offers": offers[:30],
        "rejected": latest.get("rejected") or [],
        "warnings": latest.get("warnings") or [],
        "per_site": latest.get("per_site") or {},
    }


def build_payload(cfg: Config, latest: dict[str, dict], series: dict[str, list[dict]],
                  generated_at: str, repo: str | None = None) -> dict:
    """Uygulamanin tum ekranlarini besleyen paketi uretir."""
    products = [_product_entry(p, latest.get(p.id, {}), series.get(p.id, [])) for p in cfg.products]

    # Iki urun ayni ilani takip ediyorsa (ornegin ayni urun iki farkli
    # yazimla eklenmis) uygulama bunu gostersin
    seen: dict[str, str] = {}
    for entry in products:
        key = entry["best"]["key"]
        if not key or entry["status"] not in ("ok", "suspicious"):
            continue
        if key in seen:
            entry["duplicate_of"] = seen[key]
        else:
            seen[key] = entry["id"]

    default_sites = list(cfg.get("default_sites") or []) or list(DEFAULT_SITES)
    return {
        "version": PAYLOAD_VERSION,
        "generated_at": _ts(generated_at),
        "repo": repo if repo is not None else os.environ.get("GITHUB_REPOSITORY", ""),
        "schedule": schedule_text(),
        "settings": {
            "drop_alert_percent": cfg.get("drop_alert_percent"),
            "default_sites": default_sites,
        },
        "sources": [{"id": k, "label": v, "default": k in default_sites}
                    for k, v in SOURCE_LABELS.items()],
        "products": products,
    }
