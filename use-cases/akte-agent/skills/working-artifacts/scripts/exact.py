"""Exact string-based decimal arithmetic; portable to standalone skill exports."""

import re
from decimal import ROUND_HALF_UP, Decimal, Inexact, localcontext


def parse_decimal(value: str, decimal_separator: str | None = None) -> Decimal:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("Supply a decimal string of at most 80 characters, not a float")
    if decimal_separator not in (None, ".", ","):
        raise ValueError("decimal_separator must be '.' or ','")
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", value, flags=re.ASCII):
        raise ValueError(f"Invalid decimal: {value!r}; clarify units, separators or notation")
    separator = "," if "," in value else "." if "." in value else None
    if separator and decimal_separator and separator != decimal_separator:
        raise ValueError("Decimal separator conflicts with the confirmed format")
    if separator and decimal_separator is None:
        whole, fraction = value.split(separator)
        if int(whole) != 0 and len(fraction) == 3:
            raise ValueError(f"Ambiguous decimal/grouping: {value!r}; confirm decimal_separator")
    return Decimal(value.replace(",", "."))


def exact_sum(values: list[Decimal]) -> Decimal:
    with localcontext() as context:
        context.prec = 256
        context.traps[Inexact] = True
        return sum(values, Decimal("0"))


def exact_product(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 256
        context.traps[Inexact] = True
        return left * right


def format_decimal(value: Decimal, locale: str, *, trim: bool = False) -> str:
    if locale not in ("en", "nl"):
        raise ValueError("locale must be en or nl")
    text = format(value, "f")
    if trim and "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",") if locale == "nl" else text


def currency(value: Decimal, locale: str, *, rounding: str) -> str:
    if rounding != ROUND_HALF_UP:
        raise ValueError("Supported explicit currency rule: ROUND_HALF_UP")
    with localcontext() as context:
        context.prec = 256
        rounded = value.quantize(Decimal("0.01"), rounding=rounding)
    return format_decimal(rounded, locale)
