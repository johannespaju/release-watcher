# release-watcher

Automatically notifies a Discord (and/or Slack) channel when a new stable
release ships for the ecommerce platforms I build payment plugins for. Runs
entirely on GitHub Actions — no server, no cost.

## What it watches

| Platform | Source |
|----------|--------|
| Magento | [`magento/magento2`](https://github.com/magento/magento2) |
| OpenCart | [`opencart/opencart`](https://github.com/opencart/opencart) |
| WooCommerce | [`woocommerce/woocommerce`](https://github.com/woocommerce/woocommerce) |
| PrestaShop | [`PrestaShop/PrestaShop`](https://github.com/PrestaShop/PrestaShop) |

Each platform is checked via the GitHub API endpoint `/releases/latest`, which
**excludes drafts and pre-releases automatically** — so betas and release
candidates don't trigger notifications.

> **Shopify** is not included because it's hosted SaaS with no installable
> version. What matters for app developers is the quarterly **API version**
> (e.g. `2025-01`), published on the [Shopify developer changelog](https://shopify.dev/changelog)
> rather than GitHub. Track that feed separately.

## How it works

1. A scheduled GitHub Actions workflow runs `check_releases.py`.
2. For each repo, the script fetches the latest stable release tag.
3. It compares each tag against `state.json` (the last-seen versions).
4. Any changed tag triggers a webhook POST to Discord and/or Slack.
5. The updated `state.json` is committed back to the repo, so the next run
   only reports genuinely new releases.

On the **first run**, `state.json` doesn't exist yet, so every platform is
treated as new and you'll get one message per repo. That's expected — it's the
proof that the full chain works. After that, the channel stays quiet until a
real release lands.

## Setup

### 1. Create a Discord webhook

Channel settings → **Integrations** → **Webhooks** → **New Webhook** → **Copy
Webhook URL**.

> The webhook URL is a secret — anyone with it can post to your channel. Never
> commit it to the repo.

### 2. Add repository secrets

Repo **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Required | Notes |
|--------|----------|-------|
| `DISCORD_WEBHOOK` | Yes (for Discord) | Paste the raw URL, no quotes or trailing spaces |
| `SLACK_WEBHOOK` | Optional | Slack incoming webhook URL; omit to stay Slack-silent |
| `GITHUB_TOKEN` | No | Injected automatically by Actions |

The script skips whichever webhook isn't set, so Discord-only works fine.

### 3. Run it

**Actions** tab → **Check for new releases** → **Run workflow**. Watch the log;
you should see `NEW: ...` lines and messages appear in your channel.

## Schedule

Configured in `.github/workflows/check.yml`:

```yaml
on:
  schedule:
    - cron: "0 * * * *"   # hourly
  workflow_dispatch:        # manual trigger for testing
```

Change the cron to `"0 8 * * *"` for a daily 08:00 UTC check, etc.

## Things to know

- **Scheduled runs are best-effort.** GitHub can delay a scheduled Action by
  several minutes (occasionally more) when its infrastructure is busy. A run
  scheduled for 3:00 landing at 3:12 is normal, not a bug.
- **60-day auto-disable.** GitHub disables scheduled workflows after 60 days of
  no repo activity. Because this repo commits `state.json` whenever a release
  drops, active platforms keep it alive on their own. If everything goes quiet
  for two months, GitHub emails you and one click re-enables it.
- **Discord requires a User-Agent header.** Discord's API sits behind
  Cloudflare, which rejects Python's default `Python-urllib/x.y` User-Agent
  with `403 Forbidden`. The POST request sends a custom `User-Agent` header to
  avoid this. If you rewrite the request code, keep that header.

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| `403 Forbidden` on the Discord POST | Missing/invalid `User-Agent` header, or a bad webhook URL (check for truncation, trailing spaces, or wrapping quotes in the secret) |
| `404` on the Discord POST | Webhook was deleted — recreate it and update the secret |
| First run posts nothing | Check the secret name matches exactly (`DISCORD_WEBHOOK`, case-sensitive) |
| No runs appear at all | Enable workflows on the private repo (banner in the Actions tab) |
| GitHub API rate limits | Unauthenticated calls are capped at 60/hour per IP; Actions runs use `GITHUB_TOKEN` automatically, so this mainly affects local testing |

## Local development

Clone the repo and set the webhook as an environment variable:

```bash
export DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
python check_releases.py
```

`GITHUB_TOKEN` won't exist locally; the script falls back to unauthenticated
GitHub requests, which is fine for testing four repos.

## Files

```
.
├── check_releases.py            # the script
├── state.json                   # last-seen versions (auto-managed; don't edit)
├── .github/
│   └── workflows/
│       └── check.yml            # the scheduled workflow
└── README.md
```
