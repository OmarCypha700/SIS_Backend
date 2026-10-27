"""
Consistent API error format:

    {"detail": "Human readable summary", "code": "machine_code", "errors": {...field errors...}}

Unexpected exceptions are not converted here; Django returns a generic 500 without
internal details when DEBUG is off.
"""
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError, RestrictedError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler


class DomainError(Exception):
    """Business-rule violation raised by service functions."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid"

    def __init__(self, message, *, code=None, errors=None, status_code=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.errors = errors


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


def _django_validation_to_dict(exc):
    if hasattr(exc, "message_dict"):
        return exc.message_dict
    return {"non_field_errors": exc.messages}


def api_exception_handler(exc, context):
    if isinstance(exc, DomainError):
        body = {"detail": exc.message, "code": exc.code}
        if exc.errors is not None:
            body["errors"] = exc.errors
        return Response(body, status=exc.status_code)

    if isinstance(exc, (ProtectedError, RestrictedError)):
        return Response(
            {
                "detail": "This record is referenced by other records and cannot be deleted. "
                "Deactivate or archive it instead.",
                "code": "protected",
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(_django_validation_to_dict(exc))
    elif isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    response = exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    if isinstance(exc, exceptions.ValidationError):
        errors = data if isinstance(data, dict) else {"non_field_errors": data}
        first = _first_message(errors)
        response.data = {"detail": first or "Validation failed.", "code": "validation_error", "errors": errors}
    elif isinstance(data, dict) and "detail" in data:
        detail = data["detail"]
        response.data = {"detail": str(detail), "code": getattr(detail, "code", None) or exc.default_code}
    return response


def _first_message(errors):
    if isinstance(errors, dict):
        for value in errors.values():
            message = _first_message(value)
            if message:
                return message
    elif isinstance(errors, list):
        for value in errors:
            message = _first_message(value)
            if message:
                return message
    elif errors:
        return str(errors)
    return None
