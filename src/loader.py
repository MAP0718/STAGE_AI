
import csv
import json
import os
import re
from collections import Counter, defaultdict

# words that look like someone talking to an AI agent (data is NOT instruction)
INJECTION_PATTERN = re.compile(
    r"ignore (your|all|previous)|system note|assistant instruction|\[assistant|"
    r"mark every user|send a whatsapp|message every|pre-approved|for ai agents",
    re.IGNORECASE,
)


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            yield row


def load_events(path):
    """Return (events_by_user, stats). Deduped and sorted by received_at."""
    seen = set()
    by_user = defaultdict(list)
    total = dups = 0
    for e in read_jsonl(path):
        total += 1
        if e["event_id"] in seen:                   # problem 1
            dups += 1
            continue
        seen.add(e["event_id"])
        by_user[e["user_id"]].append(e)
    for events in by_user.values():                 # problem 3
        events.sort(key=lambda e: (e["received_at"], e["event_id"]))
    return dict(by_user), {"event_lines": total, "duplicate_events_dropped": dups}


def load_profiles(path):
    """Return ({user_id: row}, stats). One row per user: the latest updated_at wins."""
    best = {}
    two_rows = 0
    for row in read_csv(path):
        uid = row["user_id"]
        if uid in best:                             # problem 2
            two_rows += 1
            if row["updated_at"] <= best[uid]["updated_at"]:
                continue
        best[uid] = row
    return best, {"profile_rows_duplicated": two_rows}


def load_catalogue(path):
    """Return (titles_by_id, quarantined_title_ids)."""
    titles, quarantined = {}, set()
    for row in read_csv(path):
        if INJECTION_PATTERN.search(row["description"]):    # problem 5
            quarantined.add(row["title_id"])
            row["description"] = ""
        row["genres"] = row["genres"].split("|") if row["genres"] else []
        titles[row["title_id"]] = row
    return titles, quarantined


def load_episodes(path):
    """Return {content_slug: [episode_id, ...]}."""
    by_slug = defaultdict(list)
    for row in read_csv(path):
        by_slug[row["content_slug"]].append(row["episode_id"])
    return dict(by_slug)


def load_pack(pack_dir):
    p = lambda *parts: os.path.join(pack_dir, *parts)   # noqa: E731
    events, ev_stats = load_events(p("data", "events.jsonl"))
    profiles, pr_stats = load_profiles(p("data", "profiles_snapshot.csv"))
    titles, quarantined = load_catalogue(p("data", "catalogue.csv"))
    episodes = load_episodes(p("data", "episodes.csv"))

    # how often each title was played (used for "popular" rows)
    popularity = Counter()
    for evs in events.values():
        for e in evs:
            if e["event"] == "play_started":
                popularity[e["properties"].get("title_id")] += 1

    # titles of each dialect, most popular first (ties broken by title_id so runs are repeatable)
    by_dialect = defaultdict(list)
    for t in titles.values():
        by_dialect[t["dialect"]].append(t)
    for d in by_dialect:
        by_dialect[d].sort(key=lambda t: (-popularity[t["title_id"]], t["title_id"]))

    with open(p("config", "goals.json"), encoding="utf-8") as f:
        goals_file = json.load(f)
    with open(p("config", "interventions.json"), encoding="utf-8") as f:
        interventions = json.load(f)
    arms = {r["user_id"]: r["arm"] for r in read_csv(p("replay", "arms.csv"))}

    return {
        "events": events, "profiles": profiles, "titles": titles, "quarantined": quarantined,
        "episodes": episodes, "popularity": popularity, "by_dialect": dict(by_dialect),
        "goals": goals_file["goals"], "interventions": interventions, "arms": arms,
        "stats": {**ev_stats, **pr_stats, "quarantined_titles": sorted(quarantined),
                  "titles_total": len(titles),
                  "slugs_with_several_title_ids": sum(
                      1 for n in Counter(t["content_slug"] for t in titles.values()).values() if n > 1)},
    }


def load_replay(pack_dir):
    """Replay-day files."""
    p = lambda name: os.path.join(pack_dir, "replay", name)   # noqa: E731
    return {
        "triggers": list(read_jsonl(p("triggers.jsonl"))),
        "live_events": list(read_jsonl(p("live_events.jsonl"))),
        "control_events": list(read_jsonl(p("control_events.jsonl"))),
        "model_faults": {f["trigger_event_id"]: f for f in read_jsonl(p("model_faults.jsonl"))},
    }


if __name__ == "__main__":
    import sys
    pack = load_pack(sys.argv[1] if len(sys.argv) > 1 else "pack")
    print(pack["stats"])
    print("users with events:", len(pack["events"]), "| users with profile:", len(pack["profiles"]))
