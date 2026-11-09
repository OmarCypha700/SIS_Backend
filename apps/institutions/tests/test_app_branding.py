"""App icons, favicon and opening screens drawn from the institution's logo."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.institutions import app_icon
from apps.institutions.models import InstitutionProfile

pytestmark = pytest.mark.django_db


def small_padded_logo():
    """A 120 px logo whose mark (a dark disc) fills only the middle half: the rest is transparent."""
    image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    from PIL import ImageDraw

    ImageDraw.Draw(image).ellipse((30, 30, 90, 90), fill=(20, 120, 50, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return SimpleUploadedFile("logo.png", buffer.getvalue(), content_type="image/png")


def coverage(png):
    """Share of the icon's width taken by the mark (non-white, opaque pixels)."""
    image = Image.open(io.BytesIO(png)).convert("RGBA")
    mask = Image.new("L", image.size, 0)
    for x in range(image.width):
        for y in range(0, image.height, 4):
            r, g, b, a = image.getpixel((x, y))
            if a > 16 and min(r, g, b) < 200:
                mask.putpixel((x, y), 255)
    box = mask.getbbox()
    return (box[2] - box[0]) / image.width


def test_a_small_padded_logo_is_trimmed_and_enlarged_to_fill_the_icon(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    profile = InstitutionProfile.load()
    profile.logo = small_padded_logo()
    profile.save()
    assert coverage(app_icon.render(profile, 512, "any")) > 0.8  # was about 0.12 (no enlarging, no trimming)
    assert 0.6 < coverage(app_icon.render(profile, 512, "maskable")) < 0.7  # inside Android's safe zone


@pytest.mark.parametrize("query,status", [("width=1179&height=2556", 200), ("width=2556&height=1179", 200),
                                          ("width=5000&height=5000", 404), ("width=x", 404)])
def test_opening_screen_only_at_real_device_sizes(api, query, status):
    response = api.get(f"/api/v1/institution/app-splash/?{query}")
    assert response.status_code == status
    if status == 200:
        image = Image.open(io.BytesIO(response.content))
        assert image.size in ((1179, 2556), (2556, 1179)) and response["Cache-Control"].startswith("public")


def test_favicon_sizes(api):
    for size in (32, 48):
        response = api.get(f"/api/v1/institution/app-icon/?size={size}&purpose=any")
        assert response.status_code == 200
        assert Image.open(io.BytesIO(response.content)).size == (size, size)
