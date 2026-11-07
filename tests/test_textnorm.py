from app.textnorm import normalize_text


def test_lookalikes_become_plain_ascii():
    assert normalize_text("ORD‑1034 and TXN‐8922​") == "ORD-1034 and TXN-8922"


def test_real_dashes_are_kept():
    assert normalize_text("9:00 – 18:00 — weekdays") == "9:00 – 18:00 — weekdays"
