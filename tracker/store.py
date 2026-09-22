"""Fiyat gecmisinin saklanmasi.

Gecmis, SQLite yerine append-only bir CSV'de tutulur: git ile takip
edilebilir, diff'i okunur, Excel'de acilir ve GitHub Actions'in her tarama
sonrasi commit atmasi sorunsuz olur.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

HISTORY_PATH = Path("data/history.csv")
LATEST_PATH = Path("data/latest.json")

FIELDS = [
    "scanned_at", "product_id", "name", "site", "url",
    "price", "currency", "in_stock", "status", "method", "note",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ScanRow:
    scanned_at: str
    product_id: str
    name: str
    site: str
    url: str
    price: Decimal | None
    currency: str
    in_stock: bool | None
    status: str          # ok | blocked | parse_failed | error | disabled
    method: str
    note: str = ""

    def as_csv(self) -> dict:
        d = asdict(self)
        d["price"] = "" if self.price is None else f"{Decimal(str(self.price)):.2f}"
        d["in_stock"] = "" if self.in_stock is None else ("1" if self.in_stock else "0")
        return d

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.price is not None


@dataclass
class PriceStats:
    """Bir urunun gecmisinden cikarilan ozet."""
    current: Decimal | None = None
    previous: Decimal | None = None
    lowest: Decimal | None = None
    highest: Decimal | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    points: int = 0

    @property
    def change(self) -> Decimal | None:
        if self.current is None or self.previous is None:
            return None
        return self.current - self.previous

    @property
    def change_percent(self) -> float | None:
        if self.change is None or not self.previous:
            return None
        return float(self.change / self.previous * 100)

    @property
    def is_lowest_ever(self) -> bool:
        """Simdiye kadarki en dusuk fiyat mi?

        Fiyat hic oynamadiysa "rekor" demek yaniltici olur; bu yuzden gecmiste
        gercek bir dalgalanma (en dusuk < en yuksek) ve en az 3 olcum aranir.
        """
        return (
            self.current is not None
            and self.lowest is not None
            and self.highest is not None
            and self.points > 2
            and self.highest > self.lowest
            and self.current <= self.lowest
        )


def _to_decimal(value: str) -> Decimal | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


def _to_bool(value: str) -> bool | None:
    value = (value or "").strip()
    if value in ("1", "True", "true"):
        return True
    if value in ("0", "False", "false"):
        return False
    return None


def append_rows(rows: list[ScanRow], path: Path = HISTORY_PATH) -> None:
    """Tarama sonuclarini gecmis dosyasinin sonuna ekler."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv())


def load_history(path: Path = HISTORY_PATH) -> list[dict]:
    """Gecmisi ham satirlar halinde okur (bozuk satirlari atlar)."""
    if not Path(path).exists():
        return []
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            price = _to_decimal(raw.get("price", ""))
            out.append(
                {
                    **raw,
                    "price": price,
                    "in_stock": _to_bool(raw.get("in_stock", "")),
                }
            )
    return out


def series_by_product(path: Path = HISTORY_PATH) -> dict[str, list[dict]]:
    """Urun kimligine gore, zamana gore sirali basarili olcumler."""
    series: dict[str, list[dict]] = {}
    for row in load_history(path):
        if row.get("status") != "ok" or row.get("price") is None:
            continue
        series.setdefault(row["product_id"], []).append(row)
    for rows in series.values():
        rows.sort(key=lambda r: r.get("scanned_at") or "")
    return series


def stats_for(rows: list[dict]) -> PriceStats:
    """Bir urunun olcum listesinden ozet cikarir."""
    prices = [r["price"] for r in rows if r.get("price") is not None]
    if not prices:
        return PriceStats()
    return PriceStats(
        current=prices[-1],
        previous=prices[-2] if len(prices) > 1 else None,
        lowest=min(prices),
        highest=max(prices),
        first_seen=rows[0].get("scanned_at"),
        last_seen=rows[-1].get("scanned_at"),
        points=len(prices),
    )


def all_stats(path: Path = HISTORY_PATH) -> dict[str, PriceStats]:
    return {pid: stats_for(rows) for pid, rows in series_by_product(path).items()}


def write_latest(rows: list[ScanRow], stats: dict[str, PriceStats],
                 path: Path = LATEST_PATH) -> None:
    """Son durumun makine okunur ozetini yazar (baska araclar icin)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": now_iso(), "products": []}
    for row in rows:
        st = stats.get(row.product_id, PriceStats())
        payload["products"].append(
            {
                "id": row.product_id,
                "name": row.name,
                "site": row.site,
                "url": row.url,
                "status": row.status,
                "price": float(row.price) if row.price is not None else None,
                "currency": row.currency,
                "in_stock": row.in_stock,
                "lowest": float(st.lowest) if st.lowest is not None else None,
                "highest": float(st.highest) if st.highest is not None else None,
                "change_percent": round(st.change_percent, 2) if st.change_percent is not None else None,
                "points": st.points,
                "note": row.note,
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
