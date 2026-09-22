"""Komut satiri arayuzu: urun ekle/cikar/listele, tara, rapor uret."""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__
from .config import Config, Product, slugify
from .fetch import Fetcher
from .money import format_price, parse_price
from .parsers import parse_page, site_of
from .report import REPORT_PATH, write_report
from .scan import rules_for, run_scan
from .search import DEFAULT_SITES, SOURCES, search
from .store import (HISTORY_PATH, LATEST_PATH, all_stats, append_rows,
                    now_iso, series_by_product, write_latest)

# Terminalde renk; boru hattina yazarken kapatilir
class C:
    if sys.stdout.isatty():
        RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"
        GREEN, RED, YELLOW, BLUE = "\033[32m", "\033[31m", "\033[33m", "\033[34m"
    else:
        RESET = BOLD = DIM = GREEN = RED = YELLOW = BLUE = ""


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _decimal(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        raise SystemExit(f"Gecersiz fiyat: {value!r}")


# --------------------------------------------------------------------- komutlar
def _looks_like_url(text: str) -> bool:
    return text.strip().lower().startswith(("http://", "https://", "www."))


def _preview_offers(product, cfg) -> list:
    """Arama urununu ekler/kontrol ederken bulunan teklifleri gosterir."""
    fetcher = Fetcher(min_delay=cfg.get("min_delay_seconds"))
    try:
        offers, notes = search(product.query, fetcher, product.sites, rules_for(product))
    finally:
        fetcher.close()
    for n in notes:
        print(f"  {C.DIM}{n}{C.RESET}")
    for o in offers[:6]:
        print(f"    {format_price(o.price):>14}  {C.DIM}{o.site:16}{C.RESET} {o.title[:52]}")
    return offers


def cmd_add(args, cfg: Config) -> int:
    target = args.url.strip()

    # URL degilse urun adi olarak alinir ve pazaryerlerinde aranir
    if not _looks_like_url(target):
        if cfg.find(target):
            print(f"{C.YELLOW}Bu urun zaten listede.{C.RESET}")
            return 1
        product = Product(
            id=args.id or cfg.unique_id(slugify(args.name or target)),
            query=target, name=args.name or target,
            target_price=_decimal(args.target),
            sites=args.sites or None,
            exclude=args.exclude or [],
            must_include=args.must_include or [],
            min_price=_decimal(args.min_price),
            max_price=_decimal(args.max_price),
        )
        print(f"Aranan urun: {C.BOLD}{target}{C.RESET}")
        offers = _preview_offers(product, cfg)
        if not offers:
            print(f"  {C.YELLOW}!{C.RESET} Hicbir teklif eslesmedi. Urun yine de eklenecek; "
                  f"sorguyu sadelestirmeyi ya da --exclude kurallarini gozden gecirmeyi dene.")
        else:
            print(f"  {C.GREEN}✓{C.RESET} en ucuz: "
                  f"{C.BOLD}{format_price(offers[0].price)}{C.RESET} @ {offers[0].site}")
        cfg.products.append(product)
        cfg.save()
        print(f"{C.GREEN}Eklendi:{C.RESET} {C.BOLD}{product.name}{C.RESET} "
              f"{C.DIM}[{product.id}] · arama{C.RESET}")
        return 0

    url = target
    if cfg.find(url):
        print(f"{C.YELLOW}Bu URL zaten listede.{C.RESET}")
        return 1

    name = args.name or ""
    print(f"Urun kontrol ediliyor: {url}")
    fetcher = Fetcher(min_delay=0)
    try:
        result = fetcher.get(url)
        if result.ok:
            parsed = parse_page(result.html, result.final_url or url,
                                selectors=[args.selector] if args.selector else None)
            name = name or (parsed.title or "")
            if parsed.ok:
                print(f"  {C.GREEN}✓{C.RESET} fiyat okundu: "
                      f"{C.BOLD}{format_price(parsed.price, parsed.currency)}{C.RESET} "
                      f"{C.DIM}({parsed.method}){C.RESET}")
            else:
                print(f"  {C.YELLOW}!{C.RESET} sayfa acildi ama fiyat bulunamadi — "
                      f"'--selector' ile CSS secici verebilir veya '--mode browser' deneyebilirsin")
        else:
            print(f"  {C.YELLOW}!{C.RESET} sayfa acilamadi: "
                  f"{result.block_reason or result.error} — urun yine de eklenecek")
    finally:
        fetcher.close()

    pid = args.id or cfg.unique_id(slugify(name or url))
    cfg.products.append(Product(
        id=pid, url=url, name=name, target_price=_decimal(args.target),
        mode=args.mode, selector=args.selector,
    ))
    cfg.save()
    print(f"{C.GREEN}Eklendi:{C.RESET} {C.BOLD}{name or pid}{C.RESET} "
          f"{C.DIM}[{pid}] · {site_of(url)}{C.RESET}")
    return 0


def cmd_remove(args, cfg: Config) -> int:
    product = cfg.find(args.product)
    if not product:
        print(f"{C.RED}Bulunamadi:{C.RESET} {args.product}")
        return 1
    cfg.products.remove(product)
    cfg.save()
    print(f"{C.GREEN}Silindi:{C.RESET} {product.name or product.id}")
    return 0


def cmd_list(args, cfg: Config) -> int:
    if not cfg.products:
        print("Liste bos. Ekle:  python -m tracker add <urun-url>")
        return 0

    stats = all_stats()
    print(f"{C.BOLD}{'KIMLIK':<26} {'SITE':<17} {'GUNCEL':>14} {'DEGISIM':>9}  URUN{C.RESET}")
    for p in cfg.products:
        st = stats.get(p.id)
        price = format_price(st.current, "TRY") if st and st.current is not None else "—"
        pct = st.change_percent if st else None
        if pct is None:
            change, color = "—", C.DIM
        elif pct < 0:
            change, color = f"{pct:+.1f}%", C.GREEN
        elif pct > 0:
            change, color = f"{pct:+.1f}%", C.RED
        else:
            change, color = "0.0%", C.DIM
        flag = "" if p.enabled else f" {C.DIM}(kapali){C.RESET}"
        target = f" {C.DIM}hedef {format_price(p.target_price)}{C.RESET}" if p.target_price else ""
        print(f"{p.id:<26} {p.site:<17} {price:>14} {color}{change:>9}{C.RESET}  "
              f"{(p.name or '')[:40]}{target}{flag}")
    print(f"\n{len(cfg.active)} aktif / {len(cfg.products)} toplam urun")
    return 0


def cmd_scan(args, cfg: Config) -> int:
    if not cfg.active:
        print("Takip edilen urun yok. Once ekle:  python -m tracker add <url>")
        return 1

    def progress(i, total, product):
        print(f"{C.DIM}[{i}/{total}]{C.RESET} {product.name or product.id} "
              f"{C.DIM}({product.site}){C.RESET}", flush=True)

    report = run_scan(cfg, only=args.only, progress=None if args.quiet else progress)

    print()
    print(f"{C.BOLD}{'URUN':<42} {'FIYAT':>14} {'DEGISIM':>9}  DURUM{C.RESET}")
    for row in report.rows:
        st = report.stats.get(row.product_id)
        pct = st.change_percent if st else None
        if not row.ok:
            print(f"{(row.name or row.product_id)[:42]:<42} {'—':>14} {'—':>9}  "
                  f"{C.RED}{row.status}{C.RESET} {C.DIM}{row.note[:44]}{C.RESET}")
            continue
        if pct is None:
            change, color = "—", C.DIM
        elif pct < -0.01:
            change, color = f"{pct:+.1f}%", C.GREEN
        elif pct > 0.01:
            change, color = f"{pct:+.1f}%", C.RED
        else:
            change, color = "0.0%", C.DIM
        marks = []
        if st and st.is_lowest_ever:
            marks.append(f"{C.GREEN}rekor dusuk{C.RESET}")
        if row.in_stock is False:
            marks.append(f"{C.YELLOW}stokta yok{C.RESET}")
        if row.status == "suspicious":
            marks.append(f"{C.YELLOW}supheli{C.RESET}")
        print(f"{(row.name or row.product_id)[:42]:<42} "
              f"{format_price(row.price, row.currency):>14} "
              f"{color}{change:>9}{C.RESET}  {' '.join(marks)}")

    ok, total = len(report.ok_rows), len(report.rows)
    print(f"\n{ok}/{total} urun okundu.")

    if args.dry_run:
        print(f"{C.YELLOW}--dry-run: gecmise yazilmadi, bildirim gonderilmedi.{C.RESET}")
        return 0

    append_rows(report.rows)
    stats = all_stats()
    write_latest(report.rows, stats, offers=report.offers)
    cfg.save()   # cozulen urun adlarini kalici hale getirir

    if not args.no_report:
        path = write_report(report.rows, stats, series_by_product(),
                            {p.id: p for p in cfg.products}, now_iso(),
                            path=Path(args.report_path), offers=report.offers)
        print(f"Rapor: {C.BLUE}{path}{C.RESET}")

    if not args.no_notify:
        from .notify import configured, notify
        if configured():
            sent = notify(report, report_url=args.report_url, summary=not args.no_summary)
            print(f"Telegram: {sent} mesaj gonderildi "
                  f"({len(report.alerts)} uyari tespit edildi)")
        elif report.alerts:
            print(f"{C.DIM}Telegram yapilandirilmamis; {len(report.alerts)} uyari gonderilmedi.{C.RESET}")

    for alert in report.alerts:
        print(f"  {C.GREEN}●{C.RESET} {alert.product.name or alert.product.id}: {alert.message}")
    return 0


def cmd_report(args, cfg: Config) -> int:
    series = series_by_product()
    if not series:
        print("Gecmis bos. Once tarama yap:  python -m tracker scan")
        return 1
    stats = all_stats()
    # Son olcumleri gecmisten geri kur
    from .store import ScanRow, load_history
    # Basarisiz urunler series_by_product() tarafindan elenir; onlarin son
    # durumunu ham gecmisten alip raporda "okunamayan" olarak gosteririz.
    last_any: dict[str, dict] = {}
    for raw in load_history():
        last_any[raw["product_id"]] = raw

    rows = []
    for p in cfg.products:
        points = series.get(p.id)
        last = points[-1] if points else last_any.get(p.id)
        if not last:
            continue
        ok = bool(points) and last_any.get(p.id, last).get("status") == "ok"
        latest = last_any.get(p.id, last)
        rows.append(ScanRow(
            scanned_at=latest.get("scanned_at", ""), product_id=p.id,
            name=p.name or latest.get("name", p.id),
            site=p.site, url=p.url,
            price=last["price"] if ok else None,
            currency=latest.get("currency") or "TRY",
            in_stock=latest.get("in_stock"),
            status="ok" if ok else (latest.get("status") or "error"),
            method=latest.get("method", ""), note=latest.get("note", ""),
        ))
    path = write_report(rows, stats, series, {p.id: p for p in cfg.products},
                        now_iso(), path=Path(args.report_path))
    print(f"Rapor yazildi: {C.BLUE}{path}{C.RESET}")
    return 0


def cmd_search(args, cfg: Config) -> int:
    """Bir urun adini kaydetmeden arar - sorgu ayarlamak icin."""
    product = Product(
        id="onizleme", query=" ".join(args.query),
        sites=args.sites or None,
        exclude=args.exclude or [],
        must_include=args.must_include or [],
        min_price=_decimal(args.min_price),
        max_price=_decimal(args.max_price),
    )
    print(f"Aranan: {C.BOLD}{product.query}{C.RESET}"
          f"  {C.DIM}({', '.join(product.sites or DEFAULT_SITES)}){C.RESET}\n")
    offers = _preview_offers(product, cfg)
    if not offers:
        print(f"\n{C.RED}Eslesen teklif yok.{C.RESET} Sorguyu kisaltmayi dene "
              f"(marka + model genelde yeterli).")
        return 1
    print(f"\n{C.GREEN}En ucuz:{C.RESET} {C.BOLD}{format_price(offers[0].price)}{C.RESET} "
          f"@ {offers[0].site}\n  {offers[0].url}")
    return 0


def cmd_test(args, cfg: Config) -> int:
    """Bir URL'yi kaydetmeden dener — yeni site eklerken hata ayiklamak icin."""
    url = args.url.strip()
    print(f"Deneniyor: {url}\n")

    if args.browser:
        from . import browser as bmod
        if not bmod.available():
            print(f"{C.RED}Playwright kurulu degil.{C.RESET}  pip install playwright")
            return 1
        b = bmod.BrowserFetcher()
        try:
            result = b.get(url)
        finally:
            b.close()
    else:
        fetcher = Fetcher(min_delay=0)
        try:
            result = fetcher.get(url)
        finally:
            fetcher.close()

    print(f"  arka uc : {result.backend}")
    print(f"  profil  : {result.profile}")
    print(f"  denemeler: {', '.join(result.attempts) or '-'}")
    print(f"  HTTP    : {result.status}  ({len(result.html)} bayt)")
    print(f"  son URL : {result.final_url}")
    if not result.ok:
        print(f"  {C.RED}engel/hata{C.RESET}: {result.block_reason or result.error}")
        if not args.browser:
            print(f"\n  {C.DIM}Oneri: --browser ile gercek tarayici uzerinden dene.{C.RESET}")
        return 1

    parsed = parse_page(result.html, result.final_url or url,
                        selectors=[args.selector] if args.selector else None)
    print(f"  baslik  : {(parsed.title or '-')[:70]}")
    print(f"  yontem  : {parsed.method}")
    print(f"  ham     : {parsed.raw!r}")
    print(f"  stok    : {parsed.in_stock}")
    if parsed.ok:
        print(f"\n  {C.GREEN}✓ FIYAT: {C.BOLD}{format_price(parsed.price, parsed.currency)}{C.RESET}")
        return 0
    print(f"\n  {C.RED}✕ Fiyat bulunamadi.{C.RESET} --selector ile CSS secici dene.")
    return 1


# ------------------------------------------------------------------------ giris
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tracker", description="Turk pazaryerleri icin fiyat takip sistemi")
    p.add_argument("--config", default="products.yaml", help="urun listesi dosyasi")
    p.add_argument("-v", "--verbose", action="store_true", help="ayrintili gunluk")
    p.add_argument("--version", action="version", version=f"fiyat-takip {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_search_flags(parser):
        parser.add_argument("--sites", nargs="*", choices=sorted(SOURCES),
                            help=f"aranacak kaynaklar (varsayilan: {', '.join(DEFAULT_SITES)})")
        parser.add_argument("--exclude", nargs="*", help="basliginda bunlar gecen sonuclari ele")
        parser.add_argument("--must-include", nargs="*", dest="must_include",
                            help="basliginda mutlaka gecmesi gereken kelimeler")
        parser.add_argument("--min-price", dest="min_price", help="bu fiyatin altindakileri ele")
        parser.add_argument("--max-price", dest="max_price", help="bu fiyatin ustundekileri ele")

    a = sub.add_parser("add", help="takibe urun ekle (URL ya da urun adi)")
    a.add_argument("url", metavar="URL_VEYA_URUN_ADI")
    a.add_argument("--name", help="urun adi (bos birakilirsa sayfadan okunur)")
    a.add_argument("--target", help="hedef fiyat; altina inince bildirim gelir")
    a.add_argument("--id", help="ozel kimlik")
    a.add_argument("--mode", choices=["http", "browser"], default="http")
    a.add_argument("--selector", help="ozel CSS fiyat secici")
    add_search_flags(a)
    a.set_defaults(func=cmd_add)

    sr = sub.add_parser("search", help="urun adini kaydetmeden ara (sorgu denemek icin)")
    sr.add_argument("query", nargs="+", help="aranacak urun adi")
    add_search_flags(sr)
    sr.set_defaults(func=cmd_search)

    r = sub.add_parser("remove", help="urunu listeden cikar")
    r.add_argument("product", help="kimlik, URL veya ad parcasi")
    r.set_defaults(func=cmd_remove)

    l = sub.add_parser("list", help="takip edilen urunleri goster")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("scan", help="fiyatlari tara")
    s.add_argument("--only", nargs="*", help="yalnizca bu urunleri tara")
    s.add_argument("--dry-run", action="store_true", help="kaydetme, sadece goster")
    s.add_argument("--no-notify", action="store_true", help="Telegram gonderme")
    s.add_argument("--no-summary", action="store_true", help="ozet mesaji gonderme, sadece uyarilar")
    s.add_argument("--no-report", action="store_true", help="HTML rapor uretme")
    s.add_argument("--report-path", default=str(REPORT_PATH))
    s.add_argument("--report-url", help="ozet mesajina eklenecek rapor adresi")
    s.add_argument("-q", "--quiet", action="store_true", help="ilerleme satirlarini gizle")
    s.set_defaults(func=cmd_scan)

    rp = sub.add_parser("report", help="gecmisten HTML raporu yeniden uret")
    rp.add_argument("--report-path", default=str(REPORT_PATH))
    rp.set_defaults(func=cmd_report)

    t = sub.add_parser("test", help="bir URL'yi kaydetmeden dene")
    t.add_argument("url")
    t.add_argument("--selector", help="denenecek CSS secici")
    t.add_argument("--browser", action="store_true", help="gercek tarayici kullan")
    t.set_defaults(func=cmd_test)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    cfg = Config.load(args.config)
    try:
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print("\nIptal edildi.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
