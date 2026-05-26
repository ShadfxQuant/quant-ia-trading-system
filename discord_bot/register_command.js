/**
 * One-shot registration of the /read command with Discord.
 *
 * Run locally (Node 18+):
 *   DISCORD_APP_ID=...  DISCORD_BOT_TOKEN=...  node register_command.js
 *
 * App ID:    Discord Developer Portal → General Information → APPLICATION ID
 * Bot Token: Discord Developer Portal → Bot → Reset Token (copy ONCE)
 *
 * Registering at the application level (global) takes up to 1h to propagate.
 * For instant testing, change the URL to a guild-scoped one with your server ID:
 *   https://discord.com/api/v10/applications/${APP_ID}/guilds/${GUILD_ID}/commands
 */
const APP_ID = process.env.DISCORD_APP_ID;
const TOKEN = process.env.DISCORD_BOT_TOKEN;
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

const res = await fetch(
  `https://discord.com/api/v10/applications/${APP_ID}/commands`,
  {
    method: "POST",
    headers: {
      "Authorization": `Bot ${TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(command),
  },
);
console.log(res.status, await res.text());
