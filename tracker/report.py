"""Tek dosyalik HTML rapor uretici.

Cikti tamamen kendi kendine yeter (harici CSS/JS yok), bu yuzden dosyayi
cift tiklayip acmak da GitHub Pages'te yayinlamak da calisir.

Gorsellestirme kararlari:
- Her urun kendi kartinda, kendi olcegiyle cizilir (kucuk coklu). Tek bir
  grafikte farkli buyuklukteki fiyatlari ust uste koymak ciftı eksen sorununa
  yol acardi; ayri kartlar bunu tamamen onler.
- Tek seri oldugu icin gosterge kutusu yok, basligin kendisi seriyi adlandirir.
- Fiyat dususu alici icin iyi haberdir: yesil + asagi ok + "ucuzladi" etiketi
  birlikte kullanilir, anlam asla yalniz renge birakilmaz.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from . import pwa
from .money import format_price
from .store import PriceStats, ScanRow

REPORT_PATH = Path("docs/index.html")

# Grafik geometrisi
W, H = 640, 150
PAD_L, PAD_R, PAD_T, PAD_B = 14, 86, 18, 26


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _short_price(value: Decimal | float | None, currency: str = "TRY") -> str:
    """Eksen etiketleri icin kisa fiyat: kurusları atar."""
    if value is None:
        return "-"
    d = Decimal(str(value))
    whole = f"{int(d):,}".replace(",", ".")
    symbol = {"TRY": "TL", "USD": "$", "EUR": "€"}.get(currency, currency)
    return f"{whole} {symbol}"


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d.%m")
    except Exception:
        return (iso or "")[:10]


def _fmt_datetime(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return iso or "-"


def _sparkline(points: list[dict], currency: str, target: Decimal | None) -> str:
    """Bir urunun fiyat gecmisini SVG cizgi grafigi olarak dondurur."""
    values = [float(p["price"]) for p in points]
    if not values:
        return '<p class="empty">Henuz olcum yok.</p>'

    if len(values) == 1:
        return (
            f'<p class="empty">Tek olcum var — grafik ikinci taramadan sonra olusur '
            f'({_esc(format_price(Decimal(str(values[0])), currency))}).</p>'
        )

    lo, hi = min(values), max(values)
    if target is not None:
        lo, hi = min(lo, float(target)), max(hi, float(target))
    span = (hi - lo) or (hi * 0.02 or 1.0)
    lo -= span * 0.12
    hi += span * 0.12
    span = hi - lo

    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    n = len(values)

    def x_of(i: int) -> float:
        return PAD_L + (plot_w if n == 1 else plot_w * i / (n - 1))

    def y_of(v: float) -> float:
        return PAD_T + plot_h * (1 - (v - lo) / span)

    coords = [(x_of(i), y_of(v)) for i, v in enumerate(values)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    area = f"{line} L{coords[-1][0]:.1f},{PAD_T + plot_h:.1f} L{coords[0][0]:.1f},{PAD_T + plot_h:.1f} Z"

    # En dusuk ve son nokta isaretlenir; her noktaya etiket konmaz
    i_min = values.index(min(values))
    i_last = n - 1

    parts = [
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Fiyat gecmisi grafigi">'
    ]
    # Yatay kilavuz cizgileri (geri planda kalir)
    for frac in (0, 0.5, 1):
        y = PAD_T + plot_h * frac
        parts.append(f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L + plot_w}" y2="{y:.1f}"/>')

    if target is not None:
        ty = y_of(float(target))
        if PAD_T - 6 <= ty <= PAD_T + plot_h + 6:
            parts.append(f'<line class="target" x1="{PAD_L}" y1="{ty:.1f}" x2="{PAD_L + plot_w}" y2="{ty:.1f}"/>')
            parts.append(f'<text class="tick target-tick" x="{PAD_L + plot_w + 6}" y="{ty + 3.5:.1f}">hedef</text>')

    parts.append(f'<path class="area" d="{area}"/>')
    parts.append(f'<path class="line" d="{line}"/>')

    for idx, label_class in ((i_min, "low"), (i_last, "last")):
        x, y = coords[idx]
        parts.append(f'<circle class="dot {label_class}" cx="{x:.1f}" cy="{y:.1f}" r="4.5"/>')

    # Son deger, cizim alaninin sagindaki ayrilmis seritte etiketlenir.
    # Noktanin yanina koymak uzun fiyatlarda kartin disina tasiyordu.
    lx, ly = coords[i_last]
    label_y = min(max(ly + 4, PAD_T + 10), PAD_T + plot_h - 2)
    parts.append(
        f'<text class="value-label" x="{PAD_L + plot_w + 8:.1f}" y="{label_y:.1f}">'
        f"{_esc(_short_price(Decimal(str(values[i_last])), currency))}</text>"
    )

    if i_min != i_last:
        mx, my = coords[i_min]
        # Etiketi cizim alani icinde tut; kenarlarda hizalamayi cevirerek tasmayi onle
        if i_min == 0:
            anchor, tx = "start", PAD_L
        elif i_min == n - 1:
            anchor, tx = "end", PAD_L + plot_w
        else:
            anchor, tx = "middle", mx
        # Nokta dibe yakinsa etiket altta eksen yazilarina caripardi; ustune al
        ty = my - 10 if my > PAD_T + plot_h * 0.72 else my + 16
        parts.append(
            f'<text class="tick low-label" x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}">'
            f"{_esc(_short_price(Decimal(str(values[i_min])), currency))}</text>"
        )

    # Zaman ekseni: yalnizca ilk ve son tarih
    parts.append(f'<text class="tick" x="{PAD_L}" y="{H - 8}">{_esc(_fmt_date(points[0]["scanned_at"]))}</text>')
    parts.append(
        f'<text class="tick" x="{PAD_L + plot_w}" y="{H - 8}" text-anchor="end">'
        f'{_esc(_fmt_date(points[-1]["scanned_at"]))}</text>'
    )

    # Hover katmani: imlec cizgisi + ipucu icin gorunmez hedefler
    parts.append('<g class="hit-layer">')
    for i, (x, y) in enumerate(coords):
        half = plot_w / max(n - 1, 1) / 2 + 2
        parts.append(
            f'<rect class="hit" x="{max(PAD_L, x - half):.1f}" y="{PAD_T}" '
            f'width="{min(half * 2, plot_w):.1f}" height="{plot_h}" '
            f'data-i="{i}" data-x="{x:.1f}" data-y="{y:.1f}"/>'
        )
    parts.append("</g>")
    parts.append(f'<line class="crosshair" x1="0" y1="{PAD_T}" x2="0" y2="{PAD_T + plot_h}" style="opacity:0"/>')
    parts.append('<circle class="hover-dot" r="5" style="opacity:0"/>')
    parts.append("</svg>")

    tooltip_data = [
        {"t": _fmt_datetime(p["scanned_at"]), "p": format_price(p["price"], currency)}
        for p in points
    ]
    parts.append(
        f'<script type="application/json" class="chart-data">{json.dumps(tooltip_data, ensure_ascii=False)}</script>'
    )
    return "".join(parts)


def _delta_badge(stats: PriceStats) -> str:
    """Degisim rozeti: ok + yuzde + sozel etiket (anlam yalniz renge birakilmaz)."""
    pct = stats.change_percent
    if pct is None:
        return '<span class="badge neutral">ilk olcum</span>'
    if abs(pct) < 0.01:
        return '<span class="badge neutral">→ degisim yok</span>'
    if pct < 0:
        return f'<span class="badge good">↓ %{abs(pct):.1f} ucuzladi</span>'
    return f'<span class="badge bad">↑ %{pct:.1f} zamlandi</span>'


def _stock_badge(in_stock) -> str:
    if in_stock is True:
        return '<span class="badge good">✓ stokta</span>'
    if in_stock is False:
        return '<span class="badge bad">✕ stokta yok</span>'
    return '<span class="badge neutral">? bilinmiyor</span>'


CSS = """
:root{color-scheme:light;
 --surface-1:#fcfcfb; --plane:#f9f9f7;
 --text-primary:#0b0b0b; --text-secondary:#52514e; --muted:#898781;
 --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,.10);
 --series-1:#2a78d6; --series-1-soft:rgba(42,120,214,.13);
 --good:#006300; --good-bg:rgba(12,163,12,.12);
 --bad:#d03b3b;  --bad-bg:rgba(208,59,59,.12);
 --warn:#ec835a;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
 --surface-1:#1a1a19; --plane:#0d0d0d;
 --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
 --series-1:#3987e5; --series-1-soft:rgba(57,135,229,.18);
 --good:#0ca30c; --good-bg:rgba(12,163,12,.16);
 --bad:#e66767; --bad-bg:rgba(230,103,103,.16);
}}
:root[data-theme="dark"]{color-scheme:dark;
 --surface-1:#1a1a19; --plane:#0d0d0d;
 --text-primary:#fff; --text-secondary:#c3c2b7; --muted:#898781;
 --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
 --series-1:#3987e5; --series-1-soft:rgba(57,135,229,.18);
 --good:#0ca30c; --good-bg:rgba(12,163,12,.16);
 --bad:#e66767; --bad-bg:rgba(230,103,103,.16);
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--text-primary);
 font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;
 padding:32px 16px 64px}
.wrap{max-width:980px;margin:0 auto}
header h1{font-size:1.5rem;margin:0 0 4px}
header p{margin:0;color:var(--text-secondary);font-size:.9rem}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.tile .k{font-size:.78rem;color:var(--text-secondary);text-transform:none;margin-bottom:6px}
.tile .v{font-size:1.6rem;font-weight:650;letter-spacing:-.02em}
.tile .v small{font-size:.85rem;font-weight:500;color:var(--text-secondary)}
h2{font-size:1.05rem;margin:32px 0 12px;color:var(--text-secondary);font-weight:600}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
 padding:16px 18px;margin-bottom:14px}
.card-head{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:baseline;justify-content:space-between}
.card-head h3{margin:0;font-size:1rem;font-weight:600;flex:1 1 320px;min-width:0}
.card-head h3 a{color:inherit;text-decoration:none}
.card-head h3 a:hover{text-decoration:underline}
.price{font-size:1.45rem;font-weight:650;letter-spacing:-.02em;white-space:nowrap}
.meta{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;margin-top:8px;
 font-size:.82rem;color:var(--text-secondary)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.78rem;font-weight:550;white-space:nowrap}
.badge.good{background:var(--good-bg);color:var(--good)}
.badge.bad{background:var(--bad-bg);color:var(--bad)}
.badge.neutral{background:var(--border);color:var(--text-secondary)}
.chart-box{position:relative;margin-top:10px}
svg.chart{width:100%;height:auto;display:block;overflow:visible}
.grid{stroke:var(--grid);stroke-width:1}
.target{stroke:var(--muted);stroke-width:1.5;stroke-dasharray:5 4}
.area{fill:var(--series-1-soft);stroke:none}
.line{fill:none;stroke:var(--series-1);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.dot{fill:var(--series-1);stroke:var(--surface-1);stroke-width:2}
.dot.low{fill:var(--good)}
.tick{fill:var(--muted);font-size:11px}
.target-tick{font-size:10px}
.low-label{fill:var(--good);font-weight:600}
.value-label{fill:var(--text-primary);font-size:12.5px;font-weight:650}
.hit{fill:transparent;cursor:crosshair}
.crosshair{stroke:var(--baseline);stroke-width:1;stroke-dasharray:3 3}
.hover-dot{fill:var(--series-1);stroke:var(--surface-1);stroke-width:2}
.tip{position:absolute;pointer-events:none;background:var(--surface-1);color:var(--text-primary);
 border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:.8rem;
 box-shadow:0 4px 14px rgba(0,0,0,.14);opacity:0;transition:opacity .1s;white-space:nowrap;z-index:5}
.empty{color:var(--muted);font-size:.85rem;margin:14px 0 4px}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;
 border:1px solid var(--border);border-radius:12px;background:var(--surface-1)}
table{width:100%;min-width:640px;border-collapse:collapse;font-size:.86rem;
}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-secondary);font-weight:600;font-size:.78rem}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:none}
td a{color:var(--series-1);text-decoration:none}
td:first-child{min-width:190px}
td a:hover{text-decoration:underline}
.fail{border-left:3px solid var(--warn)}
footer{margin-top:36px;color:var(--muted);font-size:.78rem;text-align:center}
@media (max-width:640px){body{padding:20px 12px 48px}.price{font-size:1.2rem}}
"""

JS = """
document.querySelectorAll('.chart-box').forEach(function(box){
  var svg = box.querySelector('svg.chart');
  var dataEl = box.querySelector('.chart-data');
  if(!svg || !dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var tip = document.createElement('div');
  tip.className = 'tip';
  box.appendChild(tip);
  var cross = svg.querySelector('.crosshair');
  var dot = svg.querySelector('.hover-dot');

  function show(rect){
    var i = +rect.dataset.i, x = +rect.dataset.x, y = +rect.dataset.y;
    var d = data[i]; if(!d) return;
    cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.style.opacity = 1;
    dot.setAttribute('cx', x); dot.setAttribute('cy', y); dot.style.opacity = 1;
    tip.innerHTML = '<strong>' + d.p + '</strong><br>' + d.t;
    // SVG koordinatini kutu icindeki piksele cevir
    var box_w = box.clientWidth, vb = svg.viewBox.baseVal;
    var px = x / vb.width * box_w, py = y / vb.height * svg.clientHeight;
    tip.style.opacity = 1;
    var tw = tip.offsetWidth;
    tip.style.left = Math.max(0, Math.min(px - tw/2, box_w - tw)) + 'px';
    tip.style.top = Math.max(0, py - tip.offsetHeight - 12) + 'px';
  }
  function hide(){ tip.style.opacity = 0; cross.style.opacity = 0; dot.style.opacity = 0; }

  svg.querySelectorAll('.hit').forEach(function(r){
    r.addEventListener('mouseenter', function(){ show(r); });
    r.addEventListener('touchstart', function(e){ show(r); }, {passive:true});
  });
  svg.addEventListener('mouseleave', hide);
});
"""


def build_html(rows: list[ScanRow], stats: dict[str, PriceStats],
               series: dict[str, list[dict]], products_by_id: dict,
               generated_at: str, title: str = "Fiyat Takip Raporu") -> str:
    ok_rows = [r for r in rows if r.ok]
    fail_rows = [r for r in rows if not r.ok]

    cheaper = sum(1 for r in ok_rows
                  if (stats.get(r.product_id) and (stats[r.product_id].change_percent or 0) < -0.01))
    # "Zirveden tasarruf": her urunun gordugu en yuksek fiyat ile su anki fiyat farki
    savings = sum(
        float(stats[r.product_id].highest) - float(r.price)
        for r in ok_rows
        if stats.get(r.product_id) and stats[r.product_id].highest is not None
    )
    at_lowest = sum(1 for r in ok_rows if stats.get(r.product_id) and stats[r.product_id].is_lowest_ever)

    subtitle = (f"Son tarama: {_fmt_datetime(generated_at)} · "
                f"{len(ok_rows)}/{len(rows)} urun basariyla okundu")

    out = [
        "<!DOCTYPE html>", '<html lang="tr">', "<head>",
        '<meta charset="utf-8">',
        pwa.head_tags(),
        # Uygulama, taramadan sonra yeni raporun yayinlanip yayinlanmadigini
        # bu damgayi karsilastirarak anlar (GitHub Pages yayini gecikebiliyor).
        f'<meta name="ft-generated" content="{_esc(generated_at)}">',
        f"<title>{_esc(title)}</title>",
        f"<style>{CSS}{pwa.APP_CSS}</style>", "</head>", "<body>",
        pwa.app_bar(),
        '<div class="wrap">',
        pwa.app_strips(_esc(subtitle)),
        '<div class="tiles">',
        f'<div class="tile"><div class="k">Takip edilen</div><div class="v">{len(rows)}</div></div>',
        f'<div class="tile"><div class="k">Ucuzlayan</div><div class="v">{cheaper}</div></div>',
        f'<div class="tile"><div class="k">Rekor dusuk</div><div class="v">{at_lowest}</div></div>',
        f'<div class="tile"><div class="k">Zirveden fark</div>'
        f'<div class="v">{_esc(_short_price(Decimal(str(round(savings, 2))) if savings else Decimal("0")))}</div></div>',
        "</div>",
    ]

    out.append("<h2>Urunler</h2>")
    for row in ok_rows:
        st = stats.get(row.product_id, PriceStats())
        product = products_by_id.get(row.product_id)
        target = getattr(product, "target_price", None)
        points = series.get(row.product_id, [])

        out.append('<article class="card">')
        out.append('<div class="card-head">')
        out.append(f'<h3><a href="{_esc(row.url)}" target="_blank" rel="noopener">{_esc(row.name)}</a></h3>')
        out.append(f'<div class="price">{_esc(format_price(row.price, row.currency))}</div>')
        out.append("</div>")

        out.append('<div class="meta">')
        out.append(_delta_badge(st))
        out.append(_stock_badge(row.in_stock))
        if st.is_lowest_ever:
            out.append('<span class="badge good">🏆 rekor dusuk</span>')
        out.append(f"<span>{_esc(row.site)}</span>")
        if st.lowest is not None and st.points > 1:
            out.append(f"<span>en dusuk {_esc(format_price(st.lowest, row.currency))}</span>")
            out.append(f"<span>en yuksek {_esc(format_price(st.highest, row.currency))}</span>")
        if target is not None:
            out.append(f"<span>hedef {_esc(format_price(target, row.currency))}</span>")
        out.append(f"<span>{st.points} olcum</span>")
        out.append(f'<button class="ft-remove" data-ft-remove="{_esc(row.product_id)}" '
                   f'data-ft-name="{_esc(row.name)}">sil</button>')
        out.append("</div>")

        out.append('<div class="chart-box">')
        out.append(_sparkline(points, row.currency, target))
        out.append("</div></article>")

    if fail_rows:
        out.append("<h2>Okunamayan urunler</h2>")
        for row in fail_rows:
            out.append('<article class="card fail">')
            out.append(
                f'<div class="card-head"><h3><a href="{_esc(row.url)}" target="_blank" '
                f'rel="noopener">{_esc(row.name)}</a></h3>'
                f'<span class="badge bad">{_esc(row.status)}</span></div>'
            )
            out.append(
                f'<div class="meta"><span>{_esc(row.site)}</span><span>{_esc(row.note)}</span>'
                f'<button class="ft-remove" data-ft-remove="{_esc(row.product_id)}" '
                f'data-ft-name="{_esc(row.name)}">sil</button></div>'
            )
            out.append("</article>")

    # Tablo gorunumu: grafigi okuyamayanlar icin ayni veri, sayi olarak
    out.append("<h2>Tablo gorunumu</h2>")
    out.append('<div class="table-wrap"><table><thead><tr>')
    for head, cls in [("Urun", ""), ("Site", ""), ("Guncel", "num"), ("Degisim", "num"),
                      ("En dusuk", "num"), ("En yuksek", "num"), ("Stok", ""), ("Olcum", "num")]:
        out.append(f'<th class="{cls}">{head}</th>')
    out.append("</tr></thead><tbody>")
    for row in rows:
        st = stats.get(row.product_id, PriceStats())
        pct = st.change_percent
        change = "—" if pct is None else f"{pct:+.1f}%"
        stock = {True: "stokta", False: "yok"}.get(row.in_stock, "?")
        price_cell = format_price(row.price, row.currency) if row.ok else row.status
        out.append(
            f'<tr><td><a href="{_esc(row.url)}" target="_blank" rel="noopener">{_esc(row.name)}</a></td>'
            f"<td>{_esc(row.site)}</td>"
            f'<td class="num">{_esc(price_cell)}</td>'
            f'<td class="num">{_esc(change)}</td>'
            f'<td class="num">{_esc(format_price(st.lowest, row.currency) if st.lowest is not None else "—")}</td>'
            f'<td class="num">{_esc(format_price(st.highest, row.currency) if st.highest is not None else "—")}</td>'
            f"<td>{_esc(stock)}</td>"
            f'<td class="num">{st.points}</td></tr>'
        )
    out.append("</tbody></table></div>")

    out.append(
        '<footer>Fiyatlar bilgi amaclidir; alisveristen once urun sayfasindan dogrulayin.<br>'
        "Bu rapor her taramada otomatik olarak yeniden uretilir.</footer>"
    )
    out.append("</div>")
    out.append(pwa.sheets())
    out.append(f"<script>{JS}</script>")
    out.append('<script src="app.js" defer></script>')
    out.append("</body></html>")
    return "\n".join(out)


def write_report(rows, stats, series, products_by_id, generated_at,
                 path: Path = REPORT_PATH, title: str = "Fiyat Takip Raporu") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(rows, stats, series, products_by_id, generated_at, title),
                    encoding="utf-8")
    # manifest.webmanifest / sw.js / app.js her raporla birlikte tazelenir
    pwa.write_assets(path.parent)
    return path
