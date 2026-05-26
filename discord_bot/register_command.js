/**
 * One-shot registration of the /read command with Discord.
 *
 * Two modes:
 *   - Guild-scoped (instant, for testing): set DISCORD_GUILD_ID env var.
 *   - Global (up to ~1h propagation): omit DISCORD_GUILD_ID.
 *
 * Run locally (Node 18+):
 *   DISCORD_APP_ID=... DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=... node register_command.js
 *
 * App ID:    Discord Developer Portal → General Information → APPLICATION ID
 * Bot Token: Discord Developer Portal → Bot → Reset Token
 * Guild ID:  right-click server icon → Copy Server ID (Developer Mode on)
 */
const APP_ID = process.env.DISCORD_APP_ID;
const TOKEN = process.env.DISCORD_BOT_TOKEN;
const GUILD_ID = process.env.DISCORD_GUILD_ID;
if (!APP_ID || !TOKEN) {
  console.error("Set DISCORD_APP_ID and DISCORD_BOT_TOKEN env vars.");
  process.exit(1);
}

const command = {
  name: "read",
  description: "Get the system's current Read on a symbol",
  options: [
    {
      name: "symbol",
      description: "Symbol (e.g. PAXGUSDT, SPY, GLD)",
      type: 3, // STRING
      required: true,
    },
  ],
};

const url = GUILD_ID
  ? `https://discord.com/api/v10/applications/${APP_ID}/guilds/${GUILD_ID}/commands`
  : `https://discord.com/api/v10/applications/${APP_ID}/commands`;

const res = await fetch(url, {
  method: "POST",
  headers: {
    "Authorization": `Bot ${TOKEN}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify(command),
});
console.log(res.status, await res.text());
console.log(GUILD_ID ? "→ guild-scoped (instant)" : "→ global (up to ~1h)");
