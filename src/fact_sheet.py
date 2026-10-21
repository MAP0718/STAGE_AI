
import re
from timeutil import parse

DEEPLINK = re.compile(r"ott://c/[^/]+/(d\d)/title/")


def fact(value, source, as_of, now=None):
    age = None
    if as_of and now:
        age = round((parse(now) - parse(as_of)).total_seconds() / 3600, 1)
    return {"value": value, "source": source, "as_of": as_of, "age_hours": age}


def _subscription(profile, events, now, conflicts, assumptions, evidence):
    relevant = [e for e in events if e["event"] in
                ("purchase_completed", "subscription_renewed", "subscription_lapsed")]
    if profile:
        base = fact(profile["subscription_status"], "profile", profile["snapshot_at"], now)
        relevant = [e for e in relevant if e["received_at"] > profile["snapshot_at"]]
    else:
        base = None
    if relevant:
        last = relevant[-1]
        if last["event"] == "subscription_lapsed":
            value = "lapsed"
        elif last["event"] == "purchase_completed" and last["properties"].get("is_trial"):
            value = "trial_active"
        else:
            value = "active"
        evidence.append("event:" + last["event_id"])
        if base and base["value"] != value:
            conflicts.append("profile_subscription:" + base["value"])
        return fact(value, "event:" + last["event"], last["received_at"], now)
    if base:
        end = profile["plan_ends_at"]
        if base["value"] in ("trial_active", "active") and end and end < now:
            assumptions.append("plan_ends_at passed with no renewal event, so subscription is unknown")
            return fact("unknown", "profile+plan_ends_at", profile["snapshot_at"], now)
        evidence.append("profile:" + profile["user_id"])
        return base
    signup = [e for e in events if e["event"] == "signup_completed"]
    if signup:
        assumptions.append("no profile row; signed up in the event stream and no purchase seen, so 'never' paid")
        evidence.append("event:" + signup[0]["event_id"])
        return fact("never", "derived:signup_event", signup[0]["received_at"], now)
    assumptions.append("no profile row and no signup event: subscription unknown")
    return fact("unknown", "none", None, now)


def build_facts(uid, profile, events, trigger, now):
    """profile: one row or None. events: this viewer's events. trigger: the app_open/login event."""
    events = [e for e in events if e["received_at"] <= now]
    facts, conflicts, assumptions, evidence = {}, [], [], []
    if trigger:
        evidence.append("trigger:" + trigger["event_id"])

    # ---- dialect: app setting wins ----
    if trigger and trigger.get("app_dialect"):
        facts["dialect"] = fact(trigger["app_dialect"], "app_setting", trigger["received_at"], now)
        facts["language"] = fact(trigger.get("app_language"), "app_setting", trigger["received_at"], now)
    elif any(e.get("app_dialect") for e in events):
        last = [e for e in events if e.get("app_dialect")][-1]
        facts["dialect"] = fact(last["app_dialect"], "app_setting(last known)", last["received_at"], now)
        facts["language"] = fact(last.get("app_language"), "app_setting(last known)", last["received_at"], now)
        assumptions.append("trigger had no app dialect; used the last known app setting")
    elif profile:
        facts["dialect"] = fact(profile["preferred_dialect"], "profile", profile["snapshot_at"], now)
        facts["language"] = fact(profile["preferred_language"], "profile", profile["snapshot_at"], now)
        assumptions.append("dialect taken from a one-day-stale profile; no app setting seen")
    else:
        facts["dialect"] = fact(None, "none", None, now)
        facts["language"] = fact(None, "none", None, now)
    used = facts["dialect"]["value"]
    if profile and used and profile["preferred_dialect"] != used:
        conflicts.append("profile_dialect:" + profile["preferred_dialect"])
    if trigger:
        m = DEEPLINK.match(trigger.get("properties", {}).get("deeplink", "") or "")
        if m and used and m.group(1) != used:
            conflicts.append("campaign_link_dialect:" + m.group(1))

    # ---- subscription ----
    facts["subscription"] = _subscription(profile, events, now, conflicts, assumptions, evidence)

    # ---- consent: opt-out events beat the profile; no profile = no consent ----
    for channel, field in (("push", "notifications_opt_in"), ("whatsapp", "whatsapp_opt_in")):
        out = [e for e in events if e["event"] == "notification_opt_out"
               and e["properties"].get("channel") == channel]
        if out:
            facts[channel + "_consent"] = fact(False, "opt_out_event", out[-1]["received_at"], now)
            evidence.append("event:" + out[-1]["event_id"])
        elif profile:
            facts[channel + "_consent"] = fact(profile[field] == "true", "profile", profile["snapshot_at"], now)
        else:
            facts[channel + "_consent"] = fact(False, "no_profile_row", None, now)
            assumptions.append("no profile row, so no %s consent assumed" % channel)

    # ---- signup date ----
    if profile and profile["signup_at"]:
        facts["signup_at"] = fact(profile["signup_at"], "profile", profile["snapshot_at"], now)
    else:
        sig = [e for e in events if e["event"] == "signup_completed"]
        if sig:
            facts["signup_at"] = fact(sig[0]["received_at"], "event:signup_completed", sig[0]["received_at"], now)
        else:
            facts["signup_at"] = fact(None, "none", None, now)

    return {"user_id": uid, "now": now, "facts": facts, "conflicts": conflicts,
            "assumptions": assumptions, "evidence": evidence}


if __name__ == "__main__":
    import json
    import sys
    from loader import load_pack
    pack = load_pack(sys.argv[1])
    uid, now = "U-02344", "2026-09-30T18:32:59.458Z"
    trig = {"event_id": "t1", "app_dialect": "d3", "app_language": "hin",
            "received_at": now, "properties": {}}
    print(json.dumps(build_facts(uid, pack["profiles"].get(uid), pack["events"].get(uid, []), trig, now), indent=1))
