/**
 * Cloudflare Worker — Discord interactions endpoint for /read SYMBOL.
 *
 * Deploy:
 *   1. Install wrangler:  npm i -g wrangler
 *   2. wrangler login
 *   3. Set secret:  wrangler secret put DISCORD_PUBLIC_KEY
 *      (from Discord Developer Portal → General Information → Public Key)
 *   4. wrangler deploy
 *   5. Copy the *.workers.dev URL into Discord → General Information
 *      → "Interactions Endpoint URL". Discord will PING-verify; this
 *      worker handles that.
 *   6. Register the /read command (see register_command.js).
 *
 * Reads live state from:
 *   https://raw.githubusercontent.com/ShadfxQuant/quant-ia-trading-system/main/data/state.json
 * No bot token / no server / no cost. Worker free tier = 100k req/day.
 */

const STATE_URL =
  "https://raw.githubusercontent.com/ShadfxQuant/quant-ia-trading-system/main/data/state.json";

// --- ed25519 signature verification (Discord requires this) ---
async function verify(request, body, publicKey) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!signature || !timestamp) return false;
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", hex2buf(publicKey),
    { name: "Ed25519" }, false, ["verify"],
  );
  return crypto.subtle.verify(
    "Ed25519", key, hex2buf(signature),
    enc.encode(timestamp + body),
  );
}
function hex2buf(hex) {
  const b = new Uint8Array(hex.length / 2);
  for (let i = 0; i < b.length; i++)
    b[i] = parseInt(hex.substr(i * 2, 2), 16);
  return b.buffer;
}

// --- Discord response helpers ---
const json = (data) =>
  new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });

function biasEmoji(b) {
  return { bullish: "🟢", bearish: "🔴", neutral: "⚪️" }[b] || "⚪️";
}
function tiltEmoji(t) {
  return { supports: "✅", conflicts: "⚠️", neutral: "·", "n/a": "·" }[t] || "·";
}

async function fetchRead(symbol) {
  const r = await fetch(STATE_URL, { cf: { cacheTtl: 60 } });
  if (!r.ok) throw new Error(`state.json HTTP ${r.status}`);
  const state = await r.json();
  const snap = state?.symbols?.[symbol.toUpperCase()];
  if (!snap) {
    const known = Object.keys(state?.symbols || {}).join(", ");
    throw new Error(`unknown symbol \`${symbol}\` — known: ${known || "(none)"}`);
  }
  return { snap, generatedAt: state?.generated_at_utc };
}

function formatRead({ snap, generatedAt }) {
  const r = snap.read || {};
  if (r.error) return `**${snap.symbol}** — read unavailable: ${r.error}`;
  const lines = [
    `**${snap.symbol}**  ${biasEmoji(r.bias)} **${(r.bias || "?").toUpperCase()}**  ·  strength **${(r.strength || "?").toUpperCase()}**  (ADX ${r.adx ?? "—"})`,
    `Close $${snap.close?.toFixed?.(2)}  ·  EMA50 $${snap.ema?.toFixed?.(2)}  ·  SMA130 $${snap.sma?.toFixed?.(2)}`,
    `Regime eligible last 24h: **${r.regime_pct_24h ?? 0}%**  ·  Macro: ${tiltEmoji(r.macro_tilt)} ${(r.macro_tilt || "—").toUpperCase()}`,
    "",
    r.narrative || "",
  ];
  if (r.flip?.length) {
    lines.push("", "**What would flip this read:**");
    for (const f of r.flip) lines.push(`• ${f}`);
  }
  lines.push("", `_snapshot: ${generatedAt || "unknown"}_`);
  return lines.join("\n");
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok", { status: 200 });
    const body = await request.text();
    const valid = await verify(request, body, env.DISCORD_PUBLIC_KEY);
    if (!valid) return new Response("bad signature", { status: 401 });

    const interaction = JSON.parse(body);

    // Type 1 = PING (Discord verification)
    if (interaction.type === 1) return json({ type: 1 });

    // Type 2 = APPLICATION_COMMAND
    if (interaction.type === 2) {
      const cmd = interaction.data?.name;
      if (cmd !== "read") {
        return json({
          type: 4,
          data: { content: `unknown command: ${cmd}` },
        });
      }
      const symbol =
        interaction.data?.options?.find((o) => o.name === "symbol")?.value ||
        "PAXGUSDT";
      try {
        const result = await fetchRead(symbol);
        return json({ type: 4, data: { content: formatRead(result) } });
      } catch (e) {
        return json({
          type: 4,
          data: { content: `❌ ${e.message}` },
        });
      }
    }

    return json({ type: 4, data: { content: "unhandled interaction type" } });
  },
};
