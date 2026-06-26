require("dotenv").config();
const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");
const { MongoClient } = require("mongodb");
const cron = require("node-cron");

const MONGO_URI = process.env.MONGO_URI;
const GROUP_ID = process.env.GROUP_ID;

if (!MONGO_URI || !GROUP_ID) {
  console.error("❌ Missing required env vars: MONGO_URI, GROUP_ID. Copy .env.example to .env and fill in values.");
  process.exit(1);
}

// Add or remove group IDs here
const GROUP_IDS = [GROUP_ID];

// Trigger words that fetch and display current stats
const TRIGGER_WORDS = ["!stats"];

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

    // Midnight IST = 18:30 UTC
    cron.schedule("30 18 * * *", async () => {
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
    if (!GROUP_IDS.includes(msg.from)) return;

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
    } else if (TRIGGER_WORDS.some((word) => words.includes(word))) {
      try {
        const stats = await fetchStats();
        await msg.reply(buildMessage(stats, "Current Stats"));
        console.log(`✅ On-demand stats sent (requested by ${msg.author})`);
      } catch (err) {
        console.error("❌ On-demand stats error:", err.message);
      }
    }
  });

  await client.initialize();
}

process.on("uncaughtException", (err) => console.error("Uncaught:", err));
process.on("unhandledRejection", (err) => console.error("Unhandled rejection:", err));

start().catch((err) => { console.error("Fatal:", err); process.exit(1); });
