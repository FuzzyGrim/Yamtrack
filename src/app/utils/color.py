"""Utilities for working with poster colours and accents."""

from __future__ import annotations

from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import requests
from colorthief import ColorThief
from PIL import Image

from app.providers import tmdb


FALLBACK_COLOUR = "#6F8FFF"


def _fix_transparency(img: Image.Image) -> Image.Image:
    """Return an RGB version of the image with transparency flattened."""

    if img.mode in {"RGBA", "LA"}:
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        background.paste(img, mask=img.split()[-1])
        return background.convert("RGB")
    return img.convert("RGB")


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02X%02X%02X" % rgb


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        srgb = value / 255
        return srgb / 12.92 if srgb <= 0.03928 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _lighten(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(min(255, int(round(channel + (255 - channel) * factor))) for channel in rgb)


def _darken(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, int(round(channel * (1 - factor)))) for channel in rgb)


def _ensure_min_luminance(color: str, target: float = 0.12) -> str:
    rgb = _hex_to_rgb(color)
    luminance = _relative_luminance(rgb)

    if luminance >= target:
        return _rgb_to_hex(rgb)

    # Only lift extremely dark colours; blend cautiously
    blend_steps = (0.3, 0.5, 0.7, 0.85)
    for factor in blend_steps:
        rgb = _lighten(rgb, factor)
        luminance = _relative_luminance(rgb)
        if luminance >= target:
            break

    return _rgb_to_hex(rgb)


def _ensure_max_luminance(color: str, target: float = 0.8) -> str:
    rgb = _hex_to_rgb(color)
    luminance = _relative_luminance(rgb)

    if luminance <= target:
        return _rgb_to_hex(rgb)

    # Pull very bright colours slightly towards black for better definition
    blend_steps = (0.2, 0.35, 0.5)
    for factor in blend_steps:
        rgb = _darken(rgb, factor)
        luminance = _relative_luminance(rgb)
        if luminance <= target:
            break

    return _rgb_to_hex(rgb)


def extract_dominant_color(file_obj: BytesIO) -> str:
    """Return a hex string representing the dominant colour in the image."""

    try:
        file_obj.seek(0)
        with Image.open(file_obj) as img:
            prepared = _fix_transparency(img)

        file_obj.seek(0)
        buffer = BytesIO()
        prepared.save(buffer, format="PNG")
        buffer.seek(0)

        thief = ColorThief(buffer)
        dominant = thief.get_color(quality=5)
        return "#%02X%02X%02X" % dominant
    except Exception:
        return FALLBACK_COLOUR


def _normalise_tmdb_path(url: str) -> str:
    """Return a TMDB /t/p/original path for any TMDB-style URL or fragment."""

    if not url:
        return ""

    parsed = urlparse(url)
    path = parsed.path or url

    if "/t/p/" not in path:
        fragment = path.lstrip("/")
    else:
        fragment = path.split("/t/p/")[-1]

    if fragment.startswith("original/"):
        remainder = fragment[len("original/") :]
    else:
        parts = fragment.split("/", 1)
        remainder = parts[1] if len(parts) > 1 else parts[0]

    return f"/{remainder.lstrip('/')}"


def _fetch_image_bytes(url: str, *, prefer_small_tmdb: bool = False) -> bytes:
    """Fetch raw bytes for the given image URL or TMDB path."""

    if not url:
        raise ValueError("No image URL provided")

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()

    if netloc.endswith("image.tmdb.org") or "/t/p/" in parsed.path:
        path = _normalise_tmdb_path(url)
        size = "w342" if prefer_small_tmdb else "original"
        return tmdb.fetch_image(path, size=size)

    if parsed.scheme in {"http", "https"}:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.content

    if url.startswith("/"):
        size = "w342" if prefer_small_tmdb else "original"
        return tmdb.fetch_image(url, size=size)

    raise ValueError(f"Unsupported image source: {url}")


def get_poster_accent_from_url(url: Optional[str]) -> str:
    """Compute an accent colour directly from an image URL."""

    if not url:
        return FALLBACK_COLOUR

    try:
        image_bytes = _fetch_image_bytes(url, prefer_small_tmdb=True)
        color = extract_dominant_color(BytesIO(image_bytes))
        color = _ensure_min_luminance(color)
        color = _ensure_max_luminance(color)
        return color
    except Exception:
        return FALLBACK_COLOUR


def compute_and_store_poster_accent(item, poster_url: Optional[str] = None, force: bool = False) -> str:
    """Ensure the given item has a poster accent colour stored and return it."""

    current = getattr(item, "poster_accent_color", "")
    if current and current != FALLBACK_COLOUR and not force:
        normalised = _ensure_max_luminance(_ensure_min_luminance(current))
        if normalised != current:
            try:
                item.poster_accent_color = normalised
                item.save(update_fields=["poster_accent_color"])
            except Exception:
                pass
        return normalised

    url = poster_url or getattr(item, "image", "")
    accent = get_poster_accent_from_url(url)

    try:
        if accent and accent != current:
            item.poster_accent_color = accent
            item.save(update_fields=["poster_accent_color"])
    except Exception:
        # If saving fails (e.g. item is unsaved), still return the accent colour.
        pass

    return accent or FALLBACK_COLOUR


def build_accent_palette(accent: Optional[str]) -> dict[str, str]:
    """Return a palette dict containing accent and contrast colours."""

    accent = _ensure_max_luminance(_ensure_min_luminance(accent or FALLBACK_COLOUR))
    luminance = _relative_luminance(_hex_to_rgb(accent))
    # Use contrast calculation (WCAG) with threshold ensuring at least 4.2:1
    contrast = "#000000" if luminance >= 0.7 else "#FFFFFF"

    return {
        "accent": accent,
        "contrast": contrast,
    }
