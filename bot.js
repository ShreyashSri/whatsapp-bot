require("dotenv").config();
const fs = require("fs");
const path = require("path");
const { Client, LocalAuth, MessageMedia } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const { MongoClient } = require("mongodb");
const cron = require("node-cron");
const { renderCard, CARD_TYPES } = require("./cards/render.js");

const MONGO_URI = process.env.MONGO_URI;
const GROUP_ID = process.env.GROUP_ID;

if (!MONGO_URI || !GROUP_ID) {
  console.error("❌ Missing required env vars: MONGO_URI, GROUP_ID. Copy .env.example to .env and fill in values.");
  process.exit(1);
}

// CTF groups — stats, easter eggs, stickers
const GROUP_IDS = [GROUP_ID];

// Media-team group — task manager (!add / !remove / !to-do / !posted / !posted-list / !help).
// Configured at runtime by sending `!set-media` from this bot's own WhatsApp number
// inside the target group. Persisted to media-group.json (server-only, gitignored).
const MEDIA_GROUP_FILE = path.join(__dirname, "media-group.json");
const mediaGroupIds = new Set();

// Trigger words that fetch and display current stats
const TRIGGER_WORDS = ["!stats"];

// Trigger words that send the saved custom sticker
const STICKER_TRIGGER_WORDS = ["!sticker", "shreyash", "shreyansh"];
const STICKER_DIR_PATH = path.join(__dirname, "stickers");
const STICKER_FILE_EXTENSIONS = new Set([".webp", ".png", ".jpg", ".jpeg"]);

function hasTriggerWord(words, triggerWords) {
  return triggerWords.some((word) => words.includes(word.toLowerCase()));
}

async function sendCustomSticker(client, chatId) {
  if (!fs.existsSync(STICKER_DIR_PATH)) {
    throw new Error(`Sticker folder not found at ${STICKER_DIR_PATH}`);
  }

  const stickerFiles = fs
    .readdirSync(STICKER_DIR_PATH)
    .filter((file) => STICKER_FILE_EXTENSIONS.has(path.extname(file).toLowerCase()));

  if (stickerFiles.length === 0) {
    throw new Error(`No sticker files found in ${STICKER_DIR_PATH}`);
  }

  const randomStickerFile = stickerFiles[Math.floor(Math.random() * stickerFiles.length)];
  const randomStickerPath = path.join(STICKER_DIR_PATH, randomStickerFile);
  const sticker = MessageMedia.fromFilePath(randomStickerPath);

  await client.sendMessage(chatId, sticker, {
    sendMediaAsSticker: true,
    stickerName: "PBCTF",
    stickerAuthor: "PointBlank",
  });
}

async function fetchStats() {
  const mongo = new MongoClient(MONGO_URI);
  try {
    await mongo.connect();
    const db = mongo.db();

    const istOffsetMs = 5.5 * 60 * 60 * 1000;
    const nowIST = new Date(Date.now() + istOffsetMs);
    const startOfTodayIST = new Date(
      Date.UTC(nowIST.getUTCFullYear(), nowIST.getUTCMonth(), nowIST.getUTCDate()) - istOffsetMs
    );

    const todayFilter = { createdAt: { $gte: startOfTodayIST } };

    const [totalUsers, totalTeams, usersToday, teamsToday] = await Promise.all([
      db.collection("users").countDocuments(),
      db.collection("teams").countDocuments(),
      db.collection("users").countDocuments(todayFilter),
      db.collection("teams").countDocuments(todayFilter),
    ]);

    return { totalUsers, totalTeams, usersToday, teamsToday };
  } finally {
    await mongo.close();
  }
}

function buildMessage({ totalUsers, totalTeams, usersToday, teamsToday }, title = "Daily Stats Update") {
  const now = new Date().toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "full",
    timeStyle: "short",
  });

  return (
    `📊 *PBCTF: ${title}*\n` +
    `🗓️ ${now}\n\n` +
    `*Overall*\n` +
    `👤 Total Users: ${totalUsers}\n` +
    `🏁 Total Teams: ${totalTeams}\n\n` +
    `*Registered Today*\n` +
    `🆕 New Users: ${usersToday}\n` +
    `🆕 New Teams: ${teamsToday}`
  );
}

// ---------- Media-team task manager ----------

function readMediaGroupId() {
  if (!fs.existsSync(MEDIA_GROUP_FILE)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(MEDIA_GROUP_FILE, "utf8"));
    return typeof data?.groupId === "string" ? data.groupId : null;
  } catch (err) {
    console.error("❌ media-group.json corrupt, ignoring:", err.message);
    return null;
  }
}

function writeMediaGroupId(groupId) {
  fs.writeFileSync(MEDIA_GROUP_FILE, JSON.stringify({ groupId }, null, 2));
}

(function loadMediaGroupOnStartup() {
  const stored = readMediaGroupId();
  if (stored) {
    mediaGroupIds.add(stored);
    console.log(`📌 Media group loaded from media-group.json: ${stored}`);
  } else {
    console.log("ℹ️ No media group configured. Send `!set-media` from this bot's WhatsApp in the target group to set one.");
  }
})();

const POSTS_FILE = path.join(__dirname, "posts.json");
// `design` is a pre-posting checkbox (the creative is ready); the other three
// are the destinations. All four must flip true for an entry to move to posted.
const PLATFORMS = ["design", "instagram", "linkedin", "twitter"];
const PLATFORM_ALIASES = {
  design: "design", d: "design", des: "design",
  insta: "instagram", instagram: "instagram", ig: "instagram",
  linkedin: "linkedin", li: "linkedin",
  twitter: "twitter", x: "twitter", tw: "twitter",
};
function emptyPlatformFlags() {
  return PLATFORMS.reduce((acc, p) => { acc[p] = false; return acc; }, {});
}
function normalizeEntryFlags(entry) {
  // Old entries (pre-design) won't have every key — fill missing as false.
  const next = emptyPlatformFlags();
  if (entry.platforms) {
    for (const p of PLATFORMS) {
      if (typeof entry.platforms[p] === "boolean") next[p] = entry.platforms[p];
    }
  }
  entry.platforms = next;
  return entry;
}

function readPosts() {
  if (!fs.existsSync(POSTS_FILE)) return { nextId: 1, todo: [], posted: [] };
  try {
    const data = JSON.parse(fs.readFileSync(POSTS_FILE, "utf8"));
    const todo = Array.isArray(data.todo) ? data.todo.map(normalizeEntryFlags) : [];
    const posted = Array.isArray(data.posted) ? data.posted.map(normalizeEntryFlags) : [];
    return { nextId: data.nextId ?? 1, todo, posted };
  } catch (err) {
    console.error("❌ posts.json corrupt, starting fresh:", err.message);
    return { nextId: 1, todo: [], posted: [] };
  }
}

function writePosts(state) {
  fs.writeFileSync(POSTS_FILE, JSON.stringify(state, null, 2));
}

function normalizePlatform(input) {
  if (!input) return null;
  return PLATFORM_ALIASES[input.toLowerCase()] ?? null;
}

function platformStatusLine(entry) {
  return PLATFORMS
    .map((p) => `${p[0].toUpperCase()}${p.slice(1)}: ${entry.platforms[p] ? "✅" : "⬜"}`)
    .join(" • ");
}

function formatTodoEntry(entry) {
  return `*#${entry.id}* — ${entry.text}\n   ${platformStatusLine(entry)}`;
}

function formatPostedEntry(entry) {
  const when = new Date(entry.postedAt ?? entry.createdAt).toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "medium",
    timeStyle: "short",
  });
  return `*#${entry.id}* — ${entry.text}\n   Posted: ${when}`;
}

const HELP_TEXT =
  `*📋 Task Manager Commands*\n\n` +
  `\`!add <text>\` — add a post to to-do\n` +
  `\`!remove <id>\` — remove a post (works on both lists)\n` +
  `\`!to-do\` — list pending posts\n` +
  `\`!posted <id> <stage>\` — mark a stage done\n` +
  `\`!unposted <id> <stage>\` — un-mark a stage\n` +
  `\`!posted-list\` — list fully posted entries\n` +
  `\`!card <type> | <name> | <text>\` — generate a Congratulations card (attach a photo)\n` +
  `\`!card-pdf <type> | <name> | <text>\` — same, plus an editable PDF\n` +
  `\`!help [command]\` — this message, or details for one command\n\n` +
  `Type \`!help <command>\` (e.g. \`!help card\`) for full usage.\n\n` +
  `_Stages:_ design • instagram • linkedin • twitter\n` +
  `_Card types:_ ${CARD_TYPES.join(", ")}`;

const COMMAND_HELP = {
  help:
    `*!help [command]*\n\n` +
    `Without args, shows the command list. Pass a command name for detailed usage:\n` +
    `\`!help card\`, \`!help posted\`, \`!help add\`, etc.`,

  add:
    `*!add <text>*\n\n` +
    `Adds a post to the to-do list. The bot replies with the assigned id (#1, #2, ...). ` +
    `The text can be anything you want to track — a topic, a draft idea, a link.\n\n` +
    `*Example:*\n\`!add Post about Shubhang's LFX selection\``,

  remove:
    `*!remove <id>*\n\n` +
    `Removes an entry by id. Searches both the to-do list and the posted list, so you can clean up either.\n\n` +
    `*Example:*\n\`!remove 3\``,

  todo:
    `*!to-do*\n\n` +
    `Lists every pending post with its stage checkboxes (design, instagram, linkedin, twitter). ` +
    `Alias: \`!todo\`.`,

  posted:
    `*!posted <id> <stage>*\n\n` +
    `Marks one stage as done for a post. When all four stages are marked, the entry auto-moves to posted.\n\n` +
    `*Stages and aliases:*\n` +
    `• design — \`d\`, \`des\`\n` +
    `• instagram — \`insta\`, \`ig\`\n` +
    `• linkedin — \`li\`\n` +
    `• twitter — \`x\`, \`tw\`\n\n` +
    `*Example:*\n\`!posted 3 insta\``,

  unposted:
    `*!unposted <id> <stage>*\n\n` +
    `Un-marks one stage. If the entry was already fully posted, it moves back to to-do so the workflow can resume.\n\n` +
    `*Example:*\n\`!unposted 3 insta\``,

  "posted-list":
    `*!posted-list*\n\n` +
    `Lists entries that have all four stages marked.`,

  card:
    `*!card <type> | <name> | <text>* (\\| <logoUrl>)\n` +
    `*!card-pdf <type> | <name> | <text>* — same as !card, plus an editable PDF\n\n` +
    `Generates a Congratulations card. Attach a profile photo to the *same* message.\n\n` +
    `The *type* controls only the accent color and the bottom pill — the text is yours. ` +
    `Wrap any phrase in [brackets] to highlight it in the accent color.\n\n` +
    `*Types:*\n` +
    `• \`gsoc\` — gold, "Google Summer of Code" pill\n` +
    `• \`lfx\` — blue, "The Linux Foundation" pill\n` +
    `• \`hackathon\` — purple, "Hackathon Winner" pill\n` +
    `• \`competitive\` — green, "Competitive Programming" pill\n` +
    `• \`acm\` — orange, "ACM Summer / Winter School" pill\n` +
    `• \`internship\` — cyan, no pill by default. Pass a logo URL as a 4th part to put the company logo in the pill.\n` +
    `• \`custom\` — white, no pill. Also accepts a logo URL.\n\n` +
    `*Examples:*\n` +
    `\`!card gsoc | Manas Hejmadi | For getting selected as mentor in [Google Summer of Code] 2026 with [API Dash]\`\n\n` +
    `\`!card-pdf lfx | Shubhang Sinha | For being a [LiFT Scholarship] holder for 2026\`\n\n` +
    `\`!card internship | Priya | Joining [Anthropic] as a Software Engineer Intern | https://example.com/anthropic.png\`\n\n` +
    `_PDFs are rendered with text-as-text, so they can be edited in Illustrator, Inkscape, or Figma._`,

  "set-media":
    `*!set-media*\n\n` +
    `Self-command sent by this bot's own WhatsApp number inside the target group, to register that group as the media-team group. The group id is saved on the server and persists across restarts. Send it again in another group to switch.`,
};

async function handleCardCommand(msg, body, cmdPrefix, { withPdf }) {
  const rest = body.slice(cmdPrefix.length).trim();
  const parts = rest.includes("\n")
    ? rest.split("\n").map((s) => s.trim())
    : rest.split("|").map((s) => s.trim());
  const [rawType, name, text, logoUrl] = parts;

  if (!rawType || !name || !text) {
    await msg.reply(COMMAND_HELP.card);
    return;
  }
  const type = rawType.toLowerCase();
  if (!CARD_TYPES.includes(type)) {
    await msg.reply(`⚠️ Unknown card type "${rawType}". Use one of: ${CARD_TYPES.join(", ")}\n\nSee \`!help card\` for details.`);
    return;
  }
  if (!msg.hasMedia) {
    await msg.reply(`⚠️ Attach a profile photo to the same message as the \`${cmdPrefix}\` command.\n\nSee \`!help card\` for details.`);
    return;
  }

  try {
    await msg.reply(`🎨 Rendering ${type} card for ${name}${withPdf ? " (PNG + PDF)" : ""}...`);
    const media = await msg.downloadMedia();
    if (!media || !media.data) {
      await msg.reply("❌ Couldn't download the attached media.");
      return;
    }
    const photoBuffer = Buffer.from(media.data, "base64");
    const formats = withPdf ? ["png", "pdf"] : ["png"];
    const out = await renderCard({
      type, name, text,
      photoBuffer,
      photoMime: media.mimetype || "image/jpeg",
      logoUrl: logoUrl || undefined,
      formats,
    });
    const safeName = name.replace(/[^a-zA-Z0-9-_]+/g, "-").slice(0, 60) || "card";

    // out.png / out.pdf are already base64 strings from render.js.
    if (out.png) {
      const pngMedia = new MessageMedia("image/png", out.png, `${safeName}-card.png`);
      await msg.reply(pngMedia, undefined, { caption: `🎉 ${name}` });
    }
    if (out.pdf) {
      const pdfMedia = new MessageMedia("application/pdf", out.pdf, `${safeName}-card.pdf`);
      await msg.reply(pdfMedia, undefined, { caption: `📄 ${name} — editable PDF` });
    }
    console.log(`✅ Card rendered: type=${type} name="${name}" formats=${formats.join("+")}`);
  } catch (err) {
    console.error("❌ Card render error:", err);
    await msg.reply(`❌ Card render failed: ${err.message}`);
  }
}

async function handleMediaCommand(msg) {
  const body = msg.body.trim();
  if (!body.startsWith("!")) return;

  const lower = body.toLowerCase();

  if (lower === "!help") {
    await msg.reply(HELP_TEXT);
    return;
  }
  if (lower.startsWith("!help ")) {
    const cmd = lower.slice(6).trim().replace(/^!/, "");
    const aliases = { todo: "todo", "to-do": "todo", "card-pdf": "card" };
    const key = aliases[cmd] ?? cmd;
    const detail = COMMAND_HELP[key];
    if (!detail) {
      await msg.reply(
        `⚠️ No detailed help for "${cmd}".\nKnown: ${Object.keys(COMMAND_HELP).map((k) => "`!" + k + "`").join(", ")}`
      );
      return;
    }
    await msg.reply(detail);
    return;
  }

  if (lower === "!to-do" || lower === "!todo") {
    const state = readPosts();
    if (state.todo.length === 0) {
      await msg.reply("📭 To-do list is empty.");
      return;
    }
    const lines = state.todo.map(formatTodoEntry).join("\n\n");
    await msg.reply(`*📋 To-do (${state.todo.length})*\n\n${lines}`);
    return;
  }

  if (lower === "!posted-list") {
    const state = readPosts();
    if (state.posted.length === 0) {
      await msg.reply("📭 No posts marked fully posted yet.");
      return;
    }
    const lines = state.posted.map(formatPostedEntry).join("\n\n");
    await msg.reply(`*✅ Posted (${state.posted.length})*\n\n${lines}`);
    return;
  }

  if (lower === "!add" || lower.startsWith("!add ")) {
    const text = body.slice(4).trim();
    if (!text) {
      await msg.reply("⚠️ Usage: `!add <text>`");
      return;
    }
    const state = readPosts();
    const entry = {
      id: state.nextId,
      text,
      createdAt: new Date().toISOString(),
      createdBy: msg.author ?? msg.from,
      platforms: emptyPlatformFlags(),
    };
    state.todo.push(entry);
    state.nextId += 1;
    writePosts(state);
    await msg.reply(`✅ Added *#${entry.id}* — ${entry.text}`);
    return;
  }

  if (lower === "!remove" || lower.startsWith("!remove ")) {
    const idStr = body.slice(7).trim().replace(/^#/, "");
    const id = parseInt(idStr, 10);
    if (!Number.isInteger(id)) {
      await msg.reply("⚠️ Usage: `!remove <id>`");
      return;
    }
    const state = readPosts();
    const todoIdx = state.todo.findIndex((e) => e.id === id);
    const postedIdx = state.posted.findIndex((e) => e.id === id);
    let removed;
    let where;
    if (todoIdx !== -1) {
      [removed] = state.todo.splice(todoIdx, 1);
      where = "to-do";
    } else if (postedIdx !== -1) {
      [removed] = state.posted.splice(postedIdx, 1);
      where = "posted";
    } else {
      await msg.reply(`❌ No entry with id *#${id}*.`);
      return;
    }
    writePosts(state);
    await msg.reply(`🗑️ Removed *#${removed.id}* from ${where} — ${removed.text}`);
    return;
  }

  if (lower === "!posted" || lower.startsWith("!posted ")) {
    const args = body.slice(7).trim().split(/\s+/).filter(Boolean);
    if (args.length < 2) {
      await msg.reply("⚠️ Usage: `!posted <id> <stage>` (stage: design / insta / linkedin / twitter)");
      return;
    }
    const id = parseInt(args[0].replace(/^#/, ""), 10);
    if (!Number.isInteger(id)) {
      await msg.reply("⚠️ Id must be a number. Usage: `!posted <id> <stage>`");
      return;
    }
    const platform = normalizePlatform(args[1]);
    if (!platform) {
      await msg.reply(
        `⚠️ Unknown stage "${args[1]}". Use one of: design (d), instagram (insta / ig), linkedin (li), twitter (x / tw).`
      );
      return;
    }
    const state = readPosts();
    const entry = state.todo.find((e) => e.id === id);
    if (!entry) {
      await msg.reply(
        `❌ No to-do entry with id *#${id}*. (If it's already fully posted, check \`!posted-list\`.)`
      );
      return;
    }
    const wasAlready = entry.platforms[platform];
    entry.platforms[platform] = true;
    const allDone = PLATFORMS.every((p) => entry.platforms[p]);
    if (allDone) {
      const idx = state.todo.findIndex((e) => e.id === id);
      state.todo.splice(idx, 1);
      entry.postedAt = new Date().toISOString();
      state.posted.push(entry);
    }
    writePosts(state);

    const header = wasAlready
      ? `ℹ️ *#${id}* was already marked on ${platform}.`
      : `✅ *#${id}* marked posted on ${platform}.`;
    const footer = allDone ? `\n\n🎉 All stages done — moved to posted.` : "";
    await msg.reply(`${header}\n${platformStatusLine(entry)}${footer}`);
    return;
  }

  if (lower === "!card-pdf" || lower.startsWith("!card-pdf ") || lower.startsWith("!card-pdf\n")) {
    await handleCardCommand(msg, body, "!card-pdf", { withPdf: true });
    return;
  }
  if (lower === "!card" || lower.startsWith("!card ") || lower.startsWith("!card\n")) {
    await handleCardCommand(msg, body, "!card", { withPdf: false });
    return;
  }

  if (lower === "!unposted" || lower.startsWith("!unposted ")) {
    const args = body.slice(9).trim().split(/\s+/).filter(Boolean);
    if (args.length < 2) {
      await msg.reply("⚠️ Usage: `!unposted <id> <stage>` (stage: design / insta / linkedin / twitter)");
      return;
    }
    const id = parseInt(args[0].replace(/^#/, ""), 10);
    if (!Number.isInteger(id)) {
      await msg.reply("⚠️ Id must be a number. Usage: `!unposted <id> <stage>`");
      return;
    }
    const platform = normalizePlatform(args[1]);
    if (!platform) {
      await msg.reply(
        `⚠️ Unknown stage "${args[1]}". Use one of: design (d), instagram (insta / ig), linkedin (li), twitter (x / tw).`
      );
      return;
    }
    const state = readPosts();
    let entry = state.todo.find((e) => e.id === id);
    let movedBackFromPosted = false;
    if (!entry) {
      const postedIdx = state.posted.findIndex((e) => e.id === id);
      if (postedIdx !== -1) {
        entry = state.posted[postedIdx];
        state.posted.splice(postedIdx, 1);
        delete entry.postedAt;
        state.todo.push(entry);
        movedBackFromPosted = true;
      }
    }
    if (!entry) {
      await msg.reply(`❌ No entry with id *#${id}*.`);
      return;
    }
    const wasMarked = entry.platforms[platform];
    entry.platforms[platform] = false;
    writePosts(state);

    const header = wasMarked
      ? `↩️ *#${id}* un-marked on ${platform}.`
      : `ℹ️ *#${id}* was not marked on ${platform}.`;
    const footer = movedBackFromPosted ? `\n\n📋 Moved back to to-do.` : "";
    await msg.reply(`${header}\n${platformStatusLine(entry)}${footer}`);
    return;
  }
}

// ---------- /Media-team task manager ----------

function createClient() {
  return new Client({
    authStrategy: new LocalAuth({ dataPath: "./.wwebjs_auth" }),
    puppeteer: {
      headless: true,
      args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    },
  });
}

async function start() {
  const client = createClient();

  client.on("qr", (qr) => {
    console.log("\n📱 Scan QR with WhatsApp → ⋮ → Linked Devices → Link a Device\n");
    qrcode.generate(qr, { small: true });
  });

  client.on("auth_failure", (msg) => { console.error("❌ Auth failed:", msg); process.exit(1); });

  client.on("disconnected", (reason) => {
    console.warn("⚠️ Disconnected:", reason, "— restarting in 10s");
    setTimeout(() => start(), 10_000);
  });

  client.on("ready", () => {
    console.log("✅ Bot ready — listening for messages and scheduling nightly stats");

    // 8 PM IST = 14:30 UTC
    cron.schedule("30 14 * * *", async () => {
      try {
        const stats = await fetchStats();
        await Promise.all(
          GROUP_IDS.map((groupId) => client.sendMessage(groupId, buildMessage(stats)))
        );
        console.log(`✅ Nightly stats sent to ${GROUP_IDS.length} group(s)`);
      } catch (err) {
        console.error("❌ Nightly stats error:", err.message);
      }
    }, { timezone: "UTC" });
  });

  client.on("message", async (msg) => {
    if (GROUP_IDS.includes(msg.from)) {
      const text = msg.body.toLowerCase();
      const words = text.split(/\s+/);

      if (text.includes("bytexync")) {
        try {
          await msg.reply("bytexync ki mkc");
          console.log(`🥚 Easter egg triggered by ${msg.author}`);
        } catch (err) {
          console.error("❌ Reply error:", err.message);
        }
      } else if (text.includes("pointblank")) {
        try {
          await msg.reply("love you bro");
          console.log(`🥚 Easter egg triggered by ${msg.author}`);
        } catch (err) {
          console.error("❌ Reply error:", err.message);
        }
      } else if (hasTriggerWord(words, STICKER_TRIGGER_WORDS)) {
        try {
          await sendCustomSticker(client, msg.from);
          console.log(`✅ Custom sticker sent (requested by ${msg.author})`);
        } catch (err) {
          console.error("❌ Custom sticker error:", err.message);
        }
      } else if (hasTriggerWord(words, TRIGGER_WORDS)) {
        try {
          const stats = await fetchStats();
          await msg.reply(buildMessage(stats, "Current Stats"));
          console.log(`✅ On-demand stats sent (requested by ${msg.author})`);
        } catch (err) {
          console.error("❌ On-demand stats error:", err.message);
        }
      }
      return;
    }

    if (mediaGroupIds.has(msg.from)) {
      try {
        await handleMediaCommand(msg);
      } catch (err) {
        console.error("❌ Media command error:", err.message);
      }
    }
  });

  // Self-sent admin command from this bot's own WhatsApp number.
  // `message_create` fires for outgoing messages too (unlike `message`), so we can pick this up.
  client.on("message_create", async (msg) => {
    if (!msg.fromMe) return;
    const body = (msg.body || "").trim();
    if (body !== "!set-media") return;

    try {
      let chat = null;
      try {
        chat = await msg.getChat();
      } catch (err) {
        console.error("❌ getChat failed:", err.message);
      }

      if (!chat?.isGroup) {
        console.warn(`⚠️ !set-media in non-group chat. from=${msg.from} isGroup=${chat?.isGroup}`);
        await msg.reply(
          `⚠️ \`!set-media\` only works inside a group chat.\n(chat id: \`${msg.from}\`)`
        );
        return;
      }
      // For outgoing (fromMe) messages whatsapp-web.js sets msg.from to the sender's own JID,
      // not the chat — incoming messages see the chat in msg.from. Use chat.id._serialized so
      // both events agree on what's stored.
      const chatId = chat.id?._serialized;
      if (!chatId) {
        await msg.reply("❌ Could not resolve chat id from this group. Try again.");
        return;
      }
      if (GROUP_IDS.includes(chatId)) {
        await msg.reply("⚠️ This is the CTF group. Refusing to make it the media group.");
        return;
      }

      mediaGroupIds.clear();
      mediaGroupIds.add(chatId);
      writeMediaGroupId(chatId);

      const chatName = chat.name ?? chatId;
      await msg.reply(
        `✅ Media group set to *${chatName}*.\n\nTask-manager commands are now live here. Send \`!help\` for the list.`
      );
      console.log(`✅ Media group set to ${chatId} (${chatName}); message msg.from was ${msg.from}`);
    } catch (err) {
      console.error("❌ !set-media error:", err.message);
      try { await msg.reply(`❌ Failed to set media group: ${err.message}`); } catch {}
    }
  });

  await client.initialize();
}

process.on("uncaughtException", (err) => console.error("Uncaught:", err));
process.on("unhandledRejection", (err) => console.error("Unhandled rejection:", err));

start().catch((err) => { console.error("Fatal:", err); process.exit(1); });
