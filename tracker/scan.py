"""Tarama motoru: urunleri gez, fiyati cek, degisimleri tespit et."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from . import browser as browser_mod
from .config import Config, Product
from .fetch import Fetcher
from .parsers import parse_page
from .store import PriceStats, ScanRow, now_iso, series_by_product, stats_for

log = logging.getLogger("tracker.scan")

# Yeni fiyat, gecmis medyanin bu kati disindaysa olcum suphelidir.
# Amac: site duzeni degisip yanlis bir sayi okundugunda (orn. taksit tutari
# veya kargo ucreti) bunu gercek fiyat sanip yanlis alarm uretmemek.
SANITY_LOW = Decimal("0.2")
SANITY_HIGH = Decimal("5")


@dataclass
class Alert:
    kind: str           # target | drop | stock | lowest
    product: Product
    row: ScanRow
    stats: PriceStats
    message: str


@dataclass
class ScanReport:
    rows: list[ScanRow] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    stats: dict[str, PriceStats] = field(default_factory=dict)

    @property
    def ok_rows(self) -> list[ScanRow]:
        return [r for r in self.rows if r.ok]

    @property
    def failed_rows(self) -> list[ScanRow]:
        return [r for r in self.rows if not r.ok]


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def sanity_check(price: Decimal, history: list[dict]) -> str | None:
    """Olcumun gecmisle tutarli olup olmadigini soyler; sorun varsa aciklama doner."""
    past = [r["price"] for r in history if r.get("price") is not None]
    if len(past) < 3:
        return None
    med = _median(past)
    if not med:
        return None
    if price < med * SANITY_LOW or price > med * SANITY_HIGH:
        return f"supheli fiyat: {price} (gecmis medyan {med})"
    return None


def scan_product(product: Product, fetcher: Fetcher, cfg: Config,
                 history: list[dict], browser=None) -> tuple[ScanRow, object]:
    """Tek bir urunu tarar. Gerekirse tarayici yedegine yukselir.

    Donen ikinci deger, acilmis olabilecek tarayici ornegidir; cagiran taraf
    onu sonraki urunlerde yeniden kullanir ve en sonda kapatir.
    """
    # Denenecek indirici sirasi. Urun "browser" dediyse tarayici once gelir ama
    # tarayici acilmazsa (kurulu degil, cokerse) HTTP yine de denenir; tersi de
    # gecerli. Boylece tek bir yolun tikanmasi urunu tamamen kor birakmaz.
    http_first = product.mode != "browser"
    order = ["http", "browser"] if http_first else ["browser", "http"]
    if http_first and not cfg.get("browser_fallback"):
        order = ["http"]

    result = None
    for backend in order:
        if backend == "browser":
            if not browser_mod.available():
                continue
            if browser is None:
                browser = browser_mod.BrowserFetcher(timeout=cfg.get("timeout_seconds") * 1000)
            log.info("%s: tarayici ile deneniyor", product.id)
            attempt = browser.get(product.url)
        else:
            attempt = fetcher.get(product.url)

        # Ilk basarili sonuc kazanir; hepsi basarisizsa ilk hatayi raporlariz
        if attempt.ok:
            result = attempt
            break
        if result is None:
            result = attempt

    base = dict(
        scanned_at=now_iso(), product_id=product.id, name=product.name or product.id,
        site=product.site, url=product.url, currency="TRY",
    )

    if result is None or not result.ok:
        reason = (result.block_reason or result.error or "bilinmeyen hata") if result else "istek yapilamadi"
        status = "blocked" if (result and result.blocked) else "error"
        row = ScanRow(**base, price=None, in_stock=None, status=status,
                      method=result.backend if result else "-", note=reason[:200])
        return row, browser

    parsed = parse_page(result.html, result.final_url or product.url, selectors=
                        [product.selector] if product.selector else None)

    if not parsed.ok:
        row = ScanRow(**base, price=None, in_stock=parsed.in_stock, status="parse_failed",
                      method=f"{result.backend}/{parsed.method}",
                      note="fiyat bulunamadi - siteye ozel secici gerekebilir")
        return row, browser

    note = ""
    problem = sanity_check(parsed.price, history)
    if problem:
        note = problem
        log.warning("%s: %s", product.id, problem)

    base["name"] = product.name or parsed.title or product.id
    base["currency"] = parsed.currency or "TRY"
    row = ScanRow(
        **base, price=parsed.price, in_stock=parsed.in_stock,
        status="suspicious" if problem else "ok",
        method=f"{result.backend}/{parsed.method}",
        note=note or (result.profile or ""),
    )
    return row, browser


def build_alerts(product: Product, row: ScanRow, stats: PriceStats,
                 previous_in_stock: bool | None, cfg: Config) -> list[Alert]:
    """Bir olcumden dogan bildirimleri uretir."""
    alerts: list[Alert] = []
    if not row.ok:
        return alerts

    threshold = float(cfg.get("drop_alert_percent") or 0)
    pct = stats.change_percent

    # Hedef fiyat: yalnizca yeni ulasildiginda haber ver, her taramada degil
    if cfg.get("notify_on_target") and product.target_price is not None:
        if row.price <= product.target_price and (
            stats.previous is None or stats.previous > product.target_price
        ):
            alerts.append(Alert("target", product, row, stats,
                                f"hedef fiyatin altina indi ({product.target_price})"))

    if threshold and pct is not None and pct <= -threshold:
        alerts.append(Alert("drop", product, row, stats, f"%{abs(pct):.1f} ucuzladi"))

    if stats.is_lowest_ever and stats.points > 2:
        alerts.append(Alert("lowest", product, row, stats, "gordugu en dusuk fiyat"))

    if cfg.get("notify_on_back_in_stock") and previous_in_stock is False and row.in_stock is True:
        alerts.append(Alert("stock", product, row, stats, "tekrar stokta"))

    return alerts


def run_scan(cfg: Config, only: list[str] | None = None,
             history_path=None, progress=None) -> ScanReport:
    """Tum aktif urunleri tarar ve raporu doner."""
    from .store import HISTORY_PATH

    history_path = history_path or HISTORY_PATH
    series = series_by_product(history_path)

    products = cfg.active
    if only:
        wanted = {o.lower() for o in only}
        products = [p for p in products
                    if p.id.lower() in wanted or any(w in p.name.lower() for w in wanted)]

    fetcher = Fetcher(timeout=cfg.get("timeout_seconds"), min_delay=cfg.get("min_delay_seconds"))
    browser = None
    report = ScanReport()

    try:
        for index, product in enumerate(products, 1):
            if progress:
                progress(index, len(products), product)

            past = series.get(product.id, [])
            previous_in_stock = past[-1].get("in_stock") if past else None

            row, browser = scan_product(product, fetcher, cfg, past, browser)
            report.rows.append(row)

            # Istatistikleri bu olcumu de dahil ederek hesapla
            combined = past + ([{"price": row.price, "scanned_at": row.scanned_at,
                                 "in_stock": row.in_stock}] if row.ok else [])
            stats = stats_for(combined)
            report.stats[product.id] = stats
            report.alerts.extend(build_alerts(product, row, stats, previous_in_stock, cfg))

            # Basariyla cozulen basligi kalici hale getir (bir defaya mahsus)
            if row.ok and not product.name and row.name:
                product.name = row.name
    finally:
        fetcher.close()
        if browser is not None:
            browser.close()

    return report
