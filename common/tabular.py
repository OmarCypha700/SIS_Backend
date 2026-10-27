"""
Spreadsheet input and output shared by every import and export: CSV (UTF-8) and Excel (.xlsx).

Exports choose the format with the `file_format` query parameter (`csv`, the default, or `xlsx`).
`format` is not used because Django REST framework reserves it for content negotiation.
"""
import csv
import datetime
import io
from decimal import Decimal

from django.http import HttpResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils.exceptions import InvalidFileException

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORMATS = ("csv", "xlsx")

# Characters that spreadsheet applications interpret as the start of a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Text for a cell; values that look like formulas are escaped so they are never executed."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float, Decimal)):
        return value
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES) and not _is_number(text):
        return "'" + text
    return text


def _is_number(text):
    try:
        float(text)
        return True
    except ValueError:
        return False


def requested_format(request):
    value = (request.query_params.get("file_format") or "csv").lower()
    return value if value in FORMATS else "csv"


def table_response(filename_base, header, rows, file_format="csv", sheet_title="Data"):
    """Download response for a header + rows table in CSV or Excel."""
    if file_format == "xlsx":
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = sheet_title[:31]
        sheet.append([str(h) for h in header])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        widths = [len(str(h)) for h in header]
        for row in rows:
            values = [safe_cell(v) for v in row]
            sheet.append(values)
            for i, value in enumerate(values[: len(widths)]):
                widths[i] = max(widths[i], min(len(str(value)), 60))
        for i, width in enumerate(widths, start=1):
            sheet.column_dimensions[sheet.cell(row=1, column=i).column_letter].width = width + 2
        sheet.freeze_panes = "A2"
        buffer = io.BytesIO()
        workbook.save(buffer)
        response = HttpResponse(buffer.getvalue(), content_type=XLSX_CONTENT_TYPE)
        response["Content-Disposition"] = f'attachment; filename="{filename_base}.xlsx"'
        return response

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename_base}.csv"'
    response.write("﻿")  # BOM so Excel opens UTF-8 CSV files correctly
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return response


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.date().isoformat() if value.time() == datetime.time() else value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_table(uploaded_file, *, max_bytes, max_rows=5000):
    """
    Read an uploaded CSV or .xlsx file into a list of dicts keyed by lower-case header names.
    Raises ValueError with a user-facing message.
    """
    if uploaded_file.size > max_bytes:
        raise ValueError(f"File is too large (maximum {max_bytes // (1024 * 1024)} MB).")
    name = (uploaded_file.name or "").lower()
    raw = uploaded_file.read()
    if name.endswith(".xlsx") or raw[:2] == b"PK":
        header, rows = _read_xlsx(raw)
    elif name.endswith(".xls"):
        raise ValueError("Old .xls files are not supported. Save the file as .xlsx or .csv.")
    else:
        header, rows = _read_csv(raw)
    if not header or not any(header):
        raise ValueError("The file has no header row.")
    header = [h.strip().lower() for h in header]
    records = []
    for values in rows:
        values = [_cell_text(v) for v in values]
        if not any(values):
            continue  # skip blank lines
        records.append({key: (values[i] if i < len(values) else "") for i, key in enumerate(header) if key})
        if len(records) > max_rows:
            raise ValueError(f"Import at most {max_rows} rows at a time.")
    return records


def _read_csv(raw):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("CSV files must be saved as UTF-8.") from exc
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _read_xlsx(raw):
    try:
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except (InvalidFileException, KeyError, OSError, ValueError) as exc:
        raise ValueError("This is not a valid Excel (.xlsx) file.") from exc
    sheet = workbook.worksheets[0]
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    workbook.close()
    while rows and not any(v not in (None, "") for v in rows[0]):
        rows.pop(0)  # leading empty rows
    if not rows:
        return [], []
    return [_cell_text(v) for v in rows[0]], rows[1:]
