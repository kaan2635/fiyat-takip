"""Surum 2: eslestirme motoru, yeni kaynaklar, uygulama komutlari ve veri paketi."""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup

from tracker.commands import CommandError, apply, parse_command
from tracker.config import Config, Product
from tracker.search import (MatchRules, Offer, SearchResult, evaluate, listing_key, normalize,
                            parse_amazon, parse_jsonld_list, parse_n11_search, parse_pazarama,
                            query_tokens, search, variant_tokens)
from tracker.store import ScanRow, load_latest, write_latest
from tracker.webdata import build_payload, compact_history, deal_rating, schedule_text


def offer(title, price, site="trendyol.com", url=None):
    return Offer(site=site, title=title, price=Decimal(str(price)),
                 url=url or f"https://www.{site}/{abs(hash(title)) % 10**8}-p-{abs(hash(title + str(price))) % 10**9}")


# ------------------------------------------------------------------- metin
def test_birim_ayrilir_128gb_eslesir():
    assert normalize("Apple iPhone 15 128GB") == "apple iphone 15 128 gb"
    assert normalize("0-18kg Oto Koltuğu") == "0 18 kg oto koltugu"
    # Model adlari bozulmamali
    assert normalize("Galaxy S24 5G") == "galaxy s24 5g"
    assert normalize("MX Master 3S") == "mx master 3s"
    r = evaluate("iphone 15 128 gb", [offer("Apple iPhone 15 128GB Siyah", 59999)])
    assert len(r.offers) == 1


@pytest.mark.parametrize("url,key", [
    ("https://www.trendyol.com/besafe/izi-turn-p-346523972?boutiqueId=61&merchantId=1", "trendyol:346523972"),
    ("https://www.hepsiburada.com/besafe-izi-turn-p-HBCV00002U13NY", "hepsiburada:HBCV00002U13NY"),
    ("https://www.hepsiburada.com/logitech-mx-pm-HBC0000C4QAAX", "hepsiburada:HBC0000C4QAAX"),
    ("https://www.amazon.com.tr/dp/B0F28Z3CQG", "amazon:B0F28Z3CQG"),
    ("https://www.amazon.com.tr/Maxi-Cosi-Mica/dp/B0F28Z3CQG/ref=sr_1_1", "amazon:B0F28Z3CQG"),
    ("https://www.n11.com/urun/philips-tah1205bk00-kulaklik-67019198", "n11:67019198"),
    ("https://www.pazarama.com/maxi-cosi-mica-360-p-8712930187189", "pazarama:8712930187189"),
    ("https://www.teknosa.com/apple-iphone-15-128gb-p-125079200", "teknosa:125079200"),
    ("https://www.mediamarkt.com.tr/tr/product/_apple-iphone-15-1232436.html", "mediamarkt:1232436"),
    ("https://ornek.com/urun/abc/?x=1", "ornek.com/urun/abc"),
])
def test_ilan_anahtari(url, key):
    assert listing_key(url) == key


# ------------------------------------------------------------- model ayrimi
def test_pro_varken_duz_model_tercih_edilir():
    found = [offer("Apple iPhone 15 128 GB", 59999), offer("Apple iPhone 15 Pro 128 GB", 68000),
             offer("Apple iPhone 15 Plus 128 GB", 64000)]
    r = evaluate("iPhone 15 128 GB", found)
    assert [o.price for o in r.offers] == [Decimal("59999")]
    assert {x.code for x in r.rejected} == {"variant"}
    assert all(x.near for x in r.rejected)          # kullaniciya "yakin sonuc" olarak gosterilir


def test_hepsi_farkli_modelse_kabul_edilir_ama_uyarilir():
    # Kullanicinin gercek durumu: "iZi Turn i-Size" aradi, satista yalnizca "B i-Size" var
    found = [offer("BeSafe Izi Turn B i-Size 0-18kg Oto Koltuğu", 45999, "hepsiburada.com"),
             offer("Besafe Izı Turn B I-Size Oto Koltuğu 0-18 Kg", 57899)]
    r = evaluate("besafe izi turn i size", found)
    assert len(r.offers) == 2
    assert r.warnings and "b" in r.warnings[0]


def test_tekrarlanan_kelime_bolgeyi_genisletmez():
    # Basliktaki ikinci "360" yuzunden araya giren "i" model ayrimi sanilmamali
    t = "Maxi-Cosi Mica 360 Pro i-Size Dönebilen 360 Derece Oto Koltuğu"
    assert variant_tokens("maxi cosi mica 360 pro", t) == set()
    assert variant_tokens("iphone 15", "Apple iPhone 15 Pro Max 256 GB") == {"pro", "max"}
    # Basligin sonundaki aciklama ayrim sayilmaz
    assert variant_tokens("philips airfryer", "Philips Airfryer Siyah Fritöz 2000W max güç") == set()


# --------------------------------------------------------- aksesuar ve fiyat
def test_aksesuar_ve_yenilenmis_elenir():
    found = [offer("Apple iPhone 15 128 GB", 59999), offer("Apple iPhone 15 128 GB Kılıf Şeffaf", 150),
             offer("Yenilenmiş Apple iPhone 15 128 GB", 45000), offer("Apple Uyumlu iPhone 15 128 GB Kablo", 300)]
    r = evaluate("iphone 15 128 gb", found)
    assert [o.price for o in r.offers] == [Decimal("59999")]
    assert {x.code for x in r.rejected} == {"default_exclude"}


def test_sorguda_varsa_aksesuar_kelimesi_engel_degil():
    r = evaluate("iphone 15 kılıf", [offer("Apple iPhone 15 Kılıf Şeffaf", 150)])
    assert len(r.offers) == 1


def test_cok_ucuz_teklif_supheli_sayilir():
    found = [offer("Maxi-Cosi Mica 360 Pro i-Size", 25949), offer("Maxi-Cosi Mica 360 Pro I-Size Oto", 26399),
             offer("Maxi Cosi Mica 360 Pro İSize", 27000), offer("Maxi-Cosi Mica 360 Pro Minder Seti", 900)]
    r = evaluate("maxi cosi mica 360 pro", found)
    assert min(o.price for o in r.offers) == Decimal("25949")
    out = [x for x in r.rejected if x.code == "outlier"]
    assert len(out) == 1 and out[0].near


def test_ayni_ilan_bir_kez_sayilir():
    url = "https://www.trendyol.com/a/b-p-123"
    r = evaluate("abc urun", [offer("ABC Urun", 100, url=url), offer("ABC Urun", 90, url=url + "?merchantId=5")])
    assert len(r.offers) == 1 and r.offers[0].price == Decimal("90")


def test_kullanici_karari_kurallardan_once_gelir():
    good = offer("Tamamen baska baslik", 500, url="https://www.trendyol.com/x-p-1")
    bad = offer("Logitech MX Master 4", 4000, url="https://www.trendyol.com/x-p-2")
    ok = offer("Logitech MX Master 4 Mouse", 4200, url="https://www.trendyol.com/x-p-3")
    rules = MatchRules(include_keys=["trendyol:1"], exclude_keys=["https://www.trendyol.com/x-p-2"])
    r = evaluate("logitech mx master 4", [good, bad, ok], rules)
    assert [o.key for o in r.offers] == ["trendyol:1", "trendyol:3"]
    assert any(x.code == "user" for x in r.rejected)


def test_sonuc_sozluge_doner_ve_eski_kullanim_calisir():
    r = evaluate("iphone 15", [offer("Apple iPhone 15", 50000), offer("Apple iPhone 15 Pro", 60000)])
    offers, notes = r                               # eski "offers, notes = search(...)"
    d = r.to_dict()
    assert d["count"] == 1 and d["rejected"][0]["code"] == "variant"
    json.dumps(d)                                   # serilestirilebilir


# ------------------------------------------------------------- ayristiricilar
def test_amazon_arama_kartlari():
    html = """<div data-component-type="s-search-result" data-asin="B0F28Z3CQG">
      <div data-cy="title-recipe"><h2><span>Maxi-Cosi Mica 360 Pro i-Size</span></h2></div>
      <img class="s-image" src="https://m.media-amazon.com/x.jpg">
      <span class="a-price"><span class="a-offscreen">25.949,25 TL</span></span>
    </div>
    <div data-component-type="s-search-result" data-asin="B000000000"><h2>Fiyatsiz</h2></div>"""
    offers = parse_amazon(html, BeautifulSoup(html, "lxml"))
    assert len(offers) == 1
    o = offers[0]
    assert o.price == Decimal("25949.25") and o.url == "https://www.amazon.com.tr/dp/B0F28Z3CQG"
    assert o.key == "amazon:B0F28Z3CQG" and o.image.endswith("x.jpg")


def test_pazarama_sepet_fiyati_alinir():
    html = """<div data-testid="listing-product-card-grid">
      <a href="/maxi-cosi-mica-p-8712930187189" title="Maxi-Cosi Mica 360 Pro">
      <span data-testid="product-card-title">Maxi-Cosi Mica 360 Pro</span></a>
      <div data-testid="base-product-card-price-container">25.499,00 TL Sepette 24.899,00 TL Plus ile 24.849,00 TL</div>
    </div>
    <div data-testid="listing-product-card-grid">
      <a href="/b-p-2" title="Ikinci"><span data-testid="product-card-title">Ikinci</span></a>
      <div data-testid="base-product-card-price-container">Son 10 Günün En Düşük Fiyatı 34.599,00 TL Sepette 25.949,25 TL</div>
    </div>"""
    offers = parse_pazarama(html, BeautifulSoup(html, "lxml"))
    assert [o.price for o in offers] == [Decimal("24899.00"), Decimal("25949.25")]
    assert offers[0].url == "https://www.pazarama.com/maxi-cosi-mica-p-8712930187189"


def test_jsonld_liste_ve_markasiz_urun():
    html = """<script type="application/ld+json">{"@type":"ItemList","itemListElement":[
      {"@type":"ListItem","item":{"@type":"Product","name":"Maxi-cosi Mica 360 Pro","brand":{"name":"Diğer"},
       "url":"https://www.n11.com/urun/maxi-cosi-60303290","image":"https://n11/img.jpg",
       "offers":{"@type":"Offer","price":"24719","priceCurrency":"TRY"}}},
      {"@type":"ListItem","item":{"@type":"Product","name":"iPhone 15","brand":{"name":"Apple"},
       "url":"/apple-iphone-15-p-125079200","offers":{"price":59999}}}]}</script>"""
    soup = BeautifulSoup(html, "lxml")
    n11 = parse_n11_search(html, soup)
    assert n11[0].title == "Maxi-cosi Mica 360 Pro"          # "Diğer" marka eklenmez
    assert n11[0].key == "n11:60303290"
    tek = parse_jsonld_list("teknosa.com", "https://www.teknosa.com/")(html, soup)
    assert tek[1].title == "Apple iPhone 15"                   # marka eklenir
    assert tek[1].url == "https://www.teknosa.com/apple-iphone-15-p-125079200"


class _SahteSite:
    """Her URL icin onceden verilmis HTML doner; paralel arama testi icin."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url):
        from tracker.fetch import FetchResult
        self.calls.append(url)
        for marker, html in self.pages.items():
            if marker in url:
                return FetchResult(url=url, final_url=url, status=200, html=html)
        return FetchResult(url=url, status=403, blocked=True, block_reason="HTTP 403")

    def close(self):
        pass


def test_arama_kaynak_ozeti():
    ty = '{"products":[{"brand":"Apple","name":"iPhone 15 128 GB","url":"/apple/iphone-p-1",' \
         '"price":{"discountedPrice":59000}}]}'
    fake = _SahteSite({"trendyol.com": ty})
    r = search("iphone 15 128 gb", fake, ["trendyol", "hepsiburada"], parallel=False)
    assert len(r.offers) == 1
    assert r.per_site["trendyol"] == {"ok": True, "found": 1, "kept": 1}
    assert r.per_site["hepsiburada"]["ok"] is False


def test_fetcher_kopyasi_profilleri_geri_verir():
    from tracker.fetch import Fetcher
    f = Fetcher(min_delay=0)
    assert f._good_profile["pazarama.com"] == "safari17_0"     # olculmus ipucu
    twin = f.fork()
    twin._good_profile["yeni.com"] = "edge101"
    f.absorb(twin)
    assert f._good_profile["yeni.com"] == "edge101"


# ------------------------------------------------------------------ komutlar
def _cfg(tmp_path):
    cfg = Config(path=tmp_path / "products.yaml")
    cfg.products.append(Product(id="mica", query="Maxi Cosi Mica", name="Mica",
                                include_listings=["n11:1"]))
    cfg.products.append(Product(id="url-urun", url="https://www.trendyol.com/x-p-9", name="Url"))
    return cfg


def test_komut_ekle_ve_tara(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, parse_command(json.dumps({"ops": [
        {"op": "add", "query": "BeSafe iZi Turn B i-Size", "target": "42.000,50".replace(".", ""),
         "exclude_listings": ["https://www.trendyol.com/a-p-5"], "sites": ["trendyol", "amazon"]}]})))
    p = cfg.by_id(res.added[0])
    assert p.query == "BeSafe iZi Turn B i-Size"
    assert p.target_price == Decimal("42000.50")
    assert p.exclude_listings == ["trendyol:5"]
    assert p.sites == ["trendyol", "amazon"] and p.added_at
    assert res.scan_ids == [p.id]


def test_komut_ayni_urunu_iki_kez_eklemez(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, [{"op": "add", "query": "maxi cosi mica"}])
    assert not res.added and "zaten" in res.messages[0]


def test_komut_hedef_degisikligi_tarama_gerektirmez(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, [{"op": "update", "id": "mica", "set": {"target": 24000, "name": "Oto koltugu"}}])
    assert cfg.by_id("mica").target_price == Decimal("24000.00")
    assert res.changed and res.scan_ids == []


def test_komut_bu_urun_degil(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, [{"op": "update", "id": "mica", "reject": ["n11:1", "n11:2"]}])
    p = cfg.by_id("mica")
    assert p.exclude_listings == ["n11:1", "n11:2"]
    assert p.include_listings == []              # onaylanmisken reddedildi: onay kalkar
    assert res.scan_ids == ["mica"]
    apply(cfg, [{"op": "update", "id": "mica", "unreject": ["n11:2"]}])
    assert p.exclude_listings == ["n11:1"]


def test_komut_hatalari_digerlerini_durdurmaz(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, [{"op": "update", "id": "yok"}, {"op": "uc"}, {"op": "add", "url": "ftp://x"},
                      {"op": "add", "query": "Yeni", "target": "abc"}, {"op": "remove", "id": "url-urun"},
                      {"op": "update", "id": "mica", "set": {"sites": ["olmayan"]}}])
    errors = [m for m in res.messages if m.startswith("hata")]
    assert len(errors) == 5
    assert cfg.by_id("url-urun") is None


def test_komut_tarama_ve_silme(tmp_path):
    cfg = _cfg(tmp_path)
    res = apply(cfg, [{"op": "scan", "ids": ["mica", "yok"]}])
    assert res.scan_ids == ["mica"] and not res.changed
    res = apply(cfg, [{"op": "update", "id": "mica", "set": {"query": "Maxi Cosi Mica 360 Pro"}},
                      {"op": "remove", "id": "mica"}])
    assert res.scan_ids == []                      # silinen urun taranmaz
    assert apply(cfg, [{"op": "scan"}]).scan_all


def test_komut_json_dogrulama():
    with pytest.raises(CommandError):
        parse_command("{bozuk")
    with pytest.raises(CommandError):
        parse_command('{"ops": []}')
    assert parse_command('{"op": "scan"}') == [{"op": "scan"}]


def test_cli_apply_github_ciktisi(tmp_path, monkeypatch):
    from tracker.cli import main
    cfg = _cfg(tmp_path)
    cfg.save()
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = main(["--config", str(cfg.path), "apply",
                 json.dumps({"ops": [{"op": "update", "id": "mica", "reject": ["n11:7"]}]})])
    assert code == 0
    text = out.read_text()
    assert "need_scan=true" in text and "scan_ids=mica" in text
    assert "n11:7" in (tmp_path / "products.yaml").read_text(encoding="utf-8")


# ------------------------------------------------------- son durum dosyasi
def test_son_durum_taranmayanlari_korur(tmp_path):
    path = tmp_path / "latest.json"
    row = lambda pid, price: ScanRow("2026-10-15T10:00:00+00:00", pid, pid, "ornek.com", "u",
                                     Decimal(price), "TRY", True, "ok", "t")
    write_latest([row("a", "10"), row("b", "20")], {}, path=path)
    write_latest([row("a", "9")], {}, path=path)                      # --only a
    latest = load_latest(path)
    assert latest["a"]["price"] == 9.0 and latest["b"]["price"] == 20.0
    write_latest([], {}, path=path, keep_ids=["a"])                    # b silindi
    assert set(load_latest(path)) == {"a"}


def test_son_durum_arama_ayrintilari():
    path_res = evaluate("iphone 15", [offer("Apple iPhone 15", 50000), offer("Apple iPhone 15 Pro", 60000)])
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "latest.json"
        r = ScanRow("2026-10-15T10:00:00+00:00", "p", "p", "trendyol.com", "u", Decimal("50000"),
                    "TRY", True, "ok", "t")
        write_latest([r], {}, path=path, searches={"p": path_res})
        e = load_latest(path)["p"]
        assert e["offers"][0]["key"] and e["rejected"][0]["code"] == "variant"


# ---------------------------------------------------------------- veri paketi
def _points(prices, start=None, step_hours=8):
    start = start or datetime.now(timezone.utc) - timedelta(hours=step_hours * len(prices))
    return [{"scanned_at": (start + timedelta(hours=step_hours * i)).isoformat(), "price": Decimal(str(v))}
            for i, v in enumerate(prices)]


def test_fiyat_konumu():
    assert deal_rating(_points([100, 100, 100, 100])) is None               # hic oynamamis
    assert deal_rating(_points([100, 90])) is None                          # veri az
    assert deal_rating(_points([100, 110, 105, 90]))["level"] == "lowest"
    assert deal_rating(_points([100, 120, 110, 102]))["level"] == "good"
    assert deal_rating(_points([100, 120, 110, 119]))["level"] == "high"
    old = _points([50, 50, 50], start=datetime.now(timezone.utc) - timedelta(days=200))
    assert deal_rating(old + _points([100, 110, 105, 100]))["low"] == 100.0  # 90 gun disi sayilmaz


def test_grafik_noktalari_seyreltilir_ama_en_dusuk_kalir():
    pts = _points([100 + (i % 7) for i in range(900)], step_hours=1)
    pts[123]["price"] = Decimal("50")
    out = compact_history(pts, limit=60)
    assert len(out) <= 60
    assert min(p[1] for p in out) == 50.0
    assert out == sorted(out)


def test_zamanlama_metni(tmp_path):
    wf = tmp_path / "scan.yml"
    wf.write_text('    - cron: "0 6,12,18 * * *"\n')
    assert schedule_text(wf) == "Her gün 09:00, 15:00, 21:00"
    wf.write_text('    - cron: "0 */3 * * *"\n')
    assert schedule_text(wf) == "3 saatte bir"
    wf.write_text('    - cron: "30 6 * * 1-5"\n')
    assert schedule_text(wf) == "Her gün 09:30 (hafta içi)"


def test_paket_ayni_ilani_takip_eden_urunleri_isaretler():
    cfg = Config()
    cfg.products += [Product(id="a", query="BeSafe iZi Turn B i-Size"),
                     Product(id="b", query="besafe izi turn i size", target_price=Decimal("43000")),
                     Product(id="c", query="Yeni urun")]
    url = "https://www.hepsiburada.com/besafe-p-HBCV00002U13NY"
    latest = {pid: {"status": "ok", "price": 45999.0, "site": "hepsiburada.com", "url": url,
                    "offers": [{"site": "hepsiburada.com", "title": "BeSafe", "price": 45999.0, "url": url}]}
              for pid in ("a", "b")}
    data = build_payload(cfg, latest, {"a": _points([46000, 45999])}, "2026-10-15T10:00:00+00:00", repo="k/r")
    by = {p["id"]: p for p in data["products"]}
    assert by["b"]["duplicate_of"] == "a" and "duplicate_of" not in by["a"]
    assert by["c"]["status"] == "pending" and by["c"]["price"] is None
    assert by["b"]["target"] == 43000.0
    assert by["a"]["offers"][0]["key"] == "hepsiburada:HBCV00002U13NY"
    assert data["repo"] == "k/r" and any(s["id"] == "amazon" for s in data["sources"])


def test_okunamayan_urunde_son_bilinen_fiyat():
    cfg = Config()
    cfg.products.append(Product(id="a", url="https://www.trendyol.com/x-p-1"))
    data = build_payload(cfg, {"a": {"status": "blocked", "price": None}}, {"a": _points([100, 90])},
                         "2026-10-15T10:00:00+00:00", repo="")
    p = data["products"][0]
    assert p["price"] == 90.0 and p["price_is_stale"] is True


def test_uygulama_yazilir_ve_veri_gomulur(tmp_path):
    from tracker.report import write_app
    cfg = Config()
    cfg.products.append(Product(id="a", query="</script><b>x</b>", name="</script>"))
    path = write_app(cfg, path=tmp_path / "docs" / "index.html", latest_path=tmp_path / "l.json",
                     history_path=tmp_path / "h.csv", repo="k/r")
    html = path.read_text(encoding="utf-8")
    docs = path.parent
    for name in ("app.js", "app.css", "sw.js", "manifest.webmanifest", "data.json", ".nojekyll"):
        assert (docs / name).exists(), name
    assert "__DATA__" not in html and "__VERSION__" not in html
    assert "</script><b>" not in html                       # gomulu veri sayfayi kiramaz
    embedded = html.split('id="ft-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(embedded)["products"][0]["name"] == "</script>"
    assert "__VERSION__" not in (docs / "sw.js").read_text(encoding="utf-8")


def test_en_dusuk_bildirimi_tekrarlanmaz():
    from tracker.scan import build_alerts
    from tracker.store import PriceStats
    cfg = Config()
    p = Product(id="p", url="https://ornek.com/u")
    row = ScanRow("2026-01-01T00:00:00+00:00", "p", "Urun", "ornek.com", "u", Decimal("90"),
                  "TRY", True, "ok", "t")
    yeni_dip = PriceStats(current=Decimal("90"), previous=Decimal("95"), lowest=Decimal("90"),
                          highest=Decimal("100"), points=4)
    assert any(a.kind == "lowest" for a in build_alerts(p, row, yeni_dip, True, cfg))
    dipte_kaldi = PriceStats(current=Decimal("90"), previous=Decimal("90"), lowest=Decimal("90"),
                             highest=Decimal("100"), points=5)
    assert not any(a.kind == "lowest" for a in build_alerts(p, row, dipte_kaldi, True, cfg))
