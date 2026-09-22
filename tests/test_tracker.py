"""Cekirdek mantigin testleri (ag erisimi gerektirmez)."""

from decimal import Decimal

import pytest

from tracker.config import Config, Product, slugify
from tracker.fetch import cookies_for, detect_block, host_of
from tracker.money import detect_currency, format_price, parse_machine_price, parse_price
from tracker.parsers import parse_page, site_of
from tracker.scan import build_alerts, sanity_check
from tracker.store import PriceStats, ScanRow, stats_for


# ------------------------------------------------------------------ fiyat cozme
@pytest.mark.parametrize("text,expected", [
    ("1.299,90 TL", "1299.90"),
    ("₺1.299,90", "1299.90"),
    ("24.999 TL", "24999"),
    ("1299,90", "1299.90"),
    ("1,299.90", "1299.90"),          # ABD bicimi
    ("1.234.567,89 TL", "1234567.89"),
    ("49,5 TL", "49.5"),
    ("1 299,90 TL", "1299.90"),       # bosluklu binlik
    ("Sepette 2 adet 1.299,90 TL", "1299.90"),
    ("2 adet 899 TL", "899"),
])
def test_parse_price(text, expected):
    assert parse_price(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "   ", "abc", None, "TL"])
def test_parse_price_bos(text):
    assert parse_price(text) is None


def test_makine_fiyati_nokta_ondalik():
    assert parse_machine_price("1299.90") == Decimal("1299.90")
    assert parse_machine_price("1,299.90") == Decimal("1299.90")
    # Bazi siteler makine alanina da TR bicimi yaziyor
    assert parse_machine_price("1.299,90") == Decimal("1299.90")
    assert parse_machine_price(1299.9) == Decimal("1299.9")


def test_para_birimi():
    assert detect_currency("1.299,90 TL") == "TRY"
    assert detect_currency("$24.99") == "USD"
    assert detect_currency("19,99 €") == "EUR"
    assert detect_currency("1299") == "TRY"          # varsayilan


def test_bicimleme():
    assert format_price(Decimal("1299.9")) == "1.299,90 TL"
    assert format_price(Decimal("24999")) == "24.999,00 TL"
    assert format_price(None) == "-"


# ---------------------------------------------------------------- ayristiricilar
JSONLD_SAYFA = """<html><head><title>Test Urun</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Test Urun",
 "offers":{"@type":"Offer","price":"1299.90","priceCurrency":"TRY",
 "availability":"https://schema.org/InStock"}}
</script></head><body>%s</body></html>""" % ("x" * 30000)

MICRODATA_SAYFA = """<html><head><title>Mikro</title></head><body itemscope itemtype="https://schema.org/Product">
<span itemprop="price" content="459.00">459,00 TL</span>
<meta itemprop="priceCurrency" content="TRY">
<link itemprop="availability" href="https://schema.org/OutOfStock">%s</body></html>""" % ("x" * 30000)

META_SAYFA = """<html><head><title>Meta</title>
<meta property="product:price:amount" content="79.99">
<meta property="product:price:currency" content="TRY">
</head><body>%s</body></html>""" % ("x" * 30000)

TRENDYOL_SAYFA = """<html><head><title>Trendyol Urun</title></head><body>
<script>window.__PRODUCT_DETAIL_APP_INITIAL_STATE__ = {"product":{"name":"Telefon",
"isSellable":true,"price":{"discountedPrice":{"value":56999.0},
"sellingPrice":{"value":58999.0},"currency":"TRY"}}};</script>%s</body></html>""" % ("x" * 30000)


def test_jsonld_ayristirma():
    r = parse_page(JSONLD_SAYFA, "https://ornek.com/urun")
    assert r.price == Decimal("1299.90")
    assert r.currency == "TRY"
    assert r.in_stock is True
    assert r.method == "jsonld"


def test_microdata_ayristirma():
    r = parse_page(MICRODATA_SAYFA, "https://ornek.com/urun")
    assert r.price == Decimal("459.00")
    assert r.in_stock is False


def test_meta_ayristirma():
    r = parse_page(META_SAYFA, "https://ornek.com/urun")
    assert r.price == Decimal("79.99")


def test_trendyol_gomulu_state():
    r = parse_page(TRENDYOL_SAYFA, "https://www.trendyol.com/marka/urun-p-1")
    # Indirimli fiyat, liste fiyatina tercih edilmeli
    assert r.price == Decimal("56999.0")
    assert r.method.startswith("trendyol:state")
    assert r.in_stock is True


def test_fiyat_yoksa_basarisiz():
    r = parse_page("<html><body>fiyat yok</body></html>", "https://ornek.com/u")
    assert r.price is None
    assert not r.ok


def test_site_of():
    assert site_of("https://www.trendyol.com/x") == "trendyol.com"
    assert site_of("https://m.hepsiburada.com/y") == "m.hepsiburada.com"


# -------------------------------------------------------------------- engelleme
def test_engel_tespiti():
    assert detect_block(403, "", "u", "u")
    assert detect_block(200, "<title>Attention Required! | Cloudflare</title>" + "x" * 30000, "u", "u")
    assert detect_block(200, "x" * 100, "u", "u")                      # cok kucuk
    assert detect_block(200, "x" * 30000,
                        "https://www.trendyol.com/p-1",
                        "https://www.trendyol.com/en/select-country")  # ulke kapisi
    assert detect_block(200, "<title>Urun</title>" + "x" * 60000,
                        "https://a.com/p", "https://a.com/p") is None


def test_trendyol_cerezleri():
    assert cookies_for("https://www.trendyol.com/x")["countryCode"] == "TR"
    assert cookies_for("https://ornek.com/x") == {}
    assert host_of("https://www.n11.com/urun/x") == "n11.com"


# ------------------------------------------------------------------ istatistik
def test_istatistikler():
    rows = [{"price": Decimal(v), "scanned_at": str(i)}
            for i, v in enumerate(("100", "90", "95"))]
    s = stats_for(rows)
    assert s.current == Decimal("95")
    assert s.previous == Decimal("90")
    assert s.lowest == Decimal("90")
    assert s.highest == Decimal("100")
    assert round(s.change_percent, 2) == 5.56


def test_rekor_dusuk_duz_fiyatta_tetiklenmez():
    duz = [{"price": Decimal("100"), "scanned_at": str(i)} for i in range(4)]
    assert stats_for(duz).is_lowest_ever is False

    dusen = [{"price": Decimal(v), "scanned_at": str(i)}
             for i, v in enumerate(("100", "95", "90"))]
    assert stats_for(dusen).is_lowest_ever is True


def test_supheli_fiyat():
    gecmis = [{"price": Decimal(v)} for v in ("1000", "1010", "990", "1005")]
    assert sanity_check(Decimal("1020"), gecmis) is None
    assert sanity_check(Decimal("150"), gecmis) is not None    # taksit tutari gibi
    assert sanity_check(Decimal("9000"), gecmis) is not None
    assert sanity_check(Decimal("5"), gecmis[:2]) is None      # veri az, karar verme


# ---------------------------------------------------------------------- uyarilar
def _row(price, in_stock=True):
    return ScanRow("2026-01-01T00:00:00+00:00", "p", "Urun", "ornek.com",
                   "https://ornek.com/u", Decimal(price), "TRY", in_stock, "ok", "test")


def test_hedef_fiyat_uyarisi_bir_kez():
    cfg = Config()
    p = Product(id="p", url="https://ornek.com/u", target_price=Decimal("1000"))
    # Hedefin altina yeni indi -> uyari
    st = PriceStats(current=Decimal("950"), previous=Decimal("1100"), lowest=Decimal("950"),
                    highest=Decimal("1100"), points=2)
    assert any(a.kind == "target" for a in build_alerts(p, _row("950"), st, True, cfg))

    # Zaten hedefin altindaydi -> tekrar uyarma
    st2 = PriceStats(current=Decimal("940"), previous=Decimal("950"), lowest=Decimal("940"),
                     highest=Decimal("1100"), points=3)
    assert not any(a.kind == "target" for a in build_alerts(p, _row("940"), st2, True, cfg))


def test_dusus_ve_stok_uyarilari():
    cfg = Config()
    p = Product(id="p", url="https://ornek.com/u")
    st = PriceStats(current=Decimal("900"), previous=Decimal("1000"), lowest=Decimal("900"),
                    highest=Decimal("1000"), points=3)
    kinds = {a.kind for a in build_alerts(p, _row("900"), st, False, cfg)}
    assert "drop" in kinds      # %10 dusus, esik %5
    assert "lowest" in kinds
    assert "stock" in kinds     # onceden stokta degildi, simdi var


def test_basarisiz_olcumde_uyari_yok():
    cfg = Config()
    p = Product(id="p", url="https://ornek.com/u", target_price=Decimal("1000"))
    row = ScanRow("2026-01-01T00:00:00+00:00", "p", "Urun", "ornek.com",
                  "https://ornek.com/u", None, "TRY", None, "blocked", "http")
    assert build_alerts(p, row, PriceStats(), None, cfg) == []


# ---------------------------------------------------------------- yapilandirma
def test_config_yaz_oku(tmp_path):
    path = tmp_path / "products.yaml"
    cfg = Config(path=path)
    cfg.products.append(Product(id="a", url="https://www.trendyol.com/x",
                                name="Urun A", target_price=Decimal("100")))
    cfg.save()

    geri = Config.load(path)
    assert len(geri.products) == 1
    assert geri.products[0].target_price == Decimal("100")
    assert geri.products[0].site == "trendyol.com"
    assert geri.find("urun a") is not None
    assert geri.find("a") is not None


def test_slugify():
    assert slugify("Apple iPhone 15 128 GB Siyah") == "apple-iphone-15-128-gb-siyah"
    assert slugify("Çiçek Şemsiye ĞİÖŞÜ") == "cicek-semsiye-giosu"
    assert slugify("") == "urun"


def test_benzersiz_kimlik():
    cfg = Config()
    cfg.products.append(Product(id="test", url="https://a.com/1"))
    assert cfg.unique_id("test") == "test-2"
    assert cfg.unique_id("baska") == "baska"


# ------------------------------------------------------- indirici yedekleme sirasi
class _SahteIndirici:
    """Testte gercek ag yerine gecen, basarili/basarisiz taklidi yapan indirici."""

    HTML = ('<html><head><title>T</title>'
            '<script type="application/ld+json">{"@type":"Product","name":"T",'
            '"offers":{"@type":"Offer","price":"100","priceCurrency":"TRY"}}</script>'
            '</head><body>' + "x" * 30000 + "</body></html>")

    def __init__(self, ok, backend="http"):
        self.ok, self.backend, self.calls = ok, backend, 0

    def get(self, url):
        from tracker.fetch import FetchResult
        self.calls += 1
        if self.ok:
            return FetchResult(url=url, final_url=url, status=200,
                               html=self.HTML, backend=self.backend)
        return FetchResult(url=url, error=f"{self.backend} patladi", backend=self.backend)

    def close(self):
        pass


@pytest.mark.parametrize("mode,http_ok,browser_ok,beklenen,http_cagri,tarayici_cagri", [
    # HTTP modu: once HTTP; ancak basarisizsa tarayici acilir (bosa acilmaz)
    ("http",    True,  True,  "ok",    1, 0),
    ("http",    True,  False, "ok",    1, 0),
    ("http",    False, True,  "ok",    1, 1),
    ("http",    False, False, "error", 1, 1),
    # Tarayici modu: once tarayici; tarayici tutmazsa HTTP'ye duser
    ("browser", True,  True,  "ok",    0, 1),
    ("browser", True,  False, "ok",    1, 1),
    ("browser", False, True,  "ok",    0, 1),
    ("browser", False, False, "error", 1, 1),
])
def test_indirici_yedekleme(monkeypatch, mode, http_ok, browser_ok,
                            beklenen, http_cagri, tarayici_cagri):
    from tracker import scan as scan_mod

    monkeypatch.setattr(scan_mod.browser_mod, "available", lambda: True)
    fetcher = _SahteIndirici(http_ok, "http")
    browser = _SahteIndirici(browser_ok, "browser")
    product = Product(id="p", url="https://ornek.com/u", mode=mode)

    row, _ = scan_mod.scan_product(product, fetcher, Config(), [], browser)

    assert row.status == beklenen
    assert fetcher.calls == http_cagri
    assert browser.calls == tarayici_cagri


def test_tarayici_yoksa_http_ile_devam(monkeypatch):
    """Playwright kurulu degilse mode:browser olan urun yine de HTTP ile denenir."""
    from tracker import scan as scan_mod

    monkeypatch.setattr(scan_mod.browser_mod, "available", lambda: False)
    fetcher = _SahteIndirici(True, "http")
    product = Product(id="p", url="https://ornek.com/u", mode="browser")

    row, _ = scan_mod.scan_product(product, fetcher, Config(), [], None)
    assert row.status == "ok"
    assert row.price == Decimal("100")


# ------------------------------------------------------------------------ PWA
def test_manifest_gecerli():
    import json as _json
    from tracker.pwa import MANIFEST

    data = _json.loads(_json.dumps(MANIFEST))          # serilestirilebilir mi
    assert data["display"] == "standalone"
    # Goreli yollar sart: GitHub Pages depoyu /<repo>/ altinda yayinliyor
    assert data["start_url"] == "./" and data["scope"] == "./"
    assert not any(i["src"].startswith("/") for i in data["icons"])
    assert any(i["purpose"] == "maskable" for i in data["icons"])


def test_pwa_varliklari_yazilir(tmp_path):
    import json as _json
    from tracker.pwa import write_assets

    write_assets(tmp_path)
    for name in ("manifest.webmanifest", "sw.js", "app.js", ".nojekyll"):
        assert (tmp_path / name).exists(), name
    _json.loads((tmp_path / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_head_etiketleri_ios_icin_tam():
    from tracker.pwa import head_tags

    html = head_tags()
    for gerekli in ("manifest.webmanifest", "apple-mobile-web-app-capable",
                    "apple-touch-icon", "viewport-fit=cover", "theme-color"):
        assert gerekli in html, gerekli


def test_rapor_pwa_kabugunu_icerir(tmp_path):
    from tracker.report import write_report
    from tracker.store import ScanRow

    row = ScanRow("2026-01-01T00:00:00+00:00", "p", "Urun", "ornek.com",
                  "https://ornek.com/u", Decimal("100"), "TRY", True, "ok", "test")
    path = write_report([row], {"p": PriceStats(current=Decimal("100"), points=1)},
                        {"p": []}, {}, "2026-01-01T00:00:00+00:00",
                        path=tmp_path / "index.html")
    html = path.read_text(encoding="utf-8")
    assert 'id="ft-scan"' in html                 # tarama dugmesi
    assert 'id="ft-sheet-settings"' in html       # ayarlar panosu
    assert 'meta name="ft-generated"' in html     # yayin damgasi
    assert (tmp_path / "app.js").exists()         # varliklar yaninda uretildi
