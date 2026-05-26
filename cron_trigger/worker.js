/**
 * Cloudflare cron-triggered Worker that bypasses GitHub Actions' silent
 * throttling of scheduled workflows. Cloudflare's cron triggers are
 * reliable on the free tier (unlike GitHub Actions' `schedule` events,
 * which on public repos can be skipped without warning).
 *
 * On each cron tick we POST to GitHub's workflow_dispatch endpoint,
 * which triggers the signal-worker workflow as if you'd clicked "Run
 * workflow" in the Actions UI. Manual dispatches are NOT throttled the
 * same way scheduled triggers are.
 *
 * Setup:
 *   1. Create a GitHub fine-grained PAT with `actions: write` scope
 *      restricted to this repo (https://github.com/settings/tokens?type=beta)
 *   2. wrangler secret put GITHUB_TOKEN
 *   3. wrangler deploy
 */

const GH_OWNER = "ShadfxQuant";
const GH_REPO = "quant-ia-trading-system";
const WORKFLOW_FILE = "worker.yml";

export default {
  // The cron is configured in wrangler.toml. This handler runs each tick.
  async scheduled(event, env, ctx) {
    const url = `https://api.github.com/repos/${GH_OWNER}/${GH_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
    const r = await fetch(url, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "quant-ia-cron-trigger",
      },
      body: JSON.stringify({ ref: "main" }),
    });
    console.log(`[${new Date().toISOString()}] dispatch → ${r.status}`);
    if (!r.ok) console.log(await r.text());
  },

  // Allow manual GET to confirm the Worker is alive
  async fetch(request, env) {
    return new Response(
      "cron-trigger alive. Scheduled tick is set in wrangler.toml.\n" +
      "Use `wrangler tail` to watch dispatches.",
      { headers: { "content-type": "text/plain" } },
    );
  },
};
