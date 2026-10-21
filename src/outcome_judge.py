
import json
import os
from collections import defaultdict
from datetime import timedelta

from loader import read_jsonl
from timeutil import ist_day, parse


def _signal(goal, window, titles):
    """Return the first event in the window that satisfies the goal's success rule, or None."""
    gid, want = goal["id"], goal["success"]["event"]
    start = parse(goal["started_at"])
    if want is None:
        return None
    hits = [e for e in window if e["event"] == want]
    if gid in ("G04", "G05"):
        hits = [e for e in hits if not e["properties"].get("is_trial")]
    elif gid == "G03":
        hits = [e for e in hits if ist_day(parse(e["received_at"])) > ist_day(start)]
    elif gid == "G07":
        slug = goal["context"].get("series_slug")
        hits = [e for e in hits if titles.get(e["properties"].get("title_id"), {}).get("content_slug") == slug]
    elif gid == "G08":
        since = (start - timedelta(days=30)).strftime("%Y-%m-%d")
        hits = [e for e in hits if since <= titles.get(e["properties"].get("title_id"), {}).get("release_date", "") <= start.strftime("%Y-%m-%d")]
    elif gid == "G09":
        days = sorted({ist_day(parse(e["received_at"])) for e in hits})
        if len(days) < 3:
            return None
        third = days[2]
        return next(e for e in hits if ist_day(parse(e["received_at"])) == third)
    return hits[0] if hits else None


def judge_goal(goal, events, titles, data_end):
    start, end = goal["started_at"], goal["deadline"]
    window = [e for e in events if start < e["received_at"] <= end]
    hit = _signal(goal, window, titles)
    cut = hit["received_at"] if hit else end
    exposed = any((e["event"] == "home_page_shown" and e["properties"].get("personalised"))
                  or e["event"] in ("message_delivered", "message_clicked")
                  for e in events if start < e["received_at"] <= cut)
    if hit:
        result, reason = "succeeded", "%s seen inside the window" % hit["event"]
    elif end > data_end:
        result, reason = "inconclusive", "window ends after the last day of data we observed"
    else:
        result, reason = "failed", "no %s inside the window" % goal["success"]["event"]
    return {"result": result, "reason": reason, "signal_event_id": hit["event_id"] if hit else None,
            "signal_at": hit["received_at"] if hit else None, "exposed_to_agent": exposed}


def judge_all(world, outcome_path, out_path):
    """Judge every goal of every viewer, write outcomes.jsonl, and store what was learned per viewer."""
    by_user = defaultdict(list)
    data_end = ""
    for e in read_jsonl(outcome_path):
        by_user[e["user_id"]].append(e)
        data_end = max(data_end, e["received_at"])
    for evs in by_user.values():
        evs.sort(key=lambda e: (e["received_at"], e["event_id"]))
    rows = []
    for uid in sorted(world.store.states):
        st = world.store.states[uid]
        goals = list(st["goal_history"]) + ([st["goal"]] if st["goal"] else [])
        goals.sort(key=lambda g: g["started_at"])
        for i, goal in enumerate(goals):
            j = judge_goal(goal, by_user.get(uid, []), world.pack["titles"], data_end)
            row = {"viewer_id": uid, "arm": st["arm"], "goal_id": goal["id"], "started_at": goal["started_at"],
                   "deadline": goal["deadline"], "rule_version": goal["rule_version"],
                   "shadow": goal.get("shadow", False), "primary": i == 0,
                   "goal_status_at_replay_end": goal["status"], **j}
            rows.append(row)
            st["outcomes"].append(row)
            if j["result"] == "succeeded":
                note = "goal %s succeeded" % goal["id"]
            elif j["result"] == "failed":
                note = "goal %s failed%s" % (goal["id"], " even though a personalised page/message reached the viewer"
                                             if j["exposed_to_agent"] else " (no exposure to learn from)")
            else:
                note = "goal %s inconclusive: window not fully observed" % goal["id"]
            st["learned"].append(note)
        world.store.save(uid)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows, data_end
