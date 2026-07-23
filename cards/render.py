"""Achievement card renderer.

Uses Playwright (headless Chromium) to render an HTML template into PNG and/or
PDF.  The template, CSS, and decorative SVG are identical to the original
Node.js/Puppeteer renderer so output is pixel-identical.

Card types control the accent colour and bottom pill.  The body text is
user-supplied; wrap any phrase in [brackets] to highlight it in the accent
colour.  ``internship`` and ``custom`` types also accept a logo URL.
"""

from __future__ import annotations

import base64
import html as html_mod
import logging
import re
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CARD_W = 1080
CARD_H = 1350

_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_PB_LOGO_PATH = _ASSETS_DIR / "pb-logo.png"

# Embed the PB logo as a data-URL once at import time.
_PB_LOGO_DATA_URL: str | None = None
try:
    _pb_logo_bytes = _PB_LOGO_PATH.read_bytes()
    _PB_LOGO_DATA_URL = f"data:image/png;base64,{base64.b64encode(_pb_logo_bytes).decode()}"
except FileNotFoundError:
    log.warning("PB logo asset missing at %s", _PB_LOGO_PATH)

# ---------------------------------------------------------------------------
# Card type presets
# ---------------------------------------------------------------------------

TYPES: dict[str, dict[str, Any]] = {
    "gsoc": {
        "accent": "#FBBC04",
        "pill": "Google Summer of Code",
        "logoUrl": "https://developers.google.com/open-source/gsoc/resources/downloads/GSoC-Horizontal.png",
    },
    "lfx": {
        "accent": "#5C9BD6",
        "pill": "The Linux Foundation",
        "logoUrl": "https://lfx.linuxfoundation.org/wp-content/uploads/2023/01/logo_lfx_nopad.svg",
    },
    "hackathon": {"accent": "#A855F7", "pill": "Hackathon Winner"},
    "competitive": {"accent": "#2ED573", "pill": "Competitive Programming"},
    "acm": {
        "accent": "#F5A623",
        "pill": "ACM Summer / Winter School",
        "logoUrl": "https://www.acm.org/binaries/content/gallery/global/top-menu/acm_logo_tablet.svg",
    },
    "internship": {"accent": "#00BCD4", "pill": None},  # logo via logoUrl
    "custom": {"accent": "#FFFFFF", "pill": None},
}

CARD_TYPES: list[str] = list(TYPES.keys())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape(s: str) -> str:
    return html_mod.escape(s)


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    m = _HEX_RE.match(hex_color)
    if not m:
        return f"rgba(255,255,255,{alpha})"
    v = m.group(1)
    r, g, b = int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _process_highlights(escaped_text: str) -> str:
    return re.sub(
        r"\[([^\[\]\n]+)\]",
        r'<span class="highlight">\1</span>',
        escaped_text,
    )


_EXT_TO_MIME: dict[str, str] = {
    "svg": "image/svg+xml",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


async def _fetch_image_as_data_url(url: str) -> str:
    """Fetch an image URL and return a base64 ``data:`` URL."""
    async with httpx.AsyncClient(follow_redirects=True) as http:
        resp = await http.get(url, timeout=15)
        resp.raise_for_status()

    ct_mime = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = url.split("?")[0].split("#")[0].rsplit(".", 1)[-1].lower()
    inferred = _EXT_TO_MIME.get(ext)
    mime = ct_mime if ct_mime.startswith("image/") else inferred
    if not mime:
        raise ValueError(f"Couldn't determine image type (content-type: {ct_mime or 'unknown'})")

    b64 = base64.b64encode(resp.content).decode()
    return f"data:{mime};base64,{b64}"


# ---------------------------------------------------------------------------
# Decorative SVG layer
# ---------------------------------------------------------------------------


def _build_deco_svg(accent_hex: str) -> str:
    stroke = _hex_to_rgba(accent_hex, 0.65)
    stroke_soft = _hex_to_rgba(accent_hex, 0.35)
    dot_fill = _hex_to_rgba(accent_hex, 0.75)
    dot_fill_soft = _hex_to_rgba(accent_hex, 0.45)

    dots = [
        (150, 240, 3.5), (930, 260, 3), (220, 180, 2.5), (880, 195, 2.5),
        (85, 365, 3), (1000, 395, 3.5), (120, 880, 3), (965, 905, 3.5),
        (70, 1090, 2.5), (1015, 1075, 3), (180, 1180, 3.5), (905, 1175, 2.5),
        (250, 765, 2), (840, 770, 2.5), (430, 175, 2), (665, 165, 2.5),
    ]
    plus_marks = [
        (110, 305), (980, 320), (60, 700), (1025, 720),
        (200, 1010), (890, 1010), (330, 220), (770, 230),
    ]

    dot_svg = "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{dot_fill if i % 3 == 0 else dot_fill_soft}"/>'
        for i, (x, y, r) in enumerate(dots)
    )
    plus_svg = "".join(
        f'<g stroke="{stroke_soft}" stroke-width="2" stroke-linecap="round">'
        f'<line x1="{x - 8}" y1="{y}" x2="{x + 8}" y2="{y}"/>'
        f'<line x1="{x}" y1="{y - 8}" x2="{x}" y2="{y + 8}"/>'
        f"</g>"
        for x, y in plus_marks
    )

    corners = (
        f'<path d="M 60 140 L 60 60 L 140 60" stroke="{stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="78" cy="78" r="3" fill="{dot_fill}"/>'
        f'<path d="M 940 60 L 1020 60 L 1020 140" stroke="{stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="1002" cy="78" r="3" fill="{dot_fill}"/>'
        f'<path d="M 60 1210 L 60 1290 L 140 1290" stroke="{stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="78" cy="1272" r="3" fill="{dot_fill}"/>'
        f'<path d="M 940 1290 L 1020 1290 L 1020 1210" stroke="{stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="1002" cy="1272" r="3" fill="{dot_fill}"/>'
    )

    avatar_rings = (
        f'<circle cx="540" cy="550" r="178" fill="none" stroke="{_hex_to_rgba(accent_hex, 0.4)}" stroke-width="2"/>'
        f'<circle cx="540" cy="550" r="194" fill="none" stroke="{_hex_to_rgba(accent_hex, 0.18)}" stroke-width="2" stroke-dasharray="6 10"/>'
    )

    return (
        f'<svg class="deco" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CARD_W} {CARD_H}" '
        f'width="{CARD_W}" height="{CARD_H}" aria-hidden="true">'
        f"{corners}{dot_svg}{plus_svg}{avatar_rings}"
        f"</svg>"
    )


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def _build_html(
    *,
    card_type: str,
    name: str,
    text: str,
    photo_data_url: str,
    logo_data_url: str | None,
) -> str:
    cfg = TYPES.get(card_type, TYPES["custom"])
    accent = cfg["accent"]
    sentence_html = _process_highlights(_escape(text))
    accent_soft = _hex_to_rgba(accent, 0.08)
    title_glow = _hex_to_rgba(accent, 0.18)
    deco_svg = _build_deco_svg(accent)

    pill_html = ""
    if logo_data_url:
        pill_html = f'<div class="pill logo-pill"><img src="{logo_data_url}" alt="logo" /></div>'
    elif cfg.get("pill"):
        pill_html = f'<div class="pill">{_escape(cfg["pill"])}</div>'

    pb_logo_mark = (
        f'<img class="mark" src="{_PB_LOGO_DATA_URL}" alt="Point Blank mark" />'
        if _PB_LOGO_DATA_URL
        else '<div style="font-size:56px;color:#48F80D;line-height:1;">&lt;.&gt;</div>'
    )

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&family=JetBrains+Mono:wght@600;700&display=swap">
<style>
  :root {{ --accent: {accent}; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  html, body {{
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{
    width: {CARD_W}px;
    height: {CARD_H}px;
    background: #07070d;
    background-image:
      radial-gradient(ellipse 70% 50% at 50% 50%, {accent_soft} 0%, transparent 70%);
    font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
    color: #fff;
    position: relative;
    overflow: hidden;
  }}

  .grid-bg, .deco {{
    position: absolute;
    top: 0; left: 0;
    width: {CARD_W}px;
    height: {CARD_H}px;
    pointer-events: none;
  }}
  .grid-bg {{ z-index: 0; }}
  .deco {{ z-index: 1; }}

  .pb-logo {{
    position: absolute;
    top: 70px; left: 0; right: 0;
    text-align: center;
    z-index: 2;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.05em;
  }}
  .pb-logo .mark {{
    width: 220px;
    height: auto;
    display: block;
    margin: 0 auto;
  }}
  .pb-logo .row {{ font-size: 32px; margin-top: 20px; }}
  .pb-logo .point {{ color: #48F80D; }}
  .pb-logo .blank {{ color: #fff; }}

  .title {{
    position: absolute;
    top: 230px; left: 0; right: 0;
    text-align: center;
    font-size: 86px;
    font-weight: 800;
    letter-spacing: -0.02em;
    text-shadow: 0 0 18px {title_glow};
    z-index: 2;
  }}

  .avatar {{
    position: absolute;
    left: 50%; top: 380px;
    transform: translateX(-50%);
    width: 340px; height: 340px;
    border-radius: 50%;
    overflow: hidden;
    background: #1a1a2e;
    box-shadow: 0 0 0 6px rgba(255,255,255,0.6);
  }}
  .avatar img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}

  .person-name {{
    position: absolute;
    top: 770px; left: 0; right: 0;
    text-align: center;
    font-size: 60px;
    font-weight: 700;
  }}

  .sentence {{
    position: absolute;
    top: 905px; left: 90px; right: 90px;
    text-align: center;
    font-size: 36px;
    line-height: 1.5;
    font-weight: 500;
  }}
  .highlight {{ color: var(--accent); font-weight: 700; }}

  .pill {{
    position: absolute;
    bottom: 110px; left: 50%;
    transform: translateX(-50%);
    background: #ffffff;
    color: #15192b;
    padding: 22px 44px;
    border-radius: 18px;
    font-weight: 700;
    font-size: 30px;
    white-space: nowrap;
    box-shadow: 0 10px 26px rgba(0,0,0,0.5);
  }}
  .pill.logo-pill {{
    padding: 18px 28px;
  }}
  .pill.logo-pill img {{
    height: 72px;
    max-width: 420px;
    object-fit: contain;
    display: block;
  }}
</style>
</head>
<body>
  <svg class="grid-bg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CARD_W} {CARD_H}" width="{CARD_W}" height="{CARD_H}" aria-hidden="true">
    <defs>
      <pattern id="grid-pattern" width="52" height="52" patternUnits="userSpaceOnUse">
        <path d="M 52 0 L 0 0 0 52" fill="none" stroke="#969FBE" stroke-opacity="0.34" stroke-width="1"/>
      </pattern>
    </defs>
    <rect width="{CARD_W}" height="{CARD_H}" fill="url(#grid-pattern)"/>
  </svg>

  {deco_svg}

  <div class="pb-logo">
    {pb_logo_mark}
    <div class="row"><span class="point">Point</span> <span class="blank">Blank</span></div>
  </div>

  <div class="title">Congratulations</div>

  <div class="avatar"><img src="{photo_data_url}" alt="profile" /></div>

  <div class="person-name">{_escape(name)}</div>
  <div class="sentence">{sentence_html}</div>
  {pill_html}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def render_card(
    *,
    card_type: str,
    name: str,
    text: str,
    photo_bytes: bytes,
    photo_mime: str = "image/jpeg",
    logo_url: str | None = None,
    formats: list[str] | None = None,
) -> dict[str, str]:
    """Render an achievement card and return ``{"png": base64, "pdf": base64}``.

    Only the requested *formats* (default ``["png"]``) are populated.
    """
    if formats is None:
        formats = ["png"]

    if not photo_bytes:
        raise ValueError("Missing profile photo")
    if card_type not in TYPES:
        raise ValueError(f'Unknown card type "{card_type}". Use one of: {", ".join(TYPES)}')

    want_png = "png" in formats
    want_pdf = "pdf" in formats
    if not want_png and not want_pdf:
        raise ValueError("render_card: at least one format (png|pdf) required")

    # Resolve logo
    logo_data_url: str | None = None
    cfg = TYPES[card_type]
    effective_logo_url = logo_url or cfg.get("logoUrl")
    if effective_logo_url:
        try:
            logo_data_url = await _fetch_image_as_data_url(effective_logo_url)
        except Exception as exc:
            if logo_url:
                # User-supplied URL — fail loudly.
                raise ValueError(f"Couldn't fetch logo URL: {exc}") from exc
            # Preset default failed — fall back to text pill.
            log.warning(
                "Preset logo fetch failed for %s: %s (%s)",
                card_type, exc, effective_logo_url,
            )

    photo_data_url = f"data:{photo_mime};base64,{base64.b64encode(photo_bytes).decode()}"
    page_html = _build_html(
        card_type=card_type,
        name=name,
        text=text,
        photo_data_url=photo_data_url,
        logo_data_url=logo_data_url,
    )

    from playwright.async_api import async_playwright  # lazy import

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        try:
            page = await browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
            await page.set_content(page_html, wait_until="load", timeout=30_000)
            # Wait for Google Fonts
            await page.evaluate("() => document.fonts && document.fonts.ready")
            await page.wait_for_timeout(150)

            out: dict[str, str] = {}

            if want_png:
                png_bytes = await page.screenshot(type="png")
                out["png"] = base64.b64encode(png_bytes).decode()

            if want_pdf:
                pdf_bytes = await page.pdf(
                    width=f"{CARD_W}px",
                    height=f"{CARD_H}px",
                    print_background=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                    prefer_css_page_size=False,
                )
                out["pdf"] = base64.b64encode(pdf_bytes).decode()

            await page.close()
            return out
        finally:
            await browser.close()
