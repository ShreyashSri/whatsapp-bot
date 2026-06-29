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

const TYPES = {
  gsoc:        { accent: "#FBBC04", pill: "Google Summer of Code" },
  lfx:         { accent: "#5C9BD6", pill: "The Linux Foundation" },
  hackathon:   { accent: "#A855F7", pill: "Hackathon Winner" },
  competitive: { accent: "#2ED573", pill: "Competitive Programming" },
  acm:         { accent: "#F5A623", pill: "ACM Summer / Winter School" },
  internship:  { accent: "#00BCD4", pill: null }, // logo can be provided via logoUrl
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

function buildHtml({ type, name, text, photoDataUrl, logoDataUrl }) {
  const cfg = TYPES[type] ?? TYPES.custom;
  const sentenceHtml = processHighlights(escapeHtml(text));
  const accentSoft = hexToRgba(cfg.accent, 0.07);
  const titleGlow = hexToRgba(cfg.accent, 0.28);
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

  body {
    width: ${CARD_W}px;
    height: ${CARD_H}px;
    background: #07070d;
    background-image:
      radial-gradient(ellipse 70% 50% at 50% 50%, ${accentSoft} 0%, transparent 70%),
      repeating-linear-gradient(0deg, transparent 0 51px, rgba(80,90,120,0.22) 51px 52px),
      repeating-linear-gradient(90deg, transparent 0 51px, rgba(80,90,120,0.22) 51px 52px);
    font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
    color: #fff;
    position: relative;
    overflow: hidden;
  }

  .pb-logo {
    position: absolute;
    top: 70px; left: 0; right: 0;
    text-align: center;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .pb-logo .bracket { font-size: 56px; color: #2ed573; line-height: 1; }
  .pb-logo .row { font-size: 32px; margin-top: 4px; }
  .pb-logo .point { color: #2ed573; }
  .pb-logo .blank { color: #fff; }

  .title {
    position: absolute;
    top: 230px; left: 0; right: 0;
    text-align: center;
    font-size: 86px;
    font-weight: 800;
    letter-spacing: -0.02em;
    text-shadow: 0 0 32px ${titleGlow};
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
  <div class="pb-logo">
    <div class="bracket">&lt;.&gt;</div>
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
  if (logoUrl) {
    try {
      const res = await fetch(logoUrl, { redirect: "follow" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const mime = (res.headers.get("content-type") || "").toLowerCase().split(";")[0].trim();
      if (!mime.startsWith("image/")) {
        throw new Error(`URL is not an image (content-type: ${mime || "unknown"})`);
      }
      const buf = Buffer.from(await res.arrayBuffer());
      logoDataUrl = `data:${mime};base64,${buf.toString("base64")}`;
    } catch (err) {
      throw new Error(`Couldn't fetch logo URL: ${err.message}`);
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
    await page.setContent(html, { waitUntil: "networkidle0", timeout: 30_000 });
    await new Promise((r) => setTimeout(r, 300));

    const out = {};
    if (wantPng) {
      // Puppeteer 21+ returns Uint8Array, not Buffer. Normalize so
      // .toString("base64") on the consumer side actually produces base64.
      const data = await page.screenshot({ type: "png" });
      out.png = Buffer.isBuffer(data) ? data : Buffer.from(data);
    }
    if (wantPdf) {
      const data = await page.pdf({
        width: `${CARD_W}px`,
        height: `${CARD_H}px`,
        printBackground: true,
        margin: { top: 0, right: 0, bottom: 0, left: 0 },
        preferCSSPageSize: false,
      });
      out.pdf = Buffer.isBuffer(data) ? data : Buffer.from(data);
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
