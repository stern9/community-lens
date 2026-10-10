"""Fechas en español para los textos que arma el código (periódico y reporte). Sin dependencias del resto."""
from __future__ import annotations

from datetime import datetime

MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre"]
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]  # índice de datetime.weekday()


def month_label(ym: str) -> str:
    """'2026-03' → 'mar-26'."""
    return f"{MONTHS[int(ym[5:7]) - 1][:3]}-{ym[2:4]}"


def fmt_day(day: str, year: bool = False) -> str:
    """'2026-10-08' → '8 de octubre' (o '8 de octubre de 2026')."""
    d = datetime.strptime(day[:10], "%Y-%m-%d")
    return f"{d.day} de {MONTHS[d.month - 1]}" + (f" de {d.year}" if year else "")


def short_day(day: str) -> str:
    """'2026-10-08' → '8 oct'."""
    if not day:
        return ""
    number, month = fmt_day(day).split(" de ")
    return f"{number} {month[:3]}"


def long_day(d: datetime) -> str:
    """'Jueves 8 de octubre de 2026'."""
    return f"{WEEKDAYS[d.weekday()].capitalize()} {fmt_day(d.strftime('%Y-%m-%d'), True)}"


def range_label(d_from: str, d_to: str) -> str:
    a, b = datetime.strptime(d_from, "%Y-%m-%d"), datetime.strptime(d_to, "%Y-%m-%d")
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day} al {b.day} de {MONTHS[b.month - 1]}" if a.day != b.day else fmt_day(d_to)
    return f"{fmt_day(d_from, a.year != b.year)} al {fmt_day(d_to, a.year != b.year)}"
