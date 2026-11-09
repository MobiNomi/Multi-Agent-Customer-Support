"""Hide sensitive data in customer messages before they reach the AI, the logs or the database.

Rules, not AI: card numbers are confirmed with the Luhn checksum every real card passes, so order
numbers, phone numbers and amounts are left alone.
"""
import re
from typing import List, Tuple

_CNIC_DASHED = re.compile(r"(?<!\d)\d{5}-\d{7}-\d(?!\d)")
_DIGIT_RUN = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
_CVV = re.compile(r"\b(cvv2?|cvc2?|security code|card code)\b(\s*(?:is|:|=|-)?\s*)(\d{3,4})\b", re.I)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")
_PASSWORD = re.compile(r"\b(password|passwd|pwd|passcode|pin code|pin)\b(\s*(?:is|was|:|=)\s*)([\"']?)(\S{3,})",
                       re.I)
# "my password is wrong" is a problem description, not a password.
_NOT_SECRETS = frozenset("""not wrong incorrect invalid expired correct working broken locked reset changed
still also now being too very the my a an same old new weak strong blank empty missing required forgotten
""".split())


def luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for ch in reversed(digits):
        d = int(ch) * 2 if double else int(ch)
        total += d - 9 if d > 9 else d
        double = not double
    return total % 10 == 0


def mask_sensitive(text: str) -> Tuple[str, List[str]]:
    """Return (masked text, kinds hidden), e.g. ("my card is [card ending 1111]", ["card_masked"])."""
    kinds: List[str] = []

    def cnic(_m):
        kinds.append("cnic_masked")
        return "[CNIC hidden]"

    def digits(m):
        raw = re.sub(r"\D", "", m.group(0))
        if 13 <= len(raw) <= 19 and luhn_ok(raw):
            kinds.append("card_masked")
            return f"[card ending {raw[-4:]}]"
        if len(raw) == 13:                       # CNIC typed without dashes
            kinds.append("cnic_masked")
            return "[CNIC hidden]"
        return m.group(0)

    def cvv(m):
        kinds.append("cvv_masked")
        return f"{m.group(1)}{m.group(2)}[hidden]"

    def iban(m):
        compact = m.group(0).replace(" ", "")
        if len(compact) < 15 or not any(c.isdigit() for c in compact[4:]):
            return m.group(0)
        kinds.append("iban_masked")
        return "[IBAN hidden]"

    def password(m):
        value = m.group(4).strip(".,!?\"'")
        if value.lower() in _NOT_SECRETS:
            return m.group(0)
        kinds.append("password_masked")
        return f"{m.group(1)}{m.group(2)}[hidden]"

    text = _CNIC_DASHED.sub(cnic, text)
    text = _IBAN.sub(iban, text)
    text = _DIGIT_RUN.sub(digits, text)
    text = _CVV.sub(cvv, text)
    text = _PASSWORD.sub(password, text)
    return text, kinds
