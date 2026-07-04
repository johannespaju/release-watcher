import json, os, pathlib, urllib.request

REPOS = [
    "magento/magento2",
    "opencart/opencart",
    "woocommerce/woocommerce",
    "PrestaShop/PrestaShop",
]

STATE_FILE = pathlib.Path("state.json")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK")
GH_TOKEN = os.environ.get("GH_TOKEN")  # provided automatically by Actions


def gh_get(url):
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    if GH_TOKEN:
        req.add_header("Authorization", f"Bearer {GH_TOKEN}")
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def notify(text):
    if DISCORD_WEBHOOK:
        post(DISCORD_WEBHOOK, {"content": text})
    if SLACK_WEBHOOK:
        post(SLACK_WEBHOOK, {"text": text})


def post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)


def main():
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    changed = False

    for repo in REPOS:
        try:
            rel = gh_get(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception as e:
            print(f"skip {repo}: {e}")
            continue

        tag = rel["tag_name"]
        if state.get(repo) != tag:
            print(f"NEW: {repo} -> {tag}")
            notify(f"🚀 **{repo}** released **{tag}**\n{rel['html_url']}")
            state[repo] = tag
            changed = True
        else:
            print(f"no change: {repo} ({tag})")

    if changed:
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()