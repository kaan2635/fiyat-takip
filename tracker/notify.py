"""Telegram bildirimleri.

Bot token ve sohbet kimligi ortam degiskenlerinden okunur; ikisi de yoksa
bildirim sessizce atlanir, tarama yine de calisir.
"""

from __future__ import annotations

import html
import logging
import os

import requests

from .money import format_price
from .scan import Alert, ScanReport

log = logging.getLogger("tracker.notify")

API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 3800   # Telegram siniri 4096; bicimlendirme icin pay birakiyoruz

ICONS = {
    "target": "\U0001F3AF",   # hedef
    "drop": "\U0001F53B",     # asagi ucgen
    "lowest": "\U0001F3C6",   # kupa
    "stock": "\U0001F4E6",    # koli
}


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def send(text: str, disable_preview: bool = True) -> bool:
    """Tek bir mesaj gonderir. Basarisizlik taramayi bozmaz, sadece loglanir."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.info("Telegram yapilandirilmamis, bildirim atlandi")
        return False

    try:
        resp = requests.post(
            API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": text[:MAX_LEN],
                "parse_mode": "HTML",
                "disable_web_page_preview": disable_preview,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log.warning("Telegram hatasi %s: %s", resp.status_code, resp.text[:200])
            return False
        return True
    except requests.RequestException as exc:
        log.warning("Telegram gonderilemedi: %s", exc)
        return False


def format_alert(alert: Alert) -> str:
    row, stats = alert.row, alert.stats
    icon = ICONS.get(alert.kind, "❗")
    lines = [
        f"{icon} <b>{_esc(row.name)}</b>",
        f"<i>{_esc(alert.message)}</i>",
        f"Fiyat: <b>{_esc(format_price(row.price, row.currency))}</b>",
    ]
    if stats.previous is not None:
        pct = stats.change_percent
        arrow = "↓" if (pct or 0) < 0 else "↑"
        lines.append(
            f"Onceki: {_esc(format_price(stats.previous, row.currency))} "
            f"({arrow}{abs(pct or 0):.1f}%)"
        )
    if stats.lowest is not None and stats.points > 1:
        lines.append(f"En dusuk: {_esc(format_price(stats.lowest, row.currency))}")
    if alert.product.target_price is not None:
        lines.append(f"Hedef: {_esc(format_price(alert.product.target_price, row.currency))}")
    lines.append(f'<a href="{_esc(row.url)}">{_esc(row.site)} → urune git</a>')
    return "\n".join(lines)


def format_summary(report: ScanReport, report_url: str | None = None) -> str:
    ok, failed = report.ok_rows, report.failed_rows
    lines = [f"\U0001F4CA <b>Tarama tamamlandi</b> — {len(ok)}/{len(report.rows)} urun okundu"]

    for row in sorted(ok, key=lambda r: (report.stats.get(r.product_id).change_percent or 0)
                      if report.stats.get(r.product_id) else 0):
        st = report.stats.get(row.product_id)
        pct = st.change_percent if st else None
        if pct is None or abs(pct) < 0.01:
            mark = "→"
            delta = ""
        elif pct < 0:
            mark, delta = "\U0001F53B", f" ({pct:+.1f}%)"
        else:
            mark, delta = "\U0001F53A", f" ({pct:+.1f}%)"
        stock = "" if row.in_stock is not False else " ⚠️ stokta yok"
        lines.append(
            f'{mark} <a href="{_esc(row.url)}">{_esc(row.name[:48])}</a>: '
            f"<b>{_esc(format_price(row.price, row.currency))}</b>{delta}{stock}"
        )

    if failed:
        lines.append(f"\n⚠️ <b>Okunamayan {len(failed)} urun</b>")
        for row in failed[:8]:
            lines.append(f"• {_esc(row.name[:40])} — {_esc(row.status)}: {_esc(row.note[:60])}")

    if report_url:
        lines.append(f'\n<a href="{_esc(report_url)}">Ayrintili rapor</a>')
    return "\n".join(lines)


def notify(report: ScanReport, report_url: str | None = None,
           summary: bool = True) -> int:
    """Uyarilari ve istege bagli ozeti gonderir; gonderilen mesaj sayisini doner."""
    if not configured():
        return 0

    sent = 0
    # Ayni urun icin birden fazla uyari varsa en onemlisini gonder
    priority = {"target": 0, "lowest": 1, "drop": 2, "stock": 3}
    best: dict[str, Alert] = {}
    for alert in report.alerts:
        key = alert.product.id
        if key not in best or priority.get(alert.kind, 9) < priority.get(best[key].kind, 9):
            best[key] = alert

    for alert in best.values():
        if send(format_alert(alert), disable_preview=False):
            sent += 1

    if summary and report.rows:
        if send(format_summary(report, report_url)):
            sent += 1
    return sent
