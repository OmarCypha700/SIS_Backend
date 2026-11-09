"""
App icons and opening screens for the installable web app, drawn from the institution's branding.

* any      - the logo on a white rounded square, or the initials on the primary colour (also the favicon)
* maskable - full-bleed background with the mark inside the central safe zone (Android shapes it)
* badge    - white initials on transparency; Android uses only the alpha channel in the status bar
* splash   - iPhone/iPad opening screen: the logo and the institution's name on white

The logo's empty margins are trimmed and it is scaled (up or down) to fill its space, so a small
or padded upload still gives a clear, full-size icon.
"""
import hashlib
import io
import textwrap

from PIL import Image, ImageDraw, ImageFont

from common.validators import MAX_BRANDING_PIXELS

VERSION = "2"  # change to make browsers and the cache fetch newly drawn icons
SIZES = (32, 48, 64, 96, 180, 192, 512)
PURPOSES = ("any", "maskable", "badge")
# Portrait screen sizes (pixels) of current iPhones and iPads, for their opening screens.
SPLASH_SIZES = {
    (640, 1136), (750, 1334), (828, 1792), (1125, 2436), (1170, 2532), (1179, 2556), (1206, 2622), (1242, 2208),
    (1242, 2688), (1284, 2778), (1290, 2796), (1320, 2868), (1536, 2048), (1620, 2160), (1640, 2360), (1668, 2388),
    (2048, 2732),
}


def etag(profile, size, purpose):
    key = f"{VERSION}|{profile.updated_at.isoformat() if profile.updated_at else ''}|{profile.logo.name}|" \
          f"{profile.primary_color}|{profile.short_name}|{profile.name}|{size}|{purpose}"
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


def _font(size):
    return ImageFont.load_default(size=max(int(size), 8))


def _draw_text(image, text, box, fill):
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    size = int(height * (0.55 if len(text) <= 2 else 0.42))
    font = _font(size)
    bbox = draw.textbbox((0, 0), text, font=font)
    while bbox[2] - bbox[0] > width * 0.9 and size > 8:
        size -= 2
        font = _font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
    x = left + (width - (bbox[2] - bbox[0])) / 2 - bbox[0]
    y = top + (height - (bbox[3] - bbox[1])) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def _trim(logo):
    """Crop away transparent or plain white margins, keeping a hair of space round the mark."""
    pixels = logo.load()
    width, height = logo.size
    mask = Image.new("L", logo.size, 0)
    mask_pixels = mask.load()
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > 16 and min(r, g, b) < 240:
                mask_pixels[x, y] = 255
    box = mask.getbbox()
    if box is None:
        return logo
    pad = int(max(box[2] - box[0], box[3] - box[1]) * 0.02)
    box = (max(box[0] - pad, 0), max(box[1] - pad, 0), min(box[2] + pad, width), min(box[3] + pad, height))
    return logo.crop(box)


def _logo(profile):
    if not profile.logo:
        return None
    try:
        with profile.logo.open("rb") as handle:
            logo = Image.open(handle)
            if logo.width * logo.height > MAX_BRANDING_PIXELS:
                return None  # uploaded before the size check existed: use the initials
            logo.draft("RGBA", (1024, 1024))  # JPEG: decode at a reduced scale
            logo.load()
        logo = logo.convert("RGBA")
        logo.thumbnail((600, 600), Image.LANCZOS)  # trimming looks at every pixel: keep it quick
        return _trim(logo)
    except Exception:  # noqa: BLE001 - a missing or broken file falls back to initials
        return None


def _paste_fitted(image, logo, box):
    """Scale the logo, up or down, to fit `box` (left, top, right, bottom), centred."""
    left, top, right, bottom = box
    scale = min((right - left) / logo.width, (bottom - top) / logo.height)
    fitted = logo.resize((max(int(logo.width * scale), 1), max(int(logo.height * scale), 1)), Image.LANCZOS)
    x = left + (right - left - fitted.width) // 2
    y = top + (bottom - top - fitted.height) // 2
    image.alpha_composite(fitted, (x, y))


def _centre_box(size, fraction):
    margin = int(size * (1 - fraction) / 2)
    return (margin, margin, size - margin, size - margin)


def render(profile, size, purpose):
    colour = _hex(profile.primary_color)
    if purpose == "badge":
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        _draw_text(image, _initials(profile), (0, 0, size, size), (255, 255, 255, 255))
    else:
        logo = _logo(profile)
        background = (255, 255, 255, 255) if logo else (*colour, 255)
        if purpose == "maskable":
            # Android crops to a circle or squircle: keep the mark inside the central safe zone.
            image = Image.new("RGBA", (size, size), background)
            box = _centre_box(size, 0.66 if logo else 0.6)
        else:
            image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            radius = int(size * 0.22)
            ImageDraw.Draw(image).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=background)
            box = _centre_box(size, 0.86 if logo else 0.8)
        if logo:
            _paste_fitted(image, logo, box)
        else:
            _draw_text(image, _initials(profile), box, (255, 255, 255, 255))
    return _png(image)


def render_splash(profile, width, height):
    """The opening screen: the logo (or initials tile) above the institution's name, on white."""
    image = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    short = min(width, height)
    mark = int(short * 0.42)
    top = int(height * 0.5 - mark * 0.75)
    box = ((width - mark) // 2, top, (width + mark) // 2, top + mark)
    logo = _logo(profile)
    if logo:
        _paste_fitted(image, logo, box)
    else:
        tile = Image.new("RGBA", (mark, mark), (0, 0, 0, 0))
        ImageDraw.Draw(tile).rounded_rectangle((0, 0, mark - 1, mark - 1), radius=int(mark * 0.22),
                                               fill=(*_hex(profile.primary_color), 255))
        _draw_text(tile, _initials(profile), (0, 0, mark, mark), (255, 255, 255, 255))
        image.alpha_composite(tile, box[:2])
    draw = ImageDraw.Draw(image)
    font = _font(short * 0.05)
    y = box[3] + int(short * 0.07)
    for line in textwrap.wrap(profile.name or "Student Portal", width=24)[:3]:
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text(((width - (bbox[2] - bbox[0])) / 2 - bbox[0], y), line, font=font, fill=(24, 24, 27, 255))
        y += int((bbox[3] - bbox[1]) * 1.45)
    return _png(image.convert("RGB"))


def _png(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
