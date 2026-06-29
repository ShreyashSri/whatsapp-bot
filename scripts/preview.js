// Local card preview. Pulls a placeholder photo, renders a sample card,
// writes preview-card.png and preview-card.pdf to the repo root.
//
//   node scripts/preview.js                # default: lfx, sample text
//   node scripts/preview.js gsoc           # different type
//   node scripts/preview.js gsoc "Custom name" "Some text with [highlight]"
//   node scripts/preview.js custom "Name" "Text" "https://example.com/logo.png"

const fs = require("fs");
const path = require("path");
const { renderCard } = require("../cards/render.js");

const type = process.argv[2] || "lfx";
const name = process.argv[3] || "Shivansh Pandey";
const text = process.argv[4] || "For getting selected as mentee in [LFX] 2026 in [Headlamp]";
const logoUrl = process.argv[5]; // optional 4th positional

const OUT_PNG = path.join(__dirname, "..", "preview-card.png");
const OUT_PDF = path.join(__dirname, "..", "preview-card.pdf");

(async () => {
  // Grab a square placeholder photo so the avatar crop reads as a portrait.
  const photoUrl = "https://picsum.photos/seed/pb-card/400/400";
  const photoRes = await fetch(photoUrl, { redirect: "follow" });
  if (!photoRes.ok) throw new Error(`Couldn't fetch placeholder photo: HTTP ${photoRes.status}`);
  const photoBuffer = Buffer.from(await photoRes.arrayBuffer());

  console.log(`Rendering type=${type} name="${name}"`);
  const t0 = Date.now();
  const out = await renderCard({
    type,
    name,
    text,
    photoBuffer,
    photoMime: photoRes.headers.get("content-type") || "image/jpeg",
    logoUrl,
    formats: ["png", "pdf"],
  });
  console.log(`Rendered in ${Date.now() - t0}ms`);

  fs.writeFileSync(OUT_PNG, Buffer.from(out.png, "base64"));
  fs.writeFileSync(OUT_PDF, Buffer.from(out.pdf, "base64"));
  console.log(`PNG: ${OUT_PNG}`);
  console.log(`PDF: ${OUT_PDF}`);
})().catch((err) => {
  console.error("Preview failed:", err);
  process.exit(1);
});
