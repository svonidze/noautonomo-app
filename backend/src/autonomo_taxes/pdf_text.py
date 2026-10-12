from __future__ import annotations

import math
from pathlib import Path


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def readable_text(path: Path) -> tuple[str, str | None]:
    try:
        return extract_pdf_text(path), None
    except Exception as exc:  # PDF extraction failures are audit data.
        return "", f"{type(exc).__name__}: {exc}"


def effective_font_size(font_size: float, tm, cm=None) -> float:
    """Rendered text height of a pypdf text fragment.

    AEAT receipts downloaded from Sede set the font size operand to 1 and put
    the real size (9pt) in the text matrix scale, while provider-generated
    receipts use size 9 with an identity matrix. Size filters must compare the
    rendered height, otherwise every amount on a Sede receipt looks like 1pt
    text. The height is the vertical scale of tm x cm, so condensed glyphs and
    a scaled CTM are measured correctly.
    """
    b, d = float(tm[2]), float(tm[3])
    if cm is not None:
        b, d = b * float(cm[0]) + d * float(cm[2]), b * float(cm[1]) + d * float(cm[3])
    return float(font_size) * math.hypot(b, d)
