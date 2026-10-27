from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

image_extension_validator = FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])


def validate_image_size(file):
    if file.size > settings.MAX_UPLOAD_IMAGE_BYTES:
        raise ValidationError("Image must be 2 MB or smaller.")
