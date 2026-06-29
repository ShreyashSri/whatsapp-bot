// One-off helper: trim the transparent padding around cards/assets/pb-logo.png
// and overwrite it with the tight crop. Run with: node scripts/trim-logo.js

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const LOGO_PATH = path.join(__dirname, "..", "cards", "assets", "pb-logo.png");

(async () => {
  const original = fs.readFileSync(LOGO_PATH);
  const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setContent(
    `<body style="margin:0;padding:0;background:transparent;">` +
    `<img id="x" src="data:image/png;base64,${original.toString("base64")}"/>` +
    `</body>`
  );
  await page.evaluate(() => new Promise((r) => {
    const img = document.getElementById("x");
    if (img.complete) r(); else img.onload = () => r();
  }));

  const result = await page.evaluate(() => {
    const img = document.getElementById("x");
    const w = img.naturalWidth, h = img.naturalHeight;
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, w, h).data;
    let minX = w, minY = h, maxX = -1, maxY = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const a = data[(y * w + x) * 4 + 3];
        if (a > 10) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX < 0) return null;
    const cropW = maxX - minX + 1;
    const cropH = maxY - minY + 1;
    const out = document.createElement("canvas");
    out.width = cropW; out.height = cropH;
    out.getContext("2d").drawImage(c, minX, minY, cropW, cropH, 0, 0, cropW, cropH);
    return { dataUrl: out.toDataURL("image/png"), w: cropW, h: cropH, originalW: w, originalH: h };
  });

  await browser.close();
  if (!result) {
    console.error("All pixels were transparent — refusing to overwrite.");
    process.exit(1);
  }

  const b64 = result.dataUrl.replace(/^data:image\/png;base64,/, "");
  fs.writeFileSync(LOGO_PATH, Buffer.from(b64, "base64"));
  console.log(`Trimmed ${result.originalW}x${result.originalH} -> ${result.w}x${result.h}`);
})();
