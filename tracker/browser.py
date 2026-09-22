"""Playwright tabanli yedek indirici.

HTTP katmani engellendiginde devreye girer. Gercek Chromium calistirdigi icin
JavaScript sorgulamalarini (Cloudflare, Amazon captcha oncesi kontroller)
gecebilir; buna karsilik yavastir ve daha cok kaynak tuketir. Bu yuzden
varsayilan degil, yalnizca gerektiginde veya urun `mode: browser` dediginde
kullanilir.

Playwright kurulu degilse modul sessizce devre disi kalir; sistem yalnizca
HTTP katmaniyla calismaya devam eder.
"""

from __future__ import annotations

import logging
import os

from .fetch import FetchResult, cookies_for, detect_block

log = logging.getLogger("tracker.browser")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Otomasyon izlerini silen kucuk yamalar; sitelerin "headless mi?" testlerini gecer
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['tr-TR', 'tr', 'en-US']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""


def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


class BrowserFetcher:
    """Tek bir Chromium ornegini acik tutup sayfalari sirayla gezer."""

    def __init__(self, timeout: int = 45_000, wait_ms: int = 3_000, headless: bool = True):
        self.timeout = timeout
        self.wait_ms = wait_ms
        self.headless = headless
        self._pw = None
        self._browser = None
        self._ctx = None

    def _ensure(self) -> None:
        if self._ctx is not None:
            return
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch_kwargs = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        # Ozel bir Chromium yolu verildiyse onu kullan (CI imajlarinda faydali)
        exe = os.environ.get("TRACKER_CHROME_PATH")
        if exe:
            launch_kwargs["executable_path"] = exe

        self._browser = self._pw.chromium.launch(**launch_kwargs)
        self._ctx = self._browser.new_context(
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
            viewport={"width": 1440, "height": 900},
            user_agent=UA,
            extra_http_headers={"Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8"},
        )
        self._ctx.add_init_script(STEALTH_JS)

    def get(self, url: str) -> FetchResult:
        try:
            self._ensure()
        except Exception as exc:
            return FetchResult(url=url, error=f"tarayici baslatilamadi: {exc}", backend="browser")

        cookies = cookies_for(url)
        if cookies:
            from urllib.parse import urlparse
            domain = "." + (urlparse(url).netloc or "").lstrip("www.")
            self._ctx.add_cookies(
                [{"name": k, "value": v, "domain": domain, "path": "/"} for k, v in cookies.items()]
            )

        page = self._ctx.new_page()
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            page.wait_for_timeout(self.wait_ms)
            html = page.content()
            status = resp.status if resp else 200
            final_url = page.url
            reason = detect_block(status, html, url, final_url)
            return FetchResult(
                url=url, final_url=final_url, status=status, html=html,
                blocked=reason is not None, block_reason=reason,
                backend="browser", profile="chromium",
            )
        except Exception as exc:
            return FetchResult(url=url, error=f"{type(exc).__name__}: {exc}", backend="browser")
        finally:
            page.close()

    def close(self) -> None:
        for obj, stop in ((self._ctx, "close"), (self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, stop)()
                except Exception:
                    pass
        self._ctx = self._browser = self._pw = None
