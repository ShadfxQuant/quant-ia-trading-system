# /read slash command — setup checklist

Three accounts needed: GitHub (already have it), **Discord Developer Portal** (free), **Cloudflare** (free). ~20 min total. You only do this once.

## 1. Discord Developer Portal — create the app

1. Go to https://discord.com/developers/applications → **New Application** → name it "Quant Read" → Create.
2. **General Information** tab: copy **APPLICATION ID** and **PUBLIC KEY** somewhere safe.
3. **Bot** tab → **Reset Token** → copy the token (you only see it once).
4. **OAuth2 → URL Generator**: tick scopes `bot` + `applications.commands`. Copy the generated URL, paste into a browser, invite the bot to your server.

## 2. Cloudflare — deploy the Worker

```bash
cd discord_bot
npm i -g wrangler
wrangler login                            # opens browser
wrangler secret put DISCORD_PUBLIC_KEY    # paste the PUBLIC KEY from step 1.2
wrangler deploy
```

Wrangler prints a URL like `https://quant-ia-discord-read.<your-subdomain>.workers.dev`. Copy it.

## 3. Tell Discord where the Worker lives

Back in Discord Developer Portal → **General Information** → **Interactions Endpoint URL** → paste the workers.dev URL → **Save**. Discord sends a PING; the Worker handles it. If you see "All your endpoints are valid" you're good.

## 4. Register the /read command

```bash
DISCORD_APP_ID=<your app id> \
DISCORD_BOT_TOKEN=<your bot token> \
node register_command.js
```

Should print `200` and a JSON dump of the command. Global registration takes up to ~1h to propagate. For instant testing, edit `register_command.js` to use the guild-scoped endpoint (instructions in the file).

## 5. Test in Discord

In any channel where the bot was invited:
```
/read symbol: PAXGUSDT
```
Should reply with the Read card.

## How it works

- Discord POSTs to your Cloudflare Worker.
- Worker verifies the ed25519 signature, fetches `state.json` from your public GitHub repo (cached 60s at the edge), formats the Read card, replies.
- No server, no bot token in the Worker, no cost. Free tier = 100k requests/day.
- Updates propagate as fast as the worker tick — every 12 min during the active window.
