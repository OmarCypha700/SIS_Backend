from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

image_extension_validator = FileExtensionValidator(allowed_extensions=["png", "jpg", "jpeg", "webp"])


# Logos, signatures and seals are drawn on every PDF and app icon; refuse images that would take
# hundreds of MB to decode (a 2 MB file can declare a huge, highly compressed picture).
MAX_BRANDING_PIXELS = 12_000_000


def validate_branding_image(file):
    validate_image_size(file)
    from PIL import Image

    try:
        position = file.tell()
        file.seek(0)
        with Image.open(file) as image:
            width, height = image.size
        file.seek(position)
    except Exception as exc:  # noqa: BLE001 - not an image Pillow can read
        raise ValidationError("Upload a valid PNG, JPEG or WebP image.") from exc
    if width * height > MAX_BRANDING_PIXELS:
        raise ValidationError("Image is too large: use one under 12 megapixels (e.g. 3000 x 3000 or smaller).")


def validate_image_size(file):
    if file.size > settings.MAX_UPLOAD_IMAGE_BYTES:
        raise ValidationError("Image must be 2 MB or smaller.")
