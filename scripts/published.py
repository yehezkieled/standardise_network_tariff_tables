import re


def cell_value(cell):
    value = cell.value
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    fmt = cell.number_format.split(';')[0]
    fmt = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', '', fmt)
    match = re.search(r'0\.([0#]+)|(?<![0#])0(?![0#])', fmt)
    if match and '%' not in fmt:
        digits = len(match.group(1) or '')
        if re.search(r'[Ee][+-]', fmt):
            return f'{value:.{digits}E}'
        return f'{value:.{digits}f}'
    return str(value)
