#!/usr/bin/env python3
"""X (Twitter) autopost bot, one folder per account in accounts/<id>/ (config.json + posted.json).

One run = for every account that is due: at most one original post written by Claude,
sometimes followed by a reply from the same account with a link (X API, OAuth 1.0a).
State lives in accounts/<id>/posted.json so reruns never double-post and Claude never
repeats itself. A ramp-up (config "ramp") raises the daily number of posts step by step.

Env vars:
  ANTHROPIC_API_KEY                 required
  X_API_KEY, X_API_SECRET           the X app's consumer key and secret
  X_TOKEN_<ID>, X_TOKEN_SECRET_<ID> access token and secret of each account (ID upper-case)
  ACCOUNTS=a,b                      optional: only these account ids
  DRY_RUN=1                         generate and print, publish nothing, save nothing
  FORCE=1                           ignore the time window, gap and daily cap guards
"""
import datetime as dt
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

import oauth1

ROOT = Path(__file__).parent
ACCOUNTS_DIR = ROOT / "accounts"
CONFIG = {}            # set per account in main()
STATE_FILE = None      # set per account in main()
ACCOUNT_ID = ""

X_TWEETS = "https://api.x.com/2/tweets"
DRY_RUN = os.environ.get("DRY_RUN") == "1"
FORCE = os.environ.get("FORCE") == "1"
MAX_LEN = 280  # X limit (weighted length)
URL_LEN = 23   # X counts every link as 23 characters


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------- state
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"counter": 0, "posts": []}


def save_state(state):
    state["posts"] = state["posts"][-400:]
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def threads_len(text):
    """X weighted length: links count 23, emoji and wide characters count 2."""
    text = re.sub(r"https?://\S+", "x" * URL_LEN, text)
    return sum(1 if ord(ch) < 0x1100 or ch in "\ufe0f\u200d" else 2 for ch in text if ch not in "\ufe0f\u200d")


def emojis_in(text):
    """Symbols and pictographs in the text (variation selectors and joiners ignored)."""
    return [ch for ch in text if ord(ch) >= 0x2190 and ch not in "\ufe0f\u200d"]


def emoji_rule():
    e = CONFIG["emoji"]
    return (
        f"Emojis: use {e['min']} to {e['max']} per post, ONLY from this set: {' '.join(e['palette'])}. "
        f"{e['note']} Vary which ones you pick and where they sit (opening, between lines, "
        "closing); do not copy the emoji pattern of the recent posts."
    )


# ---------------------------------------------------------------- planning
def pick_rotating(options, recent, key):
    """Pick the option used least recently (never used first)."""
    last_seen = {}
    for i, post in enumerate(recent):
        last_seen[post.get(key)] = i
    return min(options, key=lambda o: last_seen.get(o["id"], -1))


def plan_post(state, allow_links=True):
    recent = state["posts"]
    counter = state["counter"] + 1
    products = [p for p in CONFIG["products"] if p.get("active", True)]
    communities = [c for c in CONFIG.get("communities", []) if c.get("active", True)]
    every = CONFIG.get("community_every", 0)

    community = None
    if communities and every and counter % every == 0:
        community = pick_rotating(communities, [p for p in recent if p.get("community")], "community")

    product = None
    link_every = CONFIG.get("link_every", 0)
    if allow_links and products and link_every and counter % link_every == 0:
        pool = products
        if community:
            pool = [p for p in products if set(p["pillars"]) & set(community["pillars"])] or products
        product = pick_rotating(pool, [p for p in recent if p.get("product")], "product")

    allowed = {p["id"] for p in CONFIG["pillars"]}
    if community:
        allowed &= set(community["pillars"])
    if product and allowed & set(product["pillars"]):
        allowed &= set(product["pillars"])
    pillars = [p for p in CONFIG["pillars"] if p["id"] in allowed] or CONFIG["pillars"]
    pillar = pick_rotating(pillars, recent, "pillar")
    fmt = pick_rotating(CONFIG["formats"], recent, "format")
    return counter, pillar, fmt, product, community


def ramp_day(state, now):
    """0 on the day of the first post, 1 the day after, and so on."""
    started = state.get("started")
    if not started:
        return 0
    return (now.date() - dt.date.fromisoformat(started)).days


def daily_cap(state, now):
    ramp = CONFIG.get("ramp") or {}
    steps = ramp.get("posts_per_day") or []
    full = CONFIG.get("max_posts_per_24h", 24)
    if not ramp.get("enabled") or not steps:
        return full
    day = ramp_day(state, now)
    return min(steps[day], full) if day < len(steps) else full


def links_allowed(state, now):
    ramp = CONFIG.get("ramp") or {}
    if not ramp.get("enabled"):
        return True
    return ramp_day(state, now) + 1 >= ramp.get("links_from_day", 1)


def should_run(state, now):
    """Return a reason to skip this run, or None to go ahead."""
    hours = CONFIG.get("active_hours_utc")
    start = 0
    window_minutes = 24 * 60
    if hours:
        start, end = hours["start"], hours["end"]
        inside = start <= now.hour < end if start < end else (now.hour >= start or now.hour < end)
        if not inside:
            return f"outside the active window ({start}:00-{end}:00 UTC)"
        window_minutes = ((end - start) % 24 or 24) * 60
    cap = daily_cap(state, now)
    posts = state["posts"]
    # "today" = the current window, which may run past midnight UTC
    elapsed = ((now.hour - start) % 24) * 60 + now.minute
    opened = now - dt.timedelta(minutes=elapsed, seconds=now.second)
    done = sum(1 for p in posts if dt.datetime.fromisoformat(p["date"]) >= opened)
    if done >= cap:
        return f"{done} posts today, cap is {cap} (day {ramp_day(state, now) + 1} of the ramp-up)"
    # spread today's posts evenly over the window: post number N is due at N/cap of it
    due = done * window_minutes / cap
    if elapsed + 5 < due:
        return f"next post is due {due - elapsed:.0f} minutes from now ({done}/{cap} done today)"
    if posts:
        last = dt.datetime.fromisoformat(posts[-1]["date"])
        minutes = (now - last).total_seconds() / 60
        # no catch-up bursts if the bot starts late in the day
        spacing = max(CONFIG.get("min_gap_minutes", 20), (window_minutes / cap) // 30 * 30 - 5)
        if minutes < spacing:
            return f"last post was {minutes:.0f} minutes ago (today's minimum spacing: {spacing:.0f})"
    return None


# ---------------------------------------------------------------- Claude
def build_prompt(pillar, fmt, product, recent_texts):
    v = CONFIG["voice"]
    rules = "\n".join(f"- {r}" for r in CONFIG["rules"])
    system = (
        f"You write posts for the X (Twitter) account of {v['name']}.\n"
        f"Who is writing: {v['persona']}\n"
        f"Tone: {v['tone']}\n"
        f"Language: {v['language']}.\n\n"
        f"Hard rules:\n{rules}\n- {emoji_rule()}\n"
        f"- The post must be at most {CONFIG['max_post_chars']} characters.\n"
        "- Output ONLY a JSON object, no markdown fences, no commentary."
    )
    user = [
        f"Write ONE original post for X (Twitter). It must work as a single post, not a thread.",
        f"Theme: {pillar['name']} - {pillar['brief']}",
        f"Format: {fmt['name']} - {fmt['brief']}",
    ]
    if product:
        user.append(
            "This post will be followed by a reply from the same account that links to a "
            f"product: \"{product['name']}\" ({product['brief']}). The post itself must stand "
            "alone as valuable content and must NOT mention the product or any shop. "
            "Also write the reply: one short, warm, low-pressure sentence (max 140 characters) "
            "that connect the post to the product. Do not include any URL, price or discount "
            "code in the reply; they are added automatically."
        )
        user.append('Return: {"post": "...", "reply": "..."}')
    else:
        user.append('Return: {"post": "..."}')
    if recent_texts:
        joined = "\n---\n".join(recent_texts)
        user.append(
            "These are the most recent posts. Do not repeat their ideas, openings or "
            f"phrasing:\n---\n{joined}\n---"
        )
    return system, "\n\n".join(user)


def call_claude(system, user):
    model = os.environ.get("ANTHROPIC_MODEL") or CONFIG["model"]
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 600,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=90,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Claude API error {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    return "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")


def parse_json(raw):
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(raw[start : end + 1])


def check_text(text, recent_texts, limit, min_emoji=0, max_emoji=None):
    """Return a reason string if the text is not acceptable, else None."""
    if not text or not text.strip():
        return "empty"
    found = emojis_in(text)
    palette = set("".join(CONFIG["emoji"]["palette"]))
    outside = [ch for ch in found if ch not in palette]
    if outside:
        return f"emoji outside the allowed set: {' '.join(sorted(set(outside)))}"
    max_emoji = CONFIG["emoji"]["max"] if max_emoji is None else max_emoji
    if not min_emoji <= len(found) <= max_emoji:
        return f"{len(found)} emojis (allowed: {min_emoji} to {max_emoji})"
    if threads_len(text) > limit:
        return f"too long ({threads_len(text)} > {limit})"
    if re.search(r"https?://|www\.", text):
        return "contains a link"
    if text.count("#") > 1:
        return "more than one hashtag"
    low = text.lower()
    for banned in CONFIG.get("banned_phrases", []):
        if banned.lower() in low:
            return f"banned phrase: {banned}"
    for old in recent_texts:
        if difflib.SequenceMatcher(None, low, old.lower()).ratio() > 0.6:
            return "too similar to a recent post"
    return None


def generate(pillar, fmt, product, state):
    recent_texts = [p["text"] for p in state["posts"][-CONFIG.get("memory_posts", 25):]]
    system, user = build_prompt(pillar, fmt, product, recent_texts)
    problem = None
    for attempt in range(1, 4):
        prompt = user if not problem else f"{user}\n\nYour previous attempt was rejected: {problem}. Fix it."
        try:
            out = parse_json(call_claude(system, prompt))
        except (ValueError, json.JSONDecodeError) as exc:
            problem = f"invalid JSON ({exc})"
            log(f"attempt {attempt}: {problem}")
            continue
        post = (out.get("post") or "").strip()
        reply = (out.get("reply") or "").strip() if product else ""
        problem = check_text(post, recent_texts, CONFIG["max_post_chars"], min_emoji=CONFIG["emoji"]["min"])
        if not problem and product:
            problem = check_text(reply, [], 150, max_emoji=1)
            if problem:
                problem = f"reply {problem}"
        if not problem:
            return post, reply
        log(f"attempt {attempt}: {problem}")
    raise RuntimeError(f"could not get a valid post after 3 attempts: {problem}")


def build_reply(reply_intro, product):
    parts = [reply_intro]
    coupon = CONFIG.get("coupon") or {}
    if coupon.get("code"):
        parts.append(coupon["text"].format(code=coupon["code"]))
    parts.append(product["url"])
    text = "\n\n".join(parts)
    if threads_len(text) > MAX_LEN:
        text = "\n\n".join([reply_intro, product["url"]])
    return text


# ---------------------------------------------------------------- X
def publish(text, reply_to=None, topic_tag=None, extra=None):
    key, secret = os.environ["X_API_KEY"], os.environ["X_API_SECRET"]
    tok = os.environ[f"X_TOKEN_{ACCOUNT_ID.upper()}"]
    tok_secret = os.environ[f"X_TOKEN_SECRET_{ACCOUNT_ID.upper()}"]
    body = {"text": text}
    if reply_to:
        body["reply"] = {"in_reply_to_tweet_id": reply_to}
    last = None
    for attempt in range(3):
        headers = {
            "Authorization": oauth1.auth_header("POST", X_TWEETS, key, secret, tok, tok_secret),
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(X_TWEETS, headers=headers, json=body, timeout=60)
        except requests.exceptions.RequestException as exc:
            last = RuntimeError(f"network error: {exc}")
            log(f"publish attempt {attempt + 1} failed: {exc}")
            time.sleep(10)
            continue
        if resp.status_code in (200, 201):
            return resp.json()["data"]["id"]
        last = RuntimeError(f"X API error {resp.status_code}: {resp.text[:400]}")
        if resp.status_code not in (429, 500, 502, 503, 504):
            break  # auth, permission, duplicate, credits: retrying will not help
        log(f"publish attempt {attempt + 1} failed: {last}")
        time.sleep(15)
    raise last


# ---------------------------------------------------------------- main
def run_account():
    state = load_state()
    now = dt.datetime.now(dt.timezone.utc)
    if not (FORCE or DRY_RUN):
        skip = should_run(state, now)
        if skip:
            log(f"Skipping: {skip}.")
            return 0

    counter, pillar, fmt, product, community = plan_post(state, links_allowed(state, now))
    log(
        f"Post #{counter} | {'community: ' + community['topic_tag'] if community else 'profile post'}"
        f" | theme: {pillar['id']} | format: {fmt['id']} | product: {product['id'] if product else '-'}"
    )

    post, reply_intro = generate(pillar, fmt, product, state)
    reply = build_reply(reply_intro, product) if product else ""
    log("\n--- POST ---\n" + post)
    if reply:
        log("\n--- FIRST REPLY ---\n" + reply)

    if DRY_RUN:
        log("\nDRY_RUN=1: nothing published, state not saved.")
        return 0

    thread_id = publish(post)
    log(f"\nPublished post {thread_id}")
    entry = {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "text": post,
        "pillar": pillar["id"],
        "format": fmt["id"],
        "product": product["id"] if product else None,
        "community": community["id"] if community else None,
        "thread_id": thread_id,
        "reply_id": None,
    }
    state["counter"] = counter
    state.setdefault("started", entry["date"][:10])
    state["posts"].append(entry)
    save_state(state)  # save right away: a failed reply must not cause a duplicate post

    if reply:
        try:
            entry["reply_id"] = publish(reply, reply_to=thread_id)
            log(f"Published reply {entry['reply_id']}")
            save_state(state)
        except RuntimeError as exc:
            log(f"Post is live but the reply failed: {exc}")
            return 1
    return 0


def main():
    global CONFIG, STATE_FILE, ACCOUNT_ID
    only = {a.strip() for a in os.environ.get("ACCOUNTS", "").split(",") if a.strip()}
    failures = 0
    for folder in sorted(p for p in ACCOUNTS_DIR.iterdir() if (p / "config.json").exists()):
        if only and folder.name not in only:
            continue
        ACCOUNT_ID = folder.name
        CONFIG = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        if not CONFIG.get("enabled", True):
            log(f"\n===== {ACCOUNT_ID}: disabled =====")
            continue
        STATE_FILE = folder / "posted.json"
        log(f"\n===== {ACCOUNT_ID} =====")
        try:
            failures += run_account() or 0
        except Exception as exc:  # one account failing must not stop the others
            log(f"ERROR on {ACCOUNT_ID}: {exc}")
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
