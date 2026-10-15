"""Gecici olcum: aday pazaryerlerinin arama sayfalari Actions IP'sinden aciliyor mu?"""

import gzip
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, ".")
from tracker.fetch import Fetcher, page_title  # noqa: E402

OUT = Path("probe-out")
OUT.mkdir(exist_ok=True)

SITES = {
    "ebebek": "https://www.e-bebek.com/search?text={q}",
    "pazarama": "https://www.pazarama.com/arama?q={q}",
    "amazon": "https://www.amazon.com.tr/s?k={q}",
    "teknosa": "https://www.teknosa.com/arama/?s={q}",
    "mediamarkt": "https://www.mediamarkt.com.tr/tr/search.html?query={q}",
    "vatan": "https://www.vatanbilgisayar.com/arama/{q}/",
    "ciceksepeti": "https://www.ciceksepeti.com/arama?query={q}",
    "idefix": "https://www.idefix.com/arama?q={q}",
    "akakce": "https://www.akakce.com/arama/?q={q}",
    "cimri": "https://www.cimri.com/arama?q={q}",
    "n11": "https://www.n11.com/arama?q={q}",
    "koctas": "https://www.koctas.com.tr/search?q={q}",
}
QUERIES = ["maxi cosi mica", "iphone 15 128 gb"]

results = []
fetcher = Fetcher(timeout=25, min_delay=1.0)
for site, tpl in SITES.items():
    for q in QUERIES:
        url = tpl.format(q=quote_plus(q) if "{q}/" not in tpl else q.replace(" ", "-"))
        r = fetcher.get(url)
        name = f"{site}__{q.replace(' ', '_')}"
        if r.html:
            (OUT / f"{name}.html.gz").write_bytes(gzip.compress(r.html.encode("utf-8", "replace")))
        rec = {
            "site": site, "query": q, "url": url, "final_url": r.final_url,
            "status": r.status, "bytes": len(r.html or ""), "title": page_title(r.html or "")[:120],
            "ok": r.ok, "blocked": r.blocked, "reason": r.block_reason, "error": r.error,
            "attempts": r.attempts, "profile": r.profile,
            "has_jsonld": "application/ld+json" in (r.html or ""),
            "next_data": "__NEXT_DATA__" in (r.html or ""),
        }
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
fetcher.close()
(OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
