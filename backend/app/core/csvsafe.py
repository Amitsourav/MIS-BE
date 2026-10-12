"""CSV helpers for files partners open in Excel.

- Text cells starting with = + - @ (or a tab/CR) are formulas to Excel; a lead
  name typed into a web form could run one. Prefix them with ' so they show
  as plain text.
- A UTF-8 byte-order mark makes Excel read Hindi/accented names correctly.
"""
from __future__ import annotations

import csv
import io
from typing import Iterable

from fastapi.responses import StreamingResponse

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: object) -> object:
    """Neutralise a free-text cell (names, banks, sources). Numbers untouched."""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def csv_response(header: list[str], rows: Iterable[list], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(rows)
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
