
import re
from datetime import timedelta
from timeutil import parse, ist_day, is_quiet

FORBIDDEN = ["last chance", "hurry", "only today", "free forever", "guaranteed"]
ROW_TYPES = {"next_episode", "because_you_watched", "popular_in_dialect", "new_releases", "genre"}
DEEPLINK = re.compile(r"^ott://title/T\d+$")


def raw_subscription(profile, events, now):
    """Subscription status re-derived straight from the raw profile + events."""
    status = profile["subscription_status"] if profile else None
    since = profile["snapshot_at"] if profile else ""
    newer = False
    for e in events:
        if since < e["received_at"] <= now:
            if e["event"] == "purchase_completed":
                status, newer = ("trial_active" if e["properties"].get("is_trial") else "active"), True
            elif e["event"] == "subscription_renewed":
                status, newer = "active", True
            elif e["event"] == "subscription_lapsed":
                status, newer = "lapsed", True
    if profile and not newer and status in ("trial_active", "active"):
        if profile["plan_ends_at"] and profile["plan_ends_at"] < now:
            return "unknown"
    if not profile and status is None:
        if any(e["event"] == "signup_completed" for e in events):
            return "never"
        return "unknown"
    return status


def goal_supported(goal_id, profile, events, now, titles):
    """Is there evidence in the raw data that the viewer is eligible for this goal?"""
    now_dt = parse(now)
    events = [e for e in events if e["received_at"] <= now]
    status = raw_subscription(profile, events, now)
    plays = [e for e in events if e["event"] == "play_started"]
    signup = profile["signup_at"] if profile and profile["signup_at"] else next(
        (e["received_at"] for e in events if e["event"] == "signup_completed"), None)
    age_days = (now_dt - parse(signup)).total_seconds() / 86400 if signup else None
    if goal_id == "G01":
        return age_days is not None and age_days <= 7 and not plays
    if goal_id == "G02":
        return not any(e["event"] == "episode_completed" for e in events)
    if goal_id == "G03":
        return age_days is not None and age_days <= 2
    if goal_id == "G04":
        return status == "trial_active"
    if goal_id == "G05":
        return status == "never"
    if goal_id == "G06":
        return status == "lapsed"
    if goal_id == "G07":
        return any(titles.get(e["properties"].get("title_id"), {}).get("content_type") in ("show", "micro_drama")
                   for e in plays)
    if goal_id in ("G08", "G09"):
        return status == "active"
    if goal_id == "G12":
        return not any(e["event"] == "app_open" and now_dt - parse(e["received_at"]) <= timedelta(days=14)
                       for e in events)
    return False     # G10 / G11 (unmeasurable) and anything unknown


def _title_valid(titles, tid, dialect, at, env):
    t = titles.get(tid)
    if t is None:
        return "unknown title " + str(tid)
    if tid in env["quarantined"]:
        return "quarantined title " + tid
    if t["dialect"] != dialect:
        return "%s is %s, page is %s" % (tid, t["dialect"], dialect)
    if t["available_from"] > at or (t["available_until"] and at >= t["available_until"]):
        return tid + " not available"
    gone = env["withdrawn"].get(tid)
    if gone and at >= gone:
        return tid + " withdrawn"
    return None


def check_home_page(page, env):
    problems = []
    app_dialect = env["trigger"].get("app_dialect") or next(
        (e["app_dialect"] for e in reversed(env["events"]) if e.get("app_dialect")), None)
    if page["dialect"] != app_dialect:
        problems.append("page dialect %s != app setting %s" % (page["dialect"], app_dialect))
    if len(page["rows"]) > 6 or not page["rows"]:
        problems.append("row count %d not in 1..6" % len(page["rows"]))
    slugs = []
    ids = [t for r in page["rows"] for t in r["title_ids"]]
    if page.get("hero_title_id"):
        ids.append(page["hero_title_id"])
    for r in page["rows"]:
        if r["row_type"] not in ROW_TYPES:
            problems.append("bad row type " + r["row_type"])
        if not 3 <= len(r["title_ids"]) <= 10:
            problems.append("row %s has %d titles" % (r["row_type"], len(r["title_ids"])))
    for tid in ids:
        why = _title_valid(env["titles"], tid, page["dialect"], env["now"], env)
        if why:
            problems.append(why)
        else:
            slugs.append(env["titles"][tid]["content_slug"])
    if len(slugs) != len(set(slugs)):
        problems.append("same content_slug twice on the page")
    return problems


def check_push(push, env):
    """Returns a list of (check_name, reason)."""
    problems = []
    profile, events = env["profile"], env["events"]
    opted_in = bool(profile) and profile["notifications_opt_in"] == "true"
    opted_out = any(e["event"] == "notification_opt_out" and e["properties"].get("channel") == "push"
                    for e in events)
    if not opted_in or opted_out:
        problems.append(("consent", "no push consent (profile opt-in=%s, opt-out event=%s)" % (opted_in, opted_out)))
    send_at = parse(push["send_at"])
    if is_quiet(send_at):
        problems.append(("quiet_hours", "send_at is inside 22:00-08:00 IST"))
    day = ist_day(send_at)
    sent_today = sum(1 for e in events if e["event"] == "message_delivered"
                     and e["properties"].get("channel") in ("push", "whatsapp")
                     and ist_day(parse(e["received_at"])) == day)
    sent_today += sum(1 for s in env["scheduled"] if ist_day(parse(s)) == day)
    if sent_today >= 1:
        problems.append(("frequency_cap", "already %d message(s) on that IST day" % sent_today))
    text = push["text"]
    low = (text["title"] + " " + text["body"]).lower()
    if len(text["title"]) > 40 or len(text["body"]) > 120:
        problems.append(("push_copy", "text too long"))
    if any(p in low for p in FORBIDDEN) or "₹" in low or "price" in low:
        problems.append(("push_copy", "forbidden phrase or price claim"))
    if not DEEPLINK.match(push["deeplink"]):
        problems.append(("push_copy", "bad deeplink format"))
    why = _title_valid(env["titles"], push["title_id"], env["trigger"].get("app_dialect"), push["send_at"], env)
    if not env["trigger"].get("app_dialect"):
        problems.append(("dialect", "no current app dialect for the push"))
    if why:
        problems.append(("title_valid", why))
    return problems


def verify(plan, env):
    """plan: {'arm','goal_id','home_page','push', 'keys'}; env: raw data needed for the checks.
    Returns {'passed', 'failed', 'drop_home_page', 'drop_push', 'block_all'}."""
    passed, failed = [], []
    out = {"drop_home_page": False, "drop_push": False, "block_all": False}

    def fail(name, reason):
        failed.append({"check": name, "reason": reason})

    if plan["arm"] == "holdout" and (plan.get("home_page") or plan.get("push")):
        fail("holdout", "holdout viewers must receive no action")
        out["block_all"] = True
    elif plan["arm"] == "holdout":
        passed.append("holdout")

    if plan.get("goal_id"):
        if goal_supported(plan["goal_id"], env["profile"], env["events"], env["now"], env["titles"]):
            passed.append("goal_eligibility")
        else:
            fail("goal_eligibility", "no evidence in raw data for goal " + plan["goal_id"])
            out["block_all"] = True

    for key in plan.get("keys", []):
        if key in env["seen_keys"]:
            fail("duplicate_action", "action already published: " + key)
            out["block_all"] = True
    if not any(f["check"] == "duplicate_action" for f in failed):
        passed.append("duplicate_action")

    if plan.get("home_page") and not out["block_all"]:
        problems = check_home_page(plan["home_page"], env)
        if problems:
            fail("home_page_rules", "; ".join(problems[:3]))
            out["drop_home_page"] = True
        else:
            passed += ["dialect", "home_page_rules", "title_valid"]

    if plan.get("push") and not out["block_all"]:
        problems = check_push(plan["push"], env)
        names = {p[0] for p in problems}
        for name, reason in problems:
            fail(name, reason)
        if problems:
            out["drop_push"] = True
        passed += [c for c in ("consent", "quiet_hours", "frequency_cap", "push_copy") if c not in names]

    out["passed"], out["failed"] = passed, failed
    return out
