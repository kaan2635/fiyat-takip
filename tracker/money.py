"""Fiyat metinlerini sayiya cevirme.

Turk e-ticaret siteleri "1.299,90 TL" formatini kullanir; JSON-LD gibi
makine okunur alanlar ise "1299.90" verir. Iki formati da dogru cozmek
gerekiyor, cunku "1.299" TR'de 1299, makine formatinda 1.299'dur.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Fiyat metninde gecebilecek para birimi isaretleri ve kodlari
_CURRENCY_TOKENS = {
    "TRY": "TRY", "TL": "TRY", "₺": "TRY",
    "USD": "USD", "$": "USD",
    "EUR": "EUR", "€": "EUR",
    "GBP": "GBP", "£": "GBP",
}

# Sayiya ait olmayan her sey (bosluklar, harfler, semboller) temizlenir
_NON_NUMERIC = re.compile(r"[^\d.,]")
# Ayraclarla bolunmus rakam gruplarindan olusan en genis blogu yakalar:
# 1.299,90 / 1,299.90 / 1 299,90 / 1299,90 / 24.999 / 899
_PRICE_BLOCK = re.compile(r"\d+(?:[.,\s]\d+)*")
_HAS_SEPARATOR = re.compile(r"[.,\s]")


def detect_currency(text: str, default: str = "TRY") -> str:
    """Fiyat metninden para birimini cikarir, bulamazsa default doner."""
    if not text:
        return default
    upper = text.upper()
    # Once cok karakterli kodlar ("TRY"), sonra sembollerle esles ki
    # "TRY" icindeki "TL" olmayan bir esleme yanlis sonuc vermesin.
    for token in sorted(_CURRENCY_TOKENS, key=len, reverse=True):
        if token in upper:
            return _CURRENCY_TOKENS[token]
    return default


def _to_decimal(cleaned: str) -> Decimal | None:
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def parse_machine_price(value) -> Decimal | None:
    """JSON-LD / meta etiketi gibi makine formatindaki fiyati cevirir.

    Bu alanlarda ondalik ayraci her zaman noktadir, virgul binlik ayracidir.
    """
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        return _to_decimal(Decimal(str(value)))

    text = str(value).strip()
    if not text:
        return None

    cleaned = _NON_NUMERIC.sub("", text)
    if not cleaned:
        return None

    # Bazi siteler makine alanina da TR formati yazar ("1.299,90").
    # Virgulden sonra 1-2 hane varsa bu TR ondaligidir, oyle cozeriz.
    if "," in cleaned:
        after_comma = cleaned.rsplit(",", 1)[1]
        if len(after_comma) in (1, 2) and after_comma.isdigit():
            return _to_decimal(cleaned.replace(".", "").replace(",", "."))
        cleaned = cleaned.replace(",", "")

    return _to_decimal(cleaned)


def parse_price(text, locale: str = "tr") -> Decimal | None:
    """Insan okunur fiyat metnini ("1.299,90 TL") Decimal'e cevirir."""
    if text is None:
        return None
    if isinstance(text, (int, float, Decimal)):
        return _to_decimal(Decimal(str(text)))

    raw = str(text).replace("\xa0", " ").strip()
    if not raw:
        return None

    candidates = _PRICE_BLOCK.findall(raw)
    if not candidates:
        return None

    # "Sepette 2 adet 1.299,90 TL" gibi metinlerde bastaki "2" fiyat degil.
    # Ayrac iceren ilk aday fiyattir; hicbirinde ayrac yoksa en uzun rakam dizisi.
    separated = [c for c in candidates if _HAS_SEPARATOR.search(c)]
    best = separated[0] if separated else max(candidates, key=len)

    cleaned = _NON_NUMERIC.sub("", best)
    if not cleaned:
        return None

    has_dot, has_comma = "." in cleaned, "," in cleaned

    if has_dot and has_comma:
        # En sagdaki ayrac ondalik ayracidir: "1.299,90" -> virgul, "1,299.90" -> nokta
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        after = cleaned.rsplit(",", 1)[1]
        # "1299,90" -> ondalik; "1,299" TR'de binlik ayracidir
        cleaned = cleaned.replace(",", ".") if len(after) in (1, 2) else cleaned.replace(",", "")
    elif has_dot:
        parts = cleaned.split(".")
        after = parts[-1]
        # Tek nokta + 3 hane TR'de binliktir ("1.299"); 1-2 hane ondaliktir ("24.99")
        if len(parts) > 2 or len(after) == 3:
            cleaned = cleaned.replace(".", "")
        elif len(after) > 3:
            cleaned = cleaned.replace(".", "")

    return _to_decimal(cleaned)


def format_price(value, currency: str = "TRY") -> str:
    """Decimal fiyati TR formatinda gosterir: 1299.9 -> '1.299,90 TL'."""
    if value is None:
        return "-"
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    whole, _, frac = f"{amount:.2f}".partition(".")
    negative = whole.startswith("-")
    whole = whole.lstrip("-")
    grouped = f"{int(whole):,}".replace(",", ".")
    symbol = {"TRY": "TL", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency)
    sign = "-" if negative else ""
    return f"{sign}{grouped},{frac} {symbol}"
