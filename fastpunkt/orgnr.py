"""Norwegian organisation numbers: validation (MOD11) and the textual forms used on websites."""
import re

WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def normalise(raw) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def is_valid(orgnr: str) -> bool:
    """Brønnøysund MOD11 check digit."""
    if not re.fullmatch(r"\d{9}", orgnr or ""):
        return False
    s = sum(int(d) * w for d, w in zip(orgnr[:8], WEIGHTS))
    check = 11 - s % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(orgnr[8])


def mentions(text: str, orgnr: str) -> str | None:
    """Return the exact span if `text` contains the organisation number in a common written form
    (123456789, 123 456 789, 123.456.789, NO 123 456 789 MVA). Digits must not be part of a longer number."""
    if not text or not orgnr:
        return None
    a, b, c = orgnr[:3], orgnr[3:6], orgnr[6:]
    pat = re.compile(rf"(?<!\d){a}[\s.\u00a0-]?{b}[\s.\u00a0-]?{c}(?!\d)")
    m = pat.search(text)
    if not m:
        return None
    lo, hi = max(0, m.start() - 60), min(len(text), m.end() + 20)
    return " ".join(text[lo:hi].split())
