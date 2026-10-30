import json
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.fields.files import FieldFile
from django.forms.models import model_to_dict

from common.middleware import get_request_context

from .models import AuditLog

SENSITIVE_KEYS = {"password", "token", "secret", "session", "csrf", "otp"}


def _scrub(value):
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if any(s in str(key).lower() for s in SENSITIVE_KEYS) else _scrub(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return value


def _jsonable(value):
    if value is None:
        return None

    class Encoder(DjangoJSONEncoder):
        def default(self, o):
            if isinstance(o, FieldFile):
                return o.name or None
            if isinstance(o, Decimal):
                return str(o)
            if hasattr(o, "pk"):
                return o.pk
            return super().default(o)

    return _scrub(json.loads(json.dumps(value, cls=Encoder)))


def snapshot(instance, fields=None, exclude=("password",)):
    """Serialisable dict of a model instance's field values for before/after records."""
    if instance is None:
        return None
    data = model_to_dict(instance, fields=fields, exclude=exclude)
    return _jsonable(data)


def diff(before, after):
    """Keep only the keys whose value changed, to keep audit rows small."""
    if not before or not after:
        return before, after
    changed = [key for key in after if before.get(key) != after.get(key)]
    return {k: before.get(k) for k in changed}, {k: after.get(k) for k in changed}


def log_action(action, *, actor=None, instance=None, entity_type=None, entity_id=None,
               summary="", before=None, after=None, reason="", metadata=None):
    context = get_request_context()
    if instance is not None:
        entity_type = entity_type or instance._meta.label_lower
        entity_id = entity_id or str(instance.pk)
    actor = actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None
    return AuditLog.objects.create(
        actor=actor,
        actor_email=getattr(actor, "email", "") or "",
        actor_roles=" ".join(actor.capacity_codes()) if actor is not None else "",
        action=action,
        entity_type=entity_type or "",
        entity_id=str(entity_id or ""),
        summary=summary[:255],
        before=_jsonable(before),
        after=_jsonable(after),
        reason=reason or "",
        metadata=_jsonable(metadata or {}),
        ip_address=context.get("ip_address"),
        request_id=context.get("request_id", ""),
    )
