"""
App icons for the installable web app, drawn from the institution's branding.

* any      - the logo on a white rounded square, or the initials on the primary colour
* maskable - full-bleed background with the mark inside the central safe zone (Android shapes it)
* badge    - white initials on transparency; Android uses only the alpha channel in the status bar
"""
import hashlib
import io

from PIL import Image, ImageDraw, ImageFont

SIZES = (64, 96, 180, 192, 512)
PURPOSES = ("any", "maskable", "badge")


def etag(profile, size, purpose):
    key = f"{profile.updated_at.isoformat() if profile.updated_at else ''}|{profile.logo.name}|" \
          f"{profile.primary_color}|{profile.short_name}|{size}|{purpose}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


def _initials(profile):
    text = "".join(ch for ch in (profile.short_name or "SIS") if ch.isalnum()).upper()
    return text[:3] or "SIS"


def _hex(colour):
    colour = (colour or "#1d4ed8").lstrip("#")
    try:
        return tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (29, 78, 216)


def _draw_text(image, text, box, fill):
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    size = int(height * (0.55 if len(text) <= 2 else 0.42))
    font = ImageFont.load_default(size=size)
    bbox = draw.textbbox((0, 0), text, font=font)
    while bbox[2] - bbox[0] > width * 0.9 and size > 8:
        size -= 2
        font = ImageFont.load_default(size=size)
        bbox = draw.textbbox((0, 0), text, font=font)
    x = left + (width - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = top + (height - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _logo(profile):
    if not profile.logo:
        return None
    try:
        with profile.logo.open("rb") as handle:
            logo = Image.open(handle)
            logo.load()
        return logo.convert("RGBA")
    except Exception:  # noqa: BLE001 - a missing or broken file falls back to initials
        return None


def _paste_centered(image, logo, fraction):
    size = image.size[0]
    target = int(size * fraction)
    logo = logo.copy()
    logo.thumbnail((target, target), Image.LANCZOS)
    image.alpha_composite(logo, ((size - logo.size[0]) // 2, (size - logo.size[1]) // 2))


def render(profile, size, purpose):
    colour = _hex(profile.primary_color)
    if purpose == "badge":
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        _draw_text(image, _initials(profile), (0, 0, size, size), (255, 255, 255, 255))
    else:
        logo = _logo(profile)
        background = (255, 255, 255, 255) if logo else (*colour, 255)
        if purpose == "maskable":
            image = Image.new("RGBA", (size, size), background)
            safe = (int(size * 0.2), int(size * 0.2), int(size * 0.8), int(size * 0.8))
            fraction = 0.58
        else:
            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            radius = int(size * 0.22)
            ImageDraw.Draw(image).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=background)
            safe = (int(size * 0.1), int(size * 0.1), int(size * 0.9), int(size * 0.9))
            fraction = 0.76
        if logo:
            _paste_centered(image, logo, fraction)
        else:
            _draw_text(image, _initials(profile), safe, (255, 255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
