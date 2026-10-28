"""
Bulk actions over selected records.

Each record is processed in its own savepoint, so one record that cannot be changed (for example,
a student with results cannot be deleted) is reported and skipped without undoing the others.
"""
from django.db import transaction
from rest_framework import serializers

from .exceptions import DomainError

MAX_BULK_IDS = 500


class BulkIdsField(serializers.ListField):
    def __init__(self, **kwargs):
        super().__init__(child=serializers.IntegerField(min_value=1), min_length=1, max_length=MAX_BULK_IDS, **kwargs)


def run_bulk(queryset, ids, operation, *, label=str):
    """Apply `operation(obj)` to each selected object the caller may see. Returns done/skipped lists."""
    ids = list(dict.fromkeys(ids))  # keep order, drop duplicates
    found = {obj.pk: obj for obj in queryset.filter(pk__in=ids)}
    done, skipped = [], []
    for pk in ids:
        obj = found.get(pk)
        if obj is None:
            skipped.append({"id": pk, "label": str(pk), "reason": "Not found."})
            continue
        name = label(obj)
        try:
            with transaction.atomic():
                operation(obj)
        except DomainError as exc:
            skipped.append({"id": pk, "label": name, "reason": exc.message})
            continue
        done.append({"id": pk, "label": name})
    return {"done": done, "skipped": skipped}
