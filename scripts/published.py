import re
from decimal import ROUND_HALF_UP, Decimal


def excel_decimal(value):
    """The decimal number Excel holds for a float cell: Excel keeps 15 significant digits."""
    return Decimal(f"{value:.15g}")


def display(value, digits, exponent=False):
    """`value` as Excel displays it with `digits` decimals: the 15-digit decimal rounded half away from zero.

    Python's own formatting rounds the binary float instead, so a cell holding exactly half a unit can come out one
    unit low: 0.02215 is stored as 0.022149999..., f'{0.02215:.4f}' gives '0.0221' while Excel shows 0.0222 (the
    SA Power Networks STR/ZSS rates in the AER 2025-26 and 2026-27 consolidated reports)."""
    d = excel_decimal(value)
    if exponent:
        if d == 0:
            return f"{0:.{digits}E}"
        shift = d.adjusted()
        mantissa = d.scaleb(-shift).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        if abs(mantissa) >= 10:  # 9.995 rounds up to 10.00: renormalise
            shift += 1
            mantissa = d.scaleb(-shift).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
        return f"{format(mantissa, 'f')}E{'+' if shift >= 0 else '-'}{abs(shift):02d}"
    return format(d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), "f")


def cell_value(cell):
    value = cell.value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    fmt = cell.number_format.split(';')[0]
    fmt = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', fmt)
    match = re.search(r'0\.([0#]+)|(?<![0#])0(?![0#])', fmt)
    if match and '%' not in fmt:
        digits = len(match.group(1) or '')
        return display(value, digits, exponent=bool(re.search(r'[Ee][+-]', fmt)))
    return str(value)
