/**
 * Cloudflare Worker — Discord interactions endpoint for /read SYMBOL.
 *
 * Uses Discord's deferred-response pattern (type 5) to avoid the 3-second
 * timeout: we ACK instantly, then PATCH the message via the interaction
 * webhook with the actual Read content once we've fetched state.json.
 *
 * Deploy:
 *   wrangler secret put DISCORD_PUBLIC_KEY    # from Developer Portal → Public Key
 *   wrangler deploy
 */

const STATE_URL =
  "https://raw.githubusercontent.com/ShadfxQuant/quant-ia-trading-system/main/data/state.json";

// --- ed25519 signature verification ---
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
  for (let i = 0; i < b.length; i++) b[i] = parseInt(hex.substr(i * 2, 2), 16);
  return b.buffer;
}

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
function fmtNum(n, d = 2) {
  return typeof n === "number" && !isNaN(n) ? n.toFixed(d) : "—";
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
  if (!r.bias) {
    return `**${snap.symbol}** — Read not yet populated by the worker. ` +
           `Next worker tick will fill it in (within ~12 min during 07–21 UTC).\n` +
           `_snapshot: ${generatedAt || "unknown"}_`;
  }
  const lines = [
    `**${snap.symbol}**  ${biasEmoji(r.bias)} **${(r.bias || "?").toUpperCase()}**  ·  strength **${(r.strength || "?").toUpperCase()}**  (ADX ${r.adx ?? "—"})`,
    `Close $${fmtNum(snap.close)}  ·  EMA50 $${fmtNum(snap.ema)}  ·  SMA130 $${fmtNum(snap.sma)}`,
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

// Edit the original deferred response via interaction webhook
async function editFollowup(appId, interactionToken, content) {
  const url = `https://discord.com/api/v10/webhooks/${appId}/${interactionToken}/messages/@original`;
  await fetch(url, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return new Response("ok", { status: 200 });
    const body = await request.text();
    const valid = await verify(request, body, env.DISCORD_PUBLIC_KEY);
    if (!valid) return new Response("bad signature", { status: 401 });

    const interaction = JSON.parse(body);

    if (interaction.type === 1) return json({ type: 1 }); // PING

    if (interaction.type === 2) {
      const cmd = interaction.data?.name;
      if (cmd !== "read") {
        return json({ type: 4, data: { content: `unknown command: ${cmd}` } });
      }
      const symbol =
        interaction.data?.options?.find((o) => o.name === "symbol")?.value ||
        "PAXGUSDT";

      // Defer (type 5) — we have 15 minutes to PATCH a real reply.
      // Use ctx.waitUntil so the Worker keeps running after we ACK.
      ctx.waitUntil((async () => {
        try {
          const result = await fetchRead(symbol);
          await editFollowup(
            interaction.application_id,
            interaction.token,
            formatRead(result),
          );
        } catch (e) {
          await editFollowup(
            interaction.application_id,
            interaction.token,
            `❌ ${e.message}`,
          );
        }
      })());

      return json({ type: 5 }); // DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }

    return json({ type: 4, data: { content: "unhandled interaction type" } });
  },
};
