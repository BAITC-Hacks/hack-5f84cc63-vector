"""Conservative source parsing: no imputation or quantity sign changes."""
from datetime import date, datetime
import math
import re

MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "июн": 6,
          "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
TOTAL = re.compile(r"^(?:итого|всего|total|subtotal)(?:\s|:|$)", re.I)


def text(value):
    return "" if value is None else str(value).replace("\xa0", " ").strip()


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def number(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, ["missing_value"]
    if not is_number(value):
        return None, ["excel_error" if text(value).startswith("#") else "invalid_numeric_value"]
    return value, []


def identifier(value):
    if isinstance(value, str) and text(value) and not text(value).startswith("#"):
        return text(value), []
    if is_number(value) and float(value).is_integer():
        return str(int(value)), ["numeric_identifier_requires_review"]
    return None, ["invalid_identifier"]


def timestamp(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time()).isoformat()
    if isinstance(value, str):
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value.strip(), fmt).isoformat()
            except ValueError:
                pass
    return None


def month(value):
    match = re.fullmatch(r"([а-яё]+)\.?\s+(20\d{2})", text(value).lower())
    if match and match[1][:3] in MONTHS:
        return f"{match[2]}-{MONTHS[match[1][:3]]:02d}"
    return None


def raw_value(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else value
