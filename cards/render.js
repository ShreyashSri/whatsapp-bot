// Achievement card renderer. Loads Puppeteer lazily so bot.js can require
// this module even if puppeteer is missing; the launch only happens when
// renderCard() is actually called.
//
// Type controls accent color + bottom pill. The body text is fully
// user-supplied; wrap any phrase in [brackets] to highlight it in the
// accent color. `internship` and `custom` types also accept a logo URL —
// the bot fetches the image and shows it inside the bottom pill.

const CARD_W = 1080;
const CARD_H = 1350;

// Load the PB logo once at module init and embed it as a data URL in every
// render. Keeping it as a file (rather than re-fetching a URL per render)
// makes the renderer self-contained and avoids a network round trip per card.
const PB_LOGO_PATH = require("path").join(__dirname, "assets", "pb-logo.png");
const PB_LOGO_DATA_URL = (() => {
  try {
    const buf = require("fs").readFileSync(PB_LOGO_PATH);
    return `data:image/png;base64,${buf.toString("base64")}`;
  } catch (err) {
    console.warn(`⚠️ PB logo asset missing at ${PB_LOGO_PATH}: ${err.message}`);
    return null;
  }
})();

// Each preset can carry a default logoUrl. When set, that image is fetched
// at render time and embedded inside the bottom pill (replacing the `pill`
// text). The user can still pass their own logoUrl as the 4th part of the
// !card command to override it.
const TYPES = {
  gsoc: {
    accent: "#FBBC04",
    pill: "Google Summer of Code",
    logoUrl: "https://developers.google.com/open-source/gsoc/resources/downloads/GSoC-Horizontal.png",
  },
  lfx: {
    accent: "#5C9BD6",
    pill: "The Linux Foundation",
    logoUrl: "https://lfx.linuxfoundation.org/wp-content/uploads/2023/01/logo_lfx_nopad.svg",
  },
  hackathon:   { accent: "#A855F7", pill: "Hackathon Winner" },
  competitive: { accent: "#2ED573", pill: "Competitive Programming" },
  acm: {
    accent: "#F5A623",
    pill: "ACM Summer / Winter School",
    logoUrl: "https://www.acm.org/binaries/content/gallery/global/top-menu/acm_logo_tablet.svg",
  },
  internship:  { accent: "#00BCD4", pill: null }, // logo provided via logoUrl
  custom:      { accent: "#FFFFFF", pill: null },
};

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Convert a #RRGGBB hex to rgba() with the given alpha.
// We use explicit rgba (instead of CSS color-mix) because Chrome's PDF
// renderer is unreliable with color-mix in box/text-shadow values.
function hexToRgba(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return `rgba(255,255,255,${alpha})`;
  const v = m[1];
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

// Convert [phrase] markers in already-escaped text to highlighted spans.
function processHighlights(escapedText) {
  return escapedText.replace(/\[([^\[\]\n]+)\]/g, '<span class="highlight">$1</span>');
}

// Fetch an image URL and return a base64 data: URL.
// Falls back to URL-extension sniffing when the server returns a
// non-image/* content-type (e.g. some CDNs serve SVG as text/xml).
async function fetchImageAsDataUrl(url) {
  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const ctMime = (res.headers.get("content-type") || "").toLowerCase().split(";")[0].trim();
  const ext = url.split(/[?#]/)[0].split(".").pop().toLowerCase();
  const inferred = {
    svg: "image/svg+xml",
    png: "image/png",
    jpg: "image/jpeg",
    jpeg: "image/jpeg",
    gif: "image/gif",
    webp: "image/webp",
  }[ext];
  const mime = ctMime.startsWith("image/") ? ctMime : inferred;
  if (!mime) {
    throw new Error(`Couldn't determine image type (content-type: ${ctMime || "unknown"})`);
  }
  const buf = Buffer.from(await res.arrayBuffer());
  return `data:${mime};base64,${buf.toString("base64")}`;
}

// Build the decorative SVG layer: corner ornaments, sparkles, accent ring
// around the avatar. Lives below the foreground content (z-index 1) and
// above the grid (z-index 0). All colors derive from the type's accent.
function buildDecoSvg(accentHex) {
  const stroke = hexToRgba(accentHex, 0.65);
  const strokeSoft = hexToRgba(accentHex, 0.35);
  const dotFill = hexToRgba(accentHex, 0.75);
  const dotFillSoft = hexToRgba(accentHex, 0.45);

  // Sparkles — hand-placed so they read as scattered, not gridded. Mix of
  // small dots and tiny "+" marks. Coordinates are in the 1080x1350 viewBox.
  const dots = [
    [150, 240, 3.5], [930, 260, 3], [220, 180, 2.5], [880, 195, 2.5],
    [85, 365, 3], [1000, 395, 3.5], [120, 880, 3], [965, 905, 3.5],
    [70, 1090, 2.5], [1015, 1075, 3], [180, 1180, 3.5], [905, 1175, 2.5],
    [250, 765, 2], [840, 770, 2.5], [430, 175, 2], [665, 165, 2.5],
  ];
  const plusMarks = [
    [110, 305], [980, 320], [60, 700], [1025, 720],
    [200, 1010], [890, 1010], [330, 220], [770, 230],
  ];
  const dotSvg = dots
    .map(([x, y, r], i) =>
      `<circle cx="${x}" cy="${y}" r="${r}" fill="${i % 3 === 0 ? dotFill : dotFillSoft}"/>`
    )
    .join("");
  const plusSvg = plusMarks
    .map(([x, y]) =>
      `<g stroke="${strokeSoft}" stroke-width="2" stroke-linecap="round">` +
      `<line x1="${x - 8}" y1="${y}" x2="${x + 8}" y2="${y}"/>` +
      `<line x1="${x}" y1="${y - 8}" x2="${x}" y2="${y + 8}"/>` +
      `</g>`
    )
    .join("");

  // L-shaped corner brackets. Stroke width 3 reads at this size.
  const corners =
    // top-left
    `<path d="M 60 140 L 60 60 L 140 60" stroke="${stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<circle cx="78" cy="78" r="3" fill="${dotFill}"/>` +
    // top-right
    `<path d="M 940 60 L 1020 60 L 1020 140" stroke="${stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<circle cx="1002" cy="78" r="3" fill="${dotFill}"/>` +
    // bottom-left
    `<path d="M 60 1210 L 60 1290 L 140 1290" stroke="${stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<circle cx="78" cy="1272" r="3" fill="${dotFill}"/>` +
    // bottom-right
    `<path d="M 940 1290 L 1020 1290 L 1020 1210" stroke="${stroke}" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round"/>` +
    `<circle cx="1002" cy="1272" r="3" fill="${dotFill}"/>`;

  // A pair of concentric soft accent rings around the avatar (the white ring
  // sits at radius 170; these go just outside, faded out).
  const avatarRings =
    `<circle cx="540" cy="550" r="178" fill="none" stroke="${hexToRgba(accentHex, 0.4)}" stroke-width="2"/>` +
    `<circle cx="540" cy="550" r="194" fill="none" stroke="${hexToRgba(accentHex, 0.18)}" stroke-width="2" stroke-dasharray="6 10"/>`;

  return (
    `<svg class="deco" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CARD_W} ${CARD_H}" width="${CARD_W}" height="${CARD_H}" aria-hidden="true">` +
    corners +
    dotSvg +
    plusSvg +
    avatarRings +
    `</svg>`
  );
}

function buildHtml({ type, name, text, photoDataUrl, logoDataUrl }) {
  const cfg = TYPES[type] ?? TYPES.custom;
  const sentenceHtml = processHighlights(escapeHtml(text));
  const accentSoft = hexToRgba(cfg.accent, 0.08);
  const titleGlow = hexToRgba(cfg.accent, 0.18);
  const decoSvg = buildDecoSvg(cfg.accent);
  let pillHtml = "";
  if (logoDataUrl) {
    pillHtml = `<div class="pill logo-pill"><img src="${logoDataUrl}" alt="logo" /></div>`;
  } else if (cfg.pill) {
    pillHtml = `<div class="pill">${escapeHtml(cfg.pill)}</div>`;
  }

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&family=JetBrains+Mono:wght@600;700&display=swap">
<style>
  :root { --accent: ${cfg.accent}; }
  * { box-sizing: border-box; margin: 0; padding: 0; }

  /* Force Chrome to preserve background images (including SVG data URLs) when
     rendering to PDF. Without this, page.pdf() drops the grid layer entirely. */
  html, body {
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }
  body {
    width: ${CARD_W}px;
    height: ${CARD_H}px;
    background: #07070d;
    /* Accent radial overlay only — the grid is now an inline SVG element
       below, because Chrome's PDF rendering ghosts repeating-linear-gradient
       and drops SVG data-URL backgrounds even with print-color-adjust. An
       inline <svg> with <pattern> renders reliably in both PNG and PDF. */
    background-image:
      radial-gradient(ellipse 70% 50% at 50% 50%, ${accentSoft} 0%, transparent 70%);
    font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
    color: #fff;
    position: relative;
    overflow: hidden;
  }

  .grid-bg, .deco {
    position: absolute;
    top: 0; left: 0;
    width: ${CARD_W}px;
    height: ${CARD_H}px;
    pointer-events: none;
  }
  .grid-bg { z-index: 0; }
  .deco { z-index: 1; }

  .pb-logo {
    position: absolute;
    top: 70px; left: 0; right: 0;
    text-align: center;
    z-index: 2;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .pb-logo .mark {
    width: 220px;
    height: auto;
    display: block;
    margin: 0 auto;
  }
  /* Match the PB neon lime sampled from the logo PNG (#48F80D). */
  .pb-logo .row { font-size: 32px; margin-top: 20px; }
  .pb-logo .point { color: #48F80D; }
  .pb-logo .blank { color: #fff; }

  .title {
    position: absolute;
    top: 230px; left: 0; right: 0;
    text-align: center;
    font-size: 86px;
    font-weight: 800;
    letter-spacing: -0.02em;
    /* Smaller blur so the halo doesn't wash out the grid behind the title. */
    text-shadow: 0 0 18px ${titleGlow};
    z-index: 2;
  }

  .avatar {
    position: absolute;
    left: 50%; top: 380px;
    transform: translateX(-50%);
    width: 340px; height: 340px;
    border-radius: 50%;
    overflow: hidden;
    background: #1a1a2e;
    /* The accent-color blur shadow renders as a hard rounded square in
       Chrome's PDF engine on circular elements, so we keep only the
       hard white ring here. */
    box-shadow: 0 0 0 6px rgba(255,255,255,0.6);
  }
  .avatar img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .person-name {
    position: absolute;
    top: 770px; left: 0; right: 0;
    text-align: center;
    font-size: 60px;
    font-weight: 700;
  }

  .sentence {
    position: absolute;
    top: 905px; left: 90px; right: 90px;
    text-align: center;
    font-size: 36px;
    line-height: 1.5;
    font-weight: 500;
  }
  .highlight { color: var(--accent); font-weight: 700; }

  .pill {
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
  }
  .pill.logo-pill {
    padding: 18px 28px;
  }
  .pill.logo-pill img {
    height: 72px;
    max-width: 420px;
    object-fit: contain;
    display: block;
  }
</style>
</head>
<body>
  <svg class="grid-bg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${CARD_W} ${CARD_H}" width="${CARD_W}" height="${CARD_H}" aria-hidden="true">
    <defs>
      <pattern id="grid-pattern" width="52" height="52" patternUnits="userSpaceOnUse">
        <path d="M 52 0 L 0 0 0 52" fill="none" stroke="#969FBE" stroke-opacity="0.34" stroke-width="1"/>
      </pattern>
    </defs>
    <rect width="${CARD_W}" height="${CARD_H}" fill="url(#grid-pattern)"/>
  </svg>

  ${decoSvg}

  <div class="pb-logo">
    ${PB_LOGO_DATA_URL
      ? `<img class="mark" src="${PB_LOGO_DATA_URL}" alt="Point Blank mark" />`
      : `<div style="font-size:56px;color:#48F80D;line-height:1;">&lt;.&gt;</div>`}
    <div class="row"><span class="point">Point</span> <span class="blank">Blank</span></div>
  </div>

  <div class="title">Congratulations</div>

  <div class="avatar"><img src="${photoDataUrl}" alt="profile" /></div>

  <div class="person-name">${escapeHtml(name)}</div>
  <div class="sentence">${sentenceHtml}</div>
  ${pillHtml}
</body>
</html>`;
}

// Returns { png?, pdf? } depending on `formats`.
// Doing both formats reuses a single Puppeteer page, so adding PDF
// only adds ~200ms on top of the PNG render.
async function renderCard({
  type,
  name,
  text,
  photoBuffer,
  photoMime = "image/jpeg",
  logoUrl,
  formats = ["png"],
}) {
  if (!photoBuffer || !photoBuffer.length) {
    throw new Error("Missing profile photo");
  }
  if (!TYPES[type]) {
    throw new Error(`Unknown card type "${type}". Use one of: ${Object.keys(TYPES).join(", ")}`);
  }
  const wantPng = formats.includes("png");
  const wantPdf = formats.includes("pdf");
  if (!wantPng && !wantPdf) {
    throw new Error("renderCard: at least one format (png|pdf) required");
  }

  let logoDataUrl = null;
  const cfg = TYPES[type];
  const effectiveLogoUrl = logoUrl || cfg.logoUrl;
  if (effectiveLogoUrl) {
    try {
      logoDataUrl = await fetchImageAsDataUrl(effectiveLogoUrl);
    } catch (err) {
      if (logoUrl) {
        // User-supplied URL — fail loudly so they fix it.
        throw new Error(`Couldn't fetch logo URL: ${err.message}`);
      }
      // Preset default failed — fall back to the text pill rather than the
      // whole render bombing. Log so we can see it in PM2 output.
      console.warn(`⚠️ Preset logo fetch failed for ${type}: ${err.message} (${effectiveLogoUrl})`);
    }
  }

  const photoDataUrl = `data:${photoMime};base64,${photoBuffer.toString("base64")}`;
  const html = buildHtml({ type, name, text, photoDataUrl, logoDataUrl });

  const puppeteer = require("puppeteer");
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: CARD_W, height: CARD_H, deviceScaleFactor: 1 });
    // Use "load" rather than "networkidle0" — Google Fonts keep-alive can keep
    // a TCP socket open forever, never letting networkidle0 fire. Then wait
    // explicitly for document.fonts.ready so we don't screenshot before glyphs
    // are painted.
    await page.setContent(html, { waitUntil: "load", timeout: 30_000 });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    await new Promise((r) => setTimeout(r, 150));

    const out = {};
    // Return base64 strings directly so the caller doesn't have to deal with
    // Buffer vs Uint8Array differences between Puppeteer versions.
    if (wantPng) {
      // Puppeteer's encoding:"base64" gives us a clean base64 string —
      // skipping the Buffer round-trip avoids the atob-rejected output we
      // were seeing when wrapping the screenshot Uint8Array via Buffer.from().
      out.png = await page.screenshot({ type: "png", encoding: "base64" });
    }
    if (wantPdf) {
      const data = await page.pdf({
        width: `${CARD_W}px`,
        height: `${CARD_H}px`,
        printBackground: true,
        margin: { top: 0, right: 0, bottom: 0, left: 0 },
        preferCSSPageSize: false,
      });
      // page.pdf() has no encoding option, so do the explicit ArrayBuffer
      // view → Buffer conversion (rather than Buffer.from(typedArray), which
      // copies but apparently misreads under this Puppeteer build).
      const buf = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
      out.pdf = buf.toString("base64");
    }
    await page.close();
    return out;
  } finally {
    await browser.close();
  }
}

module.exports = {
  renderCard,
  CARD_TYPES: Object.keys(TYPES),
  CARD_W,
  CARD_H,
};
