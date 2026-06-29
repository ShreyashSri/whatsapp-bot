// One-off: sample the average non-transparent color in pb-logo.png.
// Run: node scripts/sample-color.js
const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer");

const LOGO_PATH = path.join(__dirname, "..", "cards", "assets", "pb-logo.png");

(async () => {
  const b64 = fs.readFileSync(LOGO_PATH).toString("base64");
  const browser = await puppeteer.launch({ headless: true, args: ["--no-sandbox"] });
  const page = await browser.newPage();
  await page.setContent(`<body style="margin:0;background:#000;"><img id="x" src="data:image/png;base64,${b64}"/></body>`);
  await page.evaluate(() => new Promise((r) => {
    const img = document.getElementById("x");
    if (img.complete) r(); else img.onload = () => r();
  }));
  const sample = await page.evaluate(() => {
    const img = document.getElementById("x");
    const c = document.createElement("canvas");
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0);
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < data.length; i += 4) {
      if (data[i + 3] > 200) { r += data[i]; g += data[i + 1]; b += data[i + 2]; n++; }
    }
    const avg = (v) => Math.round(v / n);
    return { r: avg(r), g: avg(g), b: avg(b), n };
  });
  await browser.close();
  const hex = "#" + [sample.r, sample.g, sample.b].map((v) => v.toString(16).padStart(2, "0")).join("");
  console.log(`Average of ${sample.n} opaque pixels: rgb(${sample.r}, ${sample.g}, ${sample.b}) -> ${hex.toUpperCase()}`);
})();
