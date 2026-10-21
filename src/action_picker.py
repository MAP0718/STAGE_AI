
from datetime import timedelta
from timeutil import parse
from config import MAX_ROW_TITLES

MONEY_GOALS = {"G04", "G05", "G06"}


def title_usable(t, dialect, at, withdrawn, quarantined):
    """The same test is applied again, separately, by verifier.py."""
    if t is None or t["title_id"] in quarantined or t["dialect"] != dialect:
        return False
    if t["available_from"] > at or (t["available_until"] and at >= t["available_until"]):
        return False
    gone = withdrawn.get(t["title_id"])
    return not (gone and at >= gone)


def _history(events, titles):
    """What the viewer already watched: slugs, genre counts, and shows still in progress."""
    watched, genres = set(), {}
    for e in events:
        if e["event"] == "play_started":
            t = titles.get(e["properties"].get("title_id"))
            if t:
                watched.add(t["content_slug"])
                for g in t["genres"]:
                    genres[g] = genres.get(g, 0) + 1
    return watched, genres


def _in_progress(events, titles, episodes):
    done = {}
    for e in events:
        if e["event"] == "episode_completed":
            t = titles.get(e["properties"].get("title_id"))
            if t:
                done.setdefault(t["content_slug"], set()).add(e["properties"].get("episode_id"))
    slugs = set()
    for e in events:
        if e["event"] == "play_started":
            t = titles.get(e["properties"].get("title_id"))
            if t and t["content_type"] in ("show", "micro_drama"):
                if len(done.get(t["content_slug"], ())) < len(episodes.get(t["content_slug"], [])):
                    slugs.add(t["content_slug"])
    return slugs


def build_home_page(pack, events, dialect, language, goal_id, now, withdrawn):
    """Return a home page dict, or None when there is not enough valid content."""
    titles, episodes = pack["titles"], pack["episodes"]
    watched, genres = _history(events, titles)
    progress = _in_progress(events, titles, episodes)

    # usable candidates, one per content_slug (prefer the viewer's language), most popular first
    best = {}
    for t in pack["by_dialect"].get(dialect, []):
        if not title_usable(t, dialect, now, withdrawn, pack["quarantined"]):
            continue
        cur = best.get(t["content_slug"])
        if cur is None or (t["language"] == language and cur["language"] != language):
            best[t["content_slug"]] = t
    cands = list(best.values())            # keeps popularity order (dicts keep insertion order)
    if goal_id in MONEY_GOALS:             # premium shows what a paid plan unlocks
        cands.sort(key=lambda t: t["is_premium"] != "true")   # stable: premium first, popularity kept

    used, rows = set(), []

    def add_row(row_type, pool):
        pick = [t for t in pool if t["content_slug"] not in used][:MAX_ROW_TITLES]
        if len(pick) >= 3:                  # a row with fewer than 3 titles is not allowed
            used.update(t["content_slug"] for t in pick)
            rows.append({"row_type": row_type, "title_ids": [t["title_id"] for t in pick]})

    add_row("next_episode", [t for t in cands if t["content_slug"] in progress])
    if genres:
        top = max(genres, key=genres.get)
        add_row("because_you_watched",
                [t for t in cands if top in t["genres"] and t["content_slug"] not in watched])
    add_row("popular_in_dialect", [t for t in cands if t["content_slug"] not in watched])
    since = (parse(now) - timedelta(days=30)).strftime("%Y-%m-%d")
    add_row("new_releases", [t for t in cands if since <= t["release_date"] <= now[:10]
                             and t["content_slug"] not in watched])
    # more shelves: one 'genre' row per genre (the viewer's own genres first), up to 6 rows in total
    order = sorted(genres, key=genres.get, reverse=True)
    freq = {}
    for t in cands:
        for g in t["genres"]:
            freq[g] = freq.get(g, 0) + 1
    order += [g for g in sorted(freq, key=freq.get, reverse=True) if g not in order]
    for g in order:
        if len(rows) >= 6:
            break
        add_row("genre", [t for t in cands if g in t["genres"] and t["content_slug"] not in watched])
    if not rows:
        return None
    hero = next((t for t in cands if t["content_slug"] not in used and t["content_slug"] not in watched), None)
    return {"dialect": dialect, "hero_title_id": hero["title_id"] if hero else None, "rows": rows}


def page_title_ids(page):
    ids = [t for r in page["rows"] for t in r["title_ids"]]
    if page.get("hero_title_id"):
        ids.append(page["hero_title_id"])
    return ids


def strip_title(page, title_id):
    """A title was withdrawn: remove it and drop any row that falls below 3 titles."""
    rows = []
    for r in page["rows"]:
        ids = [t for t in r["title_ids"] if t != title_id]
        if len(ids) >= 3:
            rows.append({"row_type": r["row_type"], "title_ids": ids})
    hero = None if page.get("hero_title_id") == title_id else page.get("hero_title_id")
    return {"dialect": page["dialect"], "hero_title_id": hero, "rows": rows}


def push_text(title_name):
    """Short, honest copy: no prices, no pressure words."""
    return {"title": title_name[:40], "body": ("Aapke liye: %s. Abhi dekhiye." % title_name)[:120]}
