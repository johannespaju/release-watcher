import json, os, pathlib, re, urllib.request

# Repos where we just want the single latest stable release.
# /releases/latest already excludes drafts and pre-releases.
SIMPLE_REPOS = [
    "magento/magento2",
    "woocommerce/woocommerce",
    "PrestaShop/PrestaShop",
]

# Repos where multiple major versions are maintained in parallel and must be
# tracked separately. Maps repo -> list of major versions to watch.
# OpenCart ships v3 and v4 patches from the same repo, sometimes out of order.
MAJOR_TRACKED_REPOS = {
    "opencart/opencart": [3, 4],
}

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


def parse_version(tag):
    """'3.0.5.0' -> (3, 0, 5, 0); tolerates an optional leading 'v'."""
    m = re.match(r"v?(\d+(?:\.\d+)*)", tag)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def latest_for_major(releases, major):
    """Highest stable release whose major version matches, or None.

    `releases` is the raw list from /releases (may include pre-releases and
    drafts, and is NOT ordered by version), so we filter and compare here.
    """
    best = None  # (version_tuple, release_dict)
    for rel in releases:
        if rel.get("prerelease") or rel.get("draft"):
            continue
        ver = parse_version(rel["tag_name"])
        if ver is None or ver[0] != major:
            continue
        if best is None or ver > best[0]:
            best = (ver, rel)
    return best[1] if best else None


def notify(text):
    if DISCORD_WEBHOOK:
        post(DISCORD_WEBHOOK, {"content": text})
    if SLACK_WEBHOOK:
        post(SLACK_WEBHOOK, {"text": text})


def post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        # Discord sits behind Cloudflare, which 403s the default
        # Python-urllib User-Agent. Any real-looking UA works.
        "User-Agent": "release-watcher/1.0",
    })
    urllib.request.urlopen(req)


def announce(key, label, tag, url, state):
    """Compare against saved state; notify + update state if changed.

    Notification failures are caught so one bad send doesn't abort the rest
    of the run (the loop now emits up to five messages).
    """
    if state.get(key) == tag:
        print(f"no change: {label} ({tag})")
        return False
    print(f"NEW: {label} -> {tag}")
    try:
        notify(f"🚀 **{label}** released **{tag}**\n{url}")
    except Exception as e:
        print(f"  notify failed for {label}: {e}")
        return False  # don't save state, so we retry next run
    state[key] = tag
    return True


def load_state():
    """Read state.json, tolerating a missing, empty, or corrupt file."""
    if not STATE_FILE.exists():
        return {}
    text = STATE_FILE.read_text().strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("warning: state.json is not valid JSON; treating as empty")
        return {}


def main():
    state = load_state()
    changed = False

    for repo in SIMPLE_REPOS:
        try:
            rel = gh_get(f"https://api.github.com/repos/{repo}/releases/latest")
        except Exception as e:
            print(f"skip {repo}: {e}")
            continue
        if announce(repo, repo, rel["tag_name"], rel["html_url"], state):
            changed = True

    for repo, majors in MAJOR_TRACKED_REPOS.items():
        try:
            releases = gh_get(
                f"https://api.github.com/repos/{repo}/releases?per_page=100"
            )
        except Exception as e:
            print(f"skip {repo}: {e}")
            continue
        for major in majors:
            rel = latest_for_major(releases, major)
            if rel is None:
                print(f"no stable release found for {repo} v{major}")
                continue
            key = f"{repo}#{major}"
            label = f"{repo} (v{major})"
            if announce(key, label, rel["tag_name"], rel["html_url"], state):
                changed = True

    if changed:
        STATE_FILE.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
