"""Telefondaki uygulamanin (PWA) docs/ klasorune yazilmasi.

Uygulamanin kendisi `tracker/web/` altindaki duz dosyalardir (HTML, CSS, JS).
Her taramada bunlar docs/ altina kopyalanir ve guncel veri paketi hem
docs/data.json olarak hem de index.html'in icine gomulu olarak yazilir:

- Gomulu kopya: sayfa ilk acilista ek istek yapmadan, cevrimdisiyken ve
  dosya cift tiklanarak acildiginda bile calissin diye.
- data.json: uygulama tarama bittikten sonra en guncel paketi GitHub API'den
  bu yoldan okur; Pages yayinini beklemesi gerekmez.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import Config
from .store import HISTORY_PATH, LATEST_PATH, load_latest, now_iso, series_by_product
from .webdata import build_payload

REPORT_PATH = Path("docs/index.html")
WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = ("app.js", "app.css", "manifest.webmanifest")


def _embed(data: dict) -> str:
    """JSON'u <script> icine guvenle gommek icin '<' karakterlerini kacirir."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def asset_version() -> str:
    """Uygulama dosyalarinin ozeti; degisince telefondaki onbellek yenilenir."""
    h = hashlib.sha1()
    for name in STATIC_FILES + ("index.html", "sw.js"):
        h.update((WEB_DIR / name).read_bytes())
    return h.hexdigest()[:10]


def write_app(cfg: Config, path: Path = REPORT_PATH, latest_path: Path = LATEST_PATH,
              history_path: Path = HISTORY_PATH, generated_at: str | None = None,
              repo: str | None = None) -> Path:
    """docs/ klasorune uygulamayi ve guncel veri paketini yazar."""
    path = Path(path)
    docs = path.parent
    docs.mkdir(parents=True, exist_ok=True)

    payload = build_payload(cfg, load_latest(latest_path), series_by_product(history_path),
                            generated_at or now_iso(), repo)
    version = asset_version()

    (docs / "data.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                                    encoding="utf-8")
    for name in STATIC_FILES:
        (docs / name).write_bytes((WEB_DIR / name).read_bytes())
    sw = (WEB_DIR / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", version)
    (docs / "sw.js").write_text(sw, encoding="utf-8")

    page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    page = (page.replace("__VERSION__", version)
                .replace("__GENERATED__", str(payload.get("generated_at") or ""))
                .replace("__DATA__", _embed(payload)))
    path.write_text(page, encoding="utf-8")

    # GitHub Pages varsayilan olarak Jekyll calistirir; dosyalarin dokunulmadan
    # yayinlanmasi icin bunu kapatiyoruz.
    (docs / ".nojekyll").write_text("", encoding="utf-8")
    return path


def write_report(*args, **kwargs) -> Path:  # pragma: no cover - eski ad
    """Eski adla cagiranlar icin: artik `write_app(cfg, ...)` kullanilir."""
    return write_app(*args, **kwargs)
