"""Clean up look-alike characters that models (gpt-oss especially) like to emit.

"ORD‑1034" with a non-breaking hyphen (U+2011) looks identical to "ORD-1034" but doesn't match it:
database lookups fail, copied IDs are wrong and citation chips don't render. Replace them with plain
ASCII. Real dashes (– and —) are punctuation and are left alone.
"""
_FIXES = str.maketrans({
    "‐": "-",   # hyphen
    "‑": "-",   # non-breaking hyphen
    "‒": "-",   # figure dash
    "−": "-",   # minus sign
    " ": " ",   # no-break space
    " ": " ",   # narrow no-break space
    " ": " ",   # figure space
    " ": " ",   # thin space
    "​": "",    # zero-width space
    "﻿": "",    # byte-order mark
})


def normalize_text(text: str) -> str:
    return text.translate(_FIXES)
