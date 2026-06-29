// Achievement card renderer. Loads Puppeteer lazily so bot.js can require
// this module even if puppeteer is missing — the launch only happens when
// renderCard() is actually called.

const CARD_W = 1080;
const CARD_H = 1350;

// Each type controls the connector text and the bottom-pill label.
// "highlight" is the chunk wrapped in the blue accent color.
const TYPES = {
  gsoc: {
    preamble: "on getting selected for",
    pill: "Google Summer of Code",
  },
  lfx: {
    preamble: "on getting selected for",
    highlightPrefix: "LFX (Linux Foundation Mentorship Programme)",
    pill: "LFX Mentorship",
  },
  hackathon: {
    preamble: "on winning",
    pill: "Hackathon Winner",
  },
  competitive: {
    preamble: "on achieving",
    pill: "Competitive Programming",
  },
  acm: {
    preamble: "on getting selected for",
    pill: "ACM Summer / Winter School",
  },
  custom: {
    preamble: "",
    pill: "Point Blank",
  },
};

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildSentenceHtml(cfg, detailsHtml) {
  // Two layouts: with a prefix label (e.g. LFX) or just the details.
  if (cfg.highlightPrefix) {
    return `${escapeHtml(cfg.preamble)} <span class="highlight">${escapeHtml(cfg.highlightPrefix)}</span> ${detailsHtml}!`;
  }
  if (cfg.preamble) {
    return `${escapeHtml(cfg.preamble)} <span class="highlight">${detailsHtml}</span>!`;
  }
  return `<span class="highlight">${detailsHtml}</span>!`;
}

function buildHtml({ type, name, details, photoDataUrl }) {
  const cfg = TYPES[type] ?? TYPES.custom;
  const sentenceHtml = buildSentenceHtml(cfg, escapeHtml(details));

  return `<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;700;800&family=JetBrains+Mono:wght@600;700&display=swap">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    width: ${CARD_W}px;
    height: ${CARD_H}px;
    background: #0a0a12;
    background-image:
      radial-gradient(ellipse 80% 55% at 50% 55%, rgba(40,80,180,0.10) 0%, transparent 70%),
      repeating-linear-gradient(0deg, transparent 0 47px, rgba(80,90,120,0.30) 47px 48px),
      repeating-linear-gradient(90deg, transparent 0 47px, rgba(80,90,120,0.30) 47px 48px);
    font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif;
    color: #fff;
    position: relative;
    overflow: hidden;
  }

  /* Decorative blue blocks bottom-left (rough recreation of the reference) */
  .blocks { position: absolute; left: 0; top: 720px; }
  .blocks div { position: absolute; background: #143064; }
  .b1 { left: 0;   top: 0;   width: 70px;  height: 170px; }
  .b2 { left: 70px; top: 60px; width: 540px; height: 250px; }
  .b3 { left: 0;   top: 360px; width: 220px; height: 240px; }

  /* PB logo */
  .logo {
    position: absolute;
    top: 80px; left: 0; right: 0;
    text-align: center;
    font-family: 'JetBrains Mono', monospace;
    font-weight: 700;
    letter-spacing: 0.05em;
  }
  .logo .bracket { font-size: 56px; color: #2ed573; line-height: 1; }
  .logo .name    { font-size: 32px; margin-top: 6px; }
  .logo .name .point { color: #2ed573; }
  .logo .name .blank { color: #fff; }

  /* Big headline */
  .title {
    position: absolute;
    top: 240px; left: 0; right: 0;
    text-align: center;
    font-size: 80px;
    font-weight: 800;
    letter-spacing: -0.02em;
  }

  /* Profile photo */
  .avatar {
    position: absolute;
    left: 50%; top: 380px;
    transform: translateX(-50%);
    width: 340px; height: 340px;
    border-radius: 50%;
    overflow: hidden;
    background: #1a1a2e;
    box-shadow:
      0 0 0 6px rgba(255,255,255,0.55),
      0 0 40px rgba(255,255,255,0.08);
    z-index: 3;
  }
  .avatar img { width: 100%; height: 100%; object-fit: cover; object-position: center; display: block; }

  /* Name */
  .person-name {
    position: absolute;
    top: 770px; left: 0; right: 0;
    text-align: center;
    font-size: 60px;
    font-weight: 700;
    z-index: 3;
  }

  /* Achievement sentence */
  .sentence {
    position: absolute;
    top: 900px; left: 100px; right: 100px;
    text-align: center;
    font-size: 36px;
    line-height: 1.45;
    font-weight: 500;
    z-index: 3;
  }
  .sentence .highlight { color: #6fb3ff; font-weight: 600; }

  /* Bottom pill (placeholder for org logo — swap with an <img> later) */
  .pill {
    position: absolute;
    bottom: 110px; left: 50%;
    transform: translateX(-50%);
    background: #ffffff;
    color: #1a2238;
    padding: 22px 44px;
    border-radius: 18px;
    font-weight: 700;
    font-size: 28px;
    letter-spacing: 0.01em;
    z-index: 3;
    white-space: nowrap;
  }
</style>
</head>
<body>
  <div class="blocks">
    <div class="b1"></div>
    <div class="b2"></div>
    <div class="b3"></div>
  </div>

  <div class="logo">
    <div class="bracket">&lt;.&gt;</div>
    <div class="name"><span class="point">Point</span> <span class="blank">Blank</span></div>
  </div>

  <div class="title">Congratulations</div>

  <div class="avatar"><img src="${photoDataUrl}" /></div>

  <div class="person-name">${escapeHtml(name)}</div>
  <div class="sentence">${sentenceHtml}</div>
  <div class="pill">${escapeHtml(cfg.pill)}</div>
</body>
</html>`;
}

async function renderCard({ type, name, details, photoBuffer, photoMime = "image/jpeg" }) {
  if (!photoBuffer || !photoBuffer.length) {
    throw new Error("Missing photoBuffer");
  }
  const cfg = TYPES[type];
  if (!cfg) throw new Error(`Unknown card type "${type}". Use one of: ${Object.keys(TYPES).join(", ")}`);

  const photoDataUrl = `data:${photoMime};base64,${photoBuffer.toString("base64")}`;
  const html = buildHtml({ type, name, details, photoDataUrl, cfg });

  // Lazy-load puppeteer so bot.js startup doesn't fail if puppeteer is somehow missing.
  const puppeteer = require("puppeteer");
  const browser = await puppeteer.launch({
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: CARD_W, height: CARD_H, deviceScaleFactor: 1 });
    await page.setContent(html, { waitUntil: "networkidle0", timeout: 30_000 });
    // Tiny extra wait so web fonts settle. networkidle should be enough but cheap insurance.
    await new Promise((r) => setTimeout(r, 250));
    const buf = await page.screenshot({ type: "png", omitBackground: false });
    await page.close();
    return buf;
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
