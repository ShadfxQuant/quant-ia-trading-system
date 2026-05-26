# Cron trigger Worker — setup (~10 min)

Bypasses GitHub Actions' silent throttling of scheduled workflows by using Cloudflare cron (reliable on free tier) to fire `workflow_dispatch` instead.

## 1. Create a GitHub fine-grained PAT (~3 min)

1. Open https://github.com/settings/personal-access-tokens
2. Click **Generate new token** → **Fine-grained personal access token**
3. **Token name**: `cf-cron-trigger`
4. **Expiration**: 1 year (or "No expiration" if available)
5. **Repository access**: **Only select repositories** → pick `ShadfxQuant/quant-ia-trading-system`
6. **Permissions** → **Repository permissions** → scroll to **Actions** → set to **Read and write**
7. Click **Generate token** → copy the token (starts with `github_pat_`)

## 2. Deploy the Worker (~3 min)

```bash
cd ~/Desktop/quant_ia_trading_system/cron_trigger
wrangler secret put GITHUB_TOKEN     # paste the token from step 1
wrangler deploy
```

Wrangler prints the worker URL (e.g. `https://quant-ia-cron-trigger.<your-subdomain>.workers.dev`). Hit it in your browser — should say "cron-trigger alive."

## 3. Verify the cron is scheduled

In Cloudflare dashboard → **Workers & Pages** → **quant-ia-cron-trigger** → **Triggers** tab → you should see `*/12 7-20 * * *` listed as a Cron Trigger.

## 4. Wait for the next tick + confirm GitHub Actions fires

Next fire is at the next `:00, :12, :24, :36, :48` minute mark inside 07:00–20:59 UTC.

In a terminal:

```bash
wrangler tail
```

When the cron fires you'll see:

```
[2026-05-26T13:12:00.123Z] dispatch → 204
```

`204` = GitHub accepted the workflow dispatch. ~30 sec later check:

```bash
gh run list --workflow=worker.yml --limit 3
```

You should see a new `workflow_dispatch` run. Within ~90 sec it'll complete and the bot will commit a fresh `state.json` with the populated `read` field.

## Disable the GitHub-side cron (optional)

The GitHub-side `*/12 7-20 * * *` schedule in `.github/workflows/worker.yml` is now redundant — it'll fire occasionally when GitHub feels like it, doing no harm but wasting a few minutes of free-tier budget. You can either leave it as a fallback or remove the `schedule:` block entirely. I'd leave it as belt-and-suspenders.
