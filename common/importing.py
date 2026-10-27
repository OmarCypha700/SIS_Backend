"""
Shared plumbing for "preview, then confirm" spreadsheet imports.

Each import provides `validate_rows(rows) -> [{"row", "input", "valid", "errors", "warnings", "data"}]`
and a `create(results, actor)` step. Preview never writes; confirm re-validates everything and
creates all rows in one transaction or nothing at all.
"""
from django.conf import settings
from rest_framework import serializers

from .tabular import read_table


def records_from_upload(request, *, max_rows=2000, required_columns=()):
    upload = request.FILES.get("file")
    if upload is None:
        raise serializers.ValidationError({"file": "Upload a CSV or Excel (.xlsx) file."})
    try:
        records = read_table(upload, max_bytes=settings.MAX_UPLOAD_CSV_BYTES, max_rows=max_rows)
    except ValueError as exc:
        raise serializers.ValidationError({"file": str(exc)}) from exc
    if not records:
        raise serializers.ValidationError({"file": "The file contains no data rows."})
    missing = set(required_columns) - set(records[0].keys())
    if missing:
        raise serializers.ValidationError({"file": f"Missing required columns: {', '.join(sorted(missing))}."})
    return records


def records_from_confirm(request, *, max_rows=2000):
    rows = request.data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise serializers.ValidationError({"rows": "Provide the rows returned by the preview."})
    if len(rows) > max_rows:
        raise serializers.ValidationError({"rows": f"Import at most {max_rows} rows at a time."})
    return [{str(k): ("" if v is None else str(v)) for k, v in row.items()} for row in rows if isinstance(row, dict)]


def preview_payload(results):
    return {
        "total": len(results),
        "valid": sum(1 for r in results if r["valid"]),
        "invalid": sum(1 for r in results if not r["valid"]),
        "rows": [{k: v for k, v in r.items() if k != "data"} for r in results],
    }


def row_result(index, raw, data, errors, warnings=None):
    return {"row": index, "input": raw, "valid": not errors, "errors": errors, "warnings": warnings or [], "data": data}


def serializer_errors(serializer):
    return {key: [str(m) for m in (value if isinstance(value, list) else [value])] for key, value in serializer.errors.items()}
