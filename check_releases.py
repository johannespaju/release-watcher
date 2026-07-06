import json, os, pathlib, re, urllib.request
import xml.etree.ElementTree as ET

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

# Shopify has no installable version; it ships dated quarterly API versions
# (e.g. 2026-07) announced on its developer changelog RSS feed. We watch that
# feed for (a) new API versions and (b) optionally payments-related entries.
WATCH_SHOPIFY = True
SHOPIFY_FEED = "https://shopify.dev/changelog/feed.xml"
SHOPIFY_PAYMENTS_FILTER = True  # also alert on Payments-tagged changelog entries

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


def _localname(tag):
    """Strip any XML namespace: '{ns}item' -> 'item'."""
    return tag.rsplit("}", 1)[-1]


def fetch_feed(url):
    """Fetch an RSS feed and return a list of items as dicts.

    Each item: {title, link, id, categories}. Namespace-tolerant so it works
    whether or not the feed declares one.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "release-watcher/1.0"})
    with urllib.request.urlopen(req) as r:
        root = ET.fromstring(r.read())

    items = []
    for el in root.iter():
        if _localname(el.tag) != "item":
            continue
        fields = {"title": "", "link": "", "id": "", "categories": []}
        for child in el:
            name = _localname(child.tag)
            text = (child.text or "").strip()
            if name == "title":
                fields["title"] = text
            elif name == "link":
                fields["link"] = text
            elif name == "guid":
                fields["id"] = text
            elif name == "category" and text:
                fields["categories"].append(text)
        if not fields["id"]:
            fields["id"] = fields["link"]  # fall back to link as the stable id
        items.append(fields)
    return items


def check_shopify(state):
    """Watch the Shopify developer changelog feed.

    Two independent signals, both robust to the feed's unreliable pubDate
    because we key off the version string and each entry's stable GUID:
      A) a new dated API version (e.g. 2026-10) appears  -> always announced
      B) a Payments-tagged entry appears (optional)      -> announced per entry

    First-run behaviour differs per signal on purpose: for versions we announce
    the current latest once (a useful "it works" confirmation), but for payments
    entries we seed silently so the channel isn't flooded with historical posts.
    """
    try:
        items = fetch_feed(SHOPIFY_FEED)
    except Exception as e:
        print(f"skip shopify: {e}")
        return False

    changed = False

    # --- Signal A: API versions (from exact 'YYYY-MM' category tags) --------
    versions = set()
    for it in items:
        for cat in it["categories"]:
            if re.fullmatch(r"20\d\d-\d\d", cat):
                versions.add(cat)

    if versions:
        if "shopify:versions" not in state:  # first run
            latest = max(versions)
            print(f"NEW: shopify api version -> {latest} (seeding)")
            try:
                notify(f"🛍️ **Shopify** current API version **{latest}**\n{SHOPIFY_FEED}")
                state["shopify:versions"] = sorted(versions)
                changed = True
            except Exception as e:
                print(f"  notify failed for shopify version: {e}")
        else:
            seen = set(state["shopify:versions"])
            for v in sorted(versions - seen):
                print(f"NEW: shopify api version -> {v}")
                try:
                    notify(f"🛍️ **Shopify** new API version **{v}** — time to test\n{SHOPIFY_FEED}")
                    state["shopify:versions"] = sorted(seen | {v})
                    seen.add(v)
                    changed = True
                except Exception as e:
                    print(f"  notify failed for shopify version {v}: {e}")

    # --- Signal B: Payments-related entries (optional) ---------------------
    if SHOPIFY_PAYMENTS_FILTER:
        def is_payments(it):
            haystack = (it["title"] + " " + " ".join(it["categories"])).lower()
            return "payment" in haystack

        pay = [it for it in items if is_payments(it)]
        if "shopify:seen_payments" not in state:  # first run: seed silently
            state["shopify:seen_payments"] = [it["id"] for it in pay]
            changed = True
        else:
            # Keep the stored value an ordered list (append-only) so a new
            # entry produces a one-line diff instead of reshuffling the whole
            # list; `seen` is just a membership index over that same list.
            seen = set(state["shopify:seen_payments"])
            for it in pay:
                if it["id"] in seen:
                    continue
                print(f"NEW: shopify payments entry -> {it['title']}")
                try:
                    notify(f"💳 **Shopify** payments changelog: {it['title']}\n{it['id']}")
                    seen.add(it["id"])
                    state["shopify:seen_payments"].append(it["id"])
                    changed = True
                except Exception as e:
                    print(f"  notify failed for shopify entry: {e}")

    return changed


def notify(text):
    """Send to every configured channel, isolating each one.

    If Discord succeeds but Slack fails (or vice versa), we must NOT let the
    failure propagate: callers treat any raised exception as "nothing sent" and
    skip saving state, which would re-notify the already-delivered channel on
    the next run. So each channel is guarded independently and we re-raise only
    if every configured channel failed.
    """
    channels = []
    if DISCORD_WEBHOOK:
        channels.append(("discord", DISCORD_WEBHOOK, {"content": text}))
    if SLACK_WEBHOOK:
        channels.append(("slack", SLACK_WEBHOOK, {"text": text}))

    if not channels:
        return

    errors = []
    for name, url, payload in channels:
        try:
            post(url, payload)
        except Exception as e:
            print(f"  notify failed for {name}: {e}")
            errors.append(e)

    if len(errors) == len(channels):
        raise errors[0]  # every channel failed -> let caller skip saving state


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

    if WATCH_SHOPIFY and check_shopify(state):
        changed = True

    if changed:
        STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
