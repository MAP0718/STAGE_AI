
from datetime import timedelta
from timeutil import parse, iso, ist_day

# Order matters: the first eligible goal in this list is chosen.
PRIORITY = ["G04", "G06", "G01", "G03", "G12", "G07", "G02", "G05", "G09", "G08"]
UNMEASURABLE = {
    "G10": "no success event exists in the data",
    "G11": "success event tv_app_open does not exist in the event stream",
}
PAID_GOALS = {"G04", "G05", "G06"}   # goals that make no sense after a purchase


def _days_since(iso_str, now_dt):
    return (now_dt - parse(iso_str)).total_seconds() / 86400


def eligible_goals(facts, events, titles, episodes, now):
    """Return [{'goal_id', 'evidence', 'context'}] in priority order. `events` = viewer events before `now`."""
    now_dt = parse(now)
    sub = facts["subscription"]["value"]
    signup = facts["signup_at"]["value"]
    plays = [e for e in events if e["event"] == "play_started"]
    completes = [e for e in events if e["event"] == "episode_completed"]
    opens = [e for e in events if e["event"] == "app_open"]
    ok = {}

    if signup and _days_since(signup, now_dt) <= 7 and not plays:
        ok["G01"] = {"evidence": ["signup:" + signup, "no play_started in history"], "context": {}}
    if signup and _days_since(signup, now_dt) <= 2:
        ok["G03"] = {"evidence": ["signup:" + signup], "context": {}}
    if not completes:
        ok["G02"] = {"evidence": ["no episode_completed in the 30-day history"], "context": {}}
    if sub == "trial_active":
        ok["G04"] = {"evidence": ["subscription:" + facts["subscription"]["source"]], "context": {}}
    if sub == "never":
        ok["G05"] = {"evidence": ["subscription:" + facts["subscription"]["source"]], "context": {}}
    if sub == "lapsed":
        ok["G06"] = {"evidence": ["subscription:" + facts["subscription"]["source"]], "context": {}}

    recent_opens = [e for e in opens if now_dt - parse(e["received_at"]) <= timedelta(days=14)]
    if not recent_opens:
        ev = ["last app_open:" + opens[-1]["event_id"]] if opens else ["no app_open in history"]
        ok["G12"] = {"evidence": ev + ["no app_open in the 14 days before the trigger"], "context": {}}

    # G07: a show / micro-drama that was started, has episodes left, and was not played for 3+ days
    best = None
    by_slug = {}
    for e in plays:
        t = titles.get(e["properties"].get("title_id"))
        if t and t["content_type"] in ("show", "micro_drama"):
            by_slug[t["content_slug"]] = e
    for slug, last_play in by_slug.items():
        done = {e["properties"].get("episode_id") for e in completes
                if titles.get(e["properties"].get("title_id"), {}).get("content_slug") == slug}
        total = len(episodes.get(slug, []))
        idle_days = _days_since(last_play["received_at"], now_dt)
        if total and len(done) < total and idle_days >= 3:
            if best is None or last_play["received_at"] > best[1]["received_at"]:
                best = (slug, last_play)
    if best:
        ok["G07"] = {"evidence": ["event:" + best[1]["event_id"], "episodes:" + best[0]],
                     "context": {"series_slug": best[0]}}

    if sub == "active":
        play_days = {ist_day(parse(e["received_at"])) for e in plays
                     if now_dt - parse(e["received_at"]) <= timedelta(days=14)}
        if len(play_days) <= 1:
            ok["G09"] = {"evidence": ["subscription:" + facts["subscription"]["source"],
                                      "plays on %d day(s) in the last 14 days" % len(play_days)], "context": {}}
        ok["G08"] = {"evidence": ["subscription:" + facts["subscription"]["source"]], "context": {}}

    return [dict(goal_id=g, **ok[g]) for g in PRIORITY if g in ok]


def make_goal(goal_id, goals_by_id, overrides, now, context=None):
    """Create the goal object: deadline, success signal and which version of the rule it uses."""
    g = goals_by_id[goal_id]
    corrected = overrides.get(goal_id)
    success = corrected["new_success"] if corrected else g["success"]
    return {
        "id": goal_id, "name": g["name"], "started_at": now,
        "deadline": iso(parse(now) + timedelta(hours=g["success"]["window_hours"])),
        "success": success, "rule_version": "v2-corrected" if corrected else "v1",
        "context": context or {}, "status": "open",
    }
