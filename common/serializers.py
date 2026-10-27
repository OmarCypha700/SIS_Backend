import copy

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers


class ModelCleanMixin:
    """Run the model's clean() during serializer validation so rules live in one place."""

    def validate(self, attrs):
        attrs = super().validate(attrs)
        model = self.Meta.model
        concrete = {field.name for field in model._meta.concrete_fields}
        instance = copy.copy(self.instance) if self.instance is not None else model()
        for key, value in attrs.items():
            if key in concrete:
                setattr(instance, key, value)
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs


class UpperCaseCharField(serializers.CharField):
    """Normalises codes (course, programme, department) before uniqueness validators run."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).upper()
