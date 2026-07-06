# release-watcher

Automatically notifies a Discord (and/or Slack) channel when a new stable
release ships for the ecommerce platforms I build payment plugins for. Runs
entirely on GitHub Actions — no server, no cost.

## What it watches

| Platform | Source | Notes |
|----------|--------|-------|
| Magento | [`magento/magento2`](https://github.com/magento/magento2) | latest stable |
| OpenCart | [`opencart/opencart`](https://github.com/opencart/opencart) | **v3 and v4 tracked separately** |
| WooCommerce | [`woocommerce/woocommerce`](https://github.com/woocommerce/woocommerce) | latest stable |
| PrestaShop | [`PrestaShop/PrestaShop`](https://github.com/PrestaShop/PrestaShop) | latest stable |

Most platforms are checked via the GitHub API endpoint `/releases/latest`, which
**excludes drafts and pre-releases automatically** — so betas and release
candidates don't trigger notifications.

**OpenCart is a special case.** It maintains the v3 and v4 lines in parallel from
one repo and sometimes ships a v3 patch *after* a newer v4 release, so a single
"latest" lookup can't represent both. Instead the script pulls the full
`/releases` list, filters out pre-releases itself (the list endpoint includes
them), and reports the highest stable version within **each** configured major
line independently. To track different or additional majors, edit
`MAJOR_TRACKED_REPOS` in `check_releases.py`.

### How Shopify tracking works

Shopify has no version tag to compare, so it's handled by polling
`https://shopify.dev/changelog/feed.xml` for two independent signals:

- **New API version** — the script reads the exact `YYYY-MM` category tags on
  changelog entries and alerts when a version it hasn't seen appears (e.g.
  `2026-10`). This is the direct equivalent of "a new version came out, go
  test." On the very first run it announces the current latest version once as
  a confirmation, then stays quiet until a genuinely new one ships.
- **Payments-related entries** (optional, on by default via
  `SHOPIFY_PAYMENTS_FILTER`) — entries whose title or tags mention "payment",
  such as Payments Apps API changes that can break a payment plugin. To avoid
  flooding the channel, existing payments entries are seeded silently on the
  first run; only entries that appear afterward are announced.

Both signals key off the version string and each entry's stable GUID rather than
the feed's `pubDate`, because Shopify's changelog is known to backdate and
re-date entries — so a date-based "new since last check" would be unreliable.

Turn Shopify off entirely by setting `WATCH_SHOPIFY = False`.

> **Shopify** works differently — it's hosted SaaS with no installable version
> or repo to diff. Instead it ships dated quarterly **API versions** (e.g.
> `2026-07`) announced on its [developer changelog](https://shopify.dev/changelog)
> RSS feed. The script watches that feed; see "How Shopify tracking works" below.

## How it works

1. A scheduled GitHub Actions workflow runs `check_releases.py`.
2. For each repo, the script fetches the latest stable release tag.
3. It compares each tag against `state.json` (the last-seen versions). Each
   tracked line has its own key — OpenCart uses `opencart/opencart#3` and
   `opencart/opencart#4` so the two majors never overwrite each other. Shopify
   uses `shopify:versions` (list of seen API versions) and
   `shopify:seen_payments` (list of seen payments-entry IDs).
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
    - cron: "0 8 * * *"   # daily at 08:00 UTC
  workflow_dispatch:        # manual trigger for testing
```

Change the cron to `"0 * * * *"` for an hourly check, etc.

## Things to know

- **Scheduled runs are best-effort.** GitHub can delay a scheduled Action by
  several minutes (occasionally more) when its infrastructure is busy. A run
  scheduled for 3:00 landing at 3:12 is normal, not a bug.
- **60-day auto-disable.** GitHub disables scheduled workflows after 60 days of
  no repo activity. Because this repo commits `state.json` whenever a release
  drops, active platforms keep it alive on their own. If everything goes quiet
  for two months, GitHub emails you and one click re-enables it.
- **A failed notification won't abort the run.** Each send is wrapped so that
  one bad webhook or network blip doesn't stop the remaining platforms from
  being reported. A line that fails to send isn't saved to `state.json`, so it's
  retried on the next run.
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
| `JSONDecodeError: Expecting value` on startup | `state.json` is empty or corrupt — delete it (a missing file reseeds cleanly), or update to a version with the tolerant `load_state()` which handles this automatically |
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
