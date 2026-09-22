"""HTTP katmani.

Turk pazaryerlerinin bot korumasi duz `requests` isteklerini TLS parmak
izinden taniyip 403 donuyor. curl_cffi gercek tarayici TLS parmak izini
taklit edebildigi icin birincil yontem odur; kurulu degilse `requests`'e
dusulur (o zaman bazi siteler engelleyebilir).

Tek bir tarayici profili her siteyi acmaz -- olculen davranis: Trendyol
chrome116 ile aciliyor ama chrome131 ulke kapisina atiyor, Hepsiburada
chrome120'yi reddedip chrome116/edge101/safari17_0'i kabul ediyor. Bu yuzden
profiller sirayla denenir ve bir site icin calisan profil o tarama boyunca
hatirlanir.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

log = logging.getLogger("tracker.fetch")

try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except ImportError:  # pragma: no cover - ortama bagli
    curl_requests = None
    HAS_CURL_CFFI = False

import requests as plain_requests

# Denenecek tarayici profilleri; sirasi olcumlere gore secildi
IMPERSONATE_PROFILES = ["chrome116", "edge101", "safari17_0", "chrome131", "chrome120"]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}

# Trendyol yurt disi IP'lerde urun sayfasi yerine ulke secim ekrani gosteriyor;
# magaza cerezleri bunu Turkiye vitrinine sabitliyor.
SITE_COOKIES: dict[str, dict[str, str]] = {
    "trendyol.com": {
        "storefrontId": "1",
        "countryCode": "TR",
        "language": "tr",
        "LanguageAndCulture": "tr-TR",
        "international-country-code": "TR",
        "international-language": "tr",
    },
}

# Bot korumasi / captcha / kapi sayfalarinin imzalari
_BLOCK_TITLE = re.compile(
    r"g[uü]venlik|attention required|cloudflare|just a moment|robot|captcha|"
    r"access denied|erisim engellendi|are you a human|bot detection",
    re.IGNORECASE,
)
_BLOCK_PATH = re.compile(
    r"/select-country|/captcha|/errors?/|/blocked|/security|/challenge|"
    r"/sorry|validateCaptcha|/ap/signin",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Urun sayfalari genelde 50 KB'in uzerindedir; bunun altindakiler supheli
MIN_PRODUCT_BYTES = 20_000


def host_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def cookies_for(url: str) -> dict[str, str]:
    host = host_of(url)
    for domain, cookies in SITE_COOKIES.items():
        if host == domain or host.endswith("." + domain):
            return dict(cookies)
    return {}


def page_title(html: str) -> str:
    m = _TITLE_RE.search(html or "")
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    status: int = 0
    html: str = ""
    error: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    profile: str | None = None
    backend: str = "http"
    attempts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and not self.blocked and 200 <= self.status < 300 and bool(self.html)


def detect_block(status: int, html: str, url: str, final_url: str) -> str | None:
    """Sayfanin gercek urun sayfasi mi yoksa bot duvari mi oldugunu soyler.

    Yanlis fiyat kaydetmektense "engellendi" demek her zaman daha iyidir;
    bu yuzden supheli her durumda engel sayariz.
    """
    if status in (401, 403, 429) or status >= 500:
        return f"HTTP {status}"

    title = page_title(html)
    if _BLOCK_TITLE.search(title):
        return f"bot korumasi sayfasi: {title[:60]!r}"

    # Urun sayfasindan baska bir yere atildiysak (ulke kapisi, giris, captcha)
    if final_url and final_url != url:
        if _BLOCK_PATH.search(final_url):
            return f"yonlendirme: {final_url[:80]}"
        # Yol tamamen degistiyse ve ana sayfaya dustuyse de supheli
        if urlparse(final_url).path.strip("/") in ("", "tr"):
            return f"ana sayfaya yonlendirildi: {final_url[:80]}"

    if html and len(html) < MIN_PRODUCT_BYTES:
        return f"sayfa cok kucuk ({len(html)} bayt) - muhtemelen engel sayfasi"

    return None


class Fetcher:
    """Alan adi basina hiz sinirli, profil deneyen indirici."""

    def __init__(self, timeout: int = 30, retries: int = 2, min_delay: float = 2.0,
                 profiles: list[str] | None = None):
        self.timeout = timeout
        self.retries = retries
        self.min_delay = min_delay
        self.profiles = profiles or IMPERSONATE_PROFILES
        self._last_hit: dict[str, float] = {}
        self._good_profile: dict[str, str] = {}   # host -> calistigi bilinen profil
        self._sessions: dict[str, object] = {}

    # ------------------------------------------------------------------ dahili
    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait + random.uniform(0, 0.5))
        self._last_hit[host] = time.monotonic()

    def _session(self, profile: str):
        if profile not in self._sessions:
            if HAS_CURL_CFFI:
                self._sessions[profile] = curl_requests.Session(
                    impersonate=profile, headers=dict(BASE_HEADERS)
                )
            else:
                s = plain_requests.Session()
                s.headers.update({**BASE_HEADERS, "User-Agent": DEFAULT_UA})
                self._sessions[profile] = s
        return self._sessions[profile]

    def _try_once(self, url: str, profile: str) -> FetchResult:
        session = self._session(profile)
        host = host_of(url)
        self._throttle(host)
        headers = {"Referer": f"https://{urlparse(url).netloc}/"}
        try:
            resp = session.get(
                url, timeout=self.timeout, headers=headers,
                cookies=cookies_for(url), allow_redirects=True,
            )
        except Exception as exc:
            return FetchResult(url=url, error=f"{type(exc).__name__}: {exc}", profile=profile)

        html = resp.text or ""
        final_url = str(resp.url)
        reason = detect_block(resp.status_code, html, url, final_url)
        return FetchResult(
            url=url, final_url=final_url, status=resp.status_code, html=html,
            blocked=reason is not None, block_reason=reason, profile=profile,
        )

    # ------------------------------------------------------------------- genel
    def get(self, url: str) -> FetchResult:
        """Profilleri sirayla deneyerek sayfayi indirir.

        Bir site icin daha once calisan profil varsa once o denenir; boylece
        ikinci taramadan itibaren tek istekte sonuc alinir.
        """
        host = host_of(url)
        order = list(self.profiles)
        known = self._good_profile.get(host)
        if known:
            order.remove(known)
            order.insert(0, known)

        last = FetchResult(url=url, error="hic deneme yapilmadi")
        attempts: list[str] = []

        for profile in order:
            result = self._try_once(url, profile)
            attempts.append(f"{profile}:{result.status or result.error}")
            if result.ok:
                self._good_profile[host] = profile
                result.attempts = attempts
                return result
            last = result
            # Ag hatasinda kisa bir nefes al, engelde direkt sonraki profile gec
            if result.error:
                time.sleep(1.5)

        last.attempts = attempts
        if not last.block_reason and last.error:
            last.block_reason = last.error
        return last

    def close(self) -> None:
        for s in self._sessions.values():
            try:
                s.close()
            except Exception:
                pass
        self._sessions.clear()
