"""Komut satiri arayuzu: urun ekle/cikar/listele, tara, rapor uret."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import __version__
from .config import Config, Product, slugify
from .fetch import Fetcher
from .money import format_price, parse_price
from .parsers import parse_page, site_of
from .report import REPORT_PATH, write_app
from .scan import rules_for, run_scan, sites_for
from .search import DEFAULT_SITES, SOURCES, MatchRules, search
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
        result = search(product.query, fetcher, sites_for(product, cfg), rules_for(product))
    finally:
        fetcher.close()
    for n in result.notes:
        print(f"  {C.DIM}{n}{C.RESET}")
    for w in result.warnings:
        print(f"  {C.YELLOW}! {w}{C.RESET}")
    for o in result.offers[:8]:
        print(f"    {format_price(o.price):>14}  {C.DIM}{o.site:18}{C.RESET} {o.title[:52]}")
    near = result.near
    if near:
        print(f"  {C.DIM}yakin ama elenen {len(near)} sonuc, ornegin:{C.RESET}")
        for r in sorted(near, key=lambda r: r.offer.price)[:3]:
            print(f"    {format_price(r.offer.price):>14}  {C.DIM}{r.offer.site:18} {r.reason[:50]}{C.RESET}")
    return result.offers


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
            added_at=now_iso(),
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
        mode=args.mode, selector=args.selector, added_at=now_iso(),
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
    write_latest(report.rows, stats, searches=report.searches,
                 keep_ids=[p.id for p in cfg.products])
    cfg.save()   # cozulen urun adlarini kalici hale getirir

    if not args.no_report:
        path = write_app(cfg, path=Path(args.report_path))
        print(f"Uygulama: {C.BLUE}{path}{C.RESET}")

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
    """Gecmisten ve son durumdan uygulamayi (docs/) yeniden uretir."""
    # Silinmis urunler son durum dosyasindan da cikarilir
    from .store import load_latest
    latest = load_latest()
    if latest and set(latest) - {p.id for p in cfg.products}:
        write_latest([], {}, keep_ids=[p.id for p in cfg.products])
    path = write_app(cfg, path=Path(args.report_path))
    print(f"Uygulama yazildi: {C.BLUE}{path}{C.RESET}")
    return 0


def cmd_apply(args, cfg: Config) -> int:
    """Uygulamadan gelen JSON komutunu uygular (is akisi kullanir)."""
    from .commands import CommandError, apply, parse_command

    text = args.json
    if text == "-":
        text = sys.stdin.read()
    try:
        ops = parse_command(text)
    except CommandError as exc:
        print(f"{C.RED}Komut gecersiz:{C.RESET} {exc}")
        _github_output(changed="false", scan_ids="", scan_all="false", need_scan="false")
        return 2
    result = apply(cfg, ops)
    if result.changed:
        cfg.save()
    for msg in result.messages:
        color = C.RED if msg.startswith("hata") else C.GREEN
        print(f"  {color}{msg}{C.RESET}")
    need_scan = bool(result.scan_ids) or result.scan_all
    _github_output(
        changed="true" if result.changed else "false",
        scan_ids=" ".join(result.scan_ids),
        scan_all="true" if result.scan_all else "false",
        need_scan="true" if need_scan else "false",
    )
    if args.result:
        Path(args.result).write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
    errors = [m for m in result.messages if m.startswith("hata")]
    return 1 if errors and not result.changed and not need_scan else 0


def _github_output(**values) -> None:
    """GitHub Actions adim ciktisi (yerelde calisirken sessizce atlanir)."""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


def cmd_search(args, cfg: Config) -> int:
    """Bir urun adini kaydetmeden arar - sorgu ayarlamak icin ya da uygulama icin."""
    options = {}
    if args.options:
        try:
            options = json.loads(args.options) or {}
        except json.JSONDecodeError:
            print(f"{C.RED}--options JSON degil{C.RESET}")
            return 2
        if not isinstance(options, dict):
            options = {}

    def opt_list(key, cli_value):
        value = cli_value or options.get(key) or []
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",")]
        return [str(v).strip()[:80] for v in value if str(v).strip()][:30]

    sites = [s for s in opt_list("sites", args.sites) if s in SOURCES]
    product = Product(
        id="onizleme", query=" ".join(args.query).strip()[:200],
        sites=sites or None,
        exclude=opt_list("exclude", args.exclude),
        must_include=opt_list("must_include", args.must_include),
        min_price=_decimal(args.min_price or options.get("min_price")),
        max_price=_decimal(args.max_price or options.get("max_price")),
    )
    used_sites = sites_for(product, cfg)

    if args.json:
        fetcher = Fetcher(min_delay=cfg.get("min_delay_seconds"))
        try:
            result = search(product.query, fetcher, used_sites, rules_for(product))
        finally:
            fetcher.close()
        payload = {
            "request": args.request or "",
            "query": product.query,
            "generated_at": int(__import__("time").time()),
            "options": {
                "sites": product.sites or [],
                "exclude": product.exclude,
                "must_include": product.must_include,
                "min_price": float(product.min_price) if product.min_price else None,
                "max_price": float(product.max_price) if product.max_price else None,
            },
            "searched_sites": used_sites,
            "result": result.to_dict(limit=40, rejected_limit=30),
        }
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"{len(result.offers)} teklif, {len(result.near)} yakin sonuc -> {args.json}")
        for n in result.notes:
            print(f"  {n}")
        return 0

    print(f"Aranan: {C.BOLD}{product.query}{C.RESET}"
          f"  {C.DIM}({', '.join(used_sites)}){C.RESET}\n")
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
    sr.add_argument("--json", help="sonucu bu JSON dosyasina yaz (uygulama icin)")
    sr.add_argument("--request", help="istek kimligi (JSON ciktisina yazilir)")
    sr.add_argument("--options", help="JSON secenekler: sites, exclude, must_include, min_price, max_price")
    sr.set_defaults(func=cmd_search)

    ap = sub.add_parser("apply", help="uygulamadan gelen JSON komutunu uygula")
    ap.add_argument("json", help="komut JSON'u ya da '-' (stdin)")
    ap.add_argument("--result", help="sonucu bu dosyaya JSON olarak yaz")
    ap.set_defaults(func=cmd_apply)

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

    rp = sub.add_parser("report", help="uygulamayi (docs/) gecmisten yeniden uret")
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
