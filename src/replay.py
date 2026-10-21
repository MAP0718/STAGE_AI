
import argparse
import glob
import json
import os
import subprocess
import sys
from collections import Counter

import impact_report
import outcome_judge
from agent import StateStore, World
from loader import load_pack, load_replay
from model_provider import MockModelProvider
from timeutil import parse


def find_pack_root(path):
    """Accept the pack folder itself or a folder that contains it (zip extracted one level deeper)."""
    if os.path.exists(os.path.join(path, "data", "events.jsonl")):
        return path
    for sub in sorted(glob.glob(os.path.join(path, "*"))):
        if os.path.exists(os.path.join(sub, "data", "events.jsonl")):
            return sub
    raise SystemExit("Could not find data/events.jsonl under %s. Pass --pack <folder with data/ config/ replay/>" % path)


def windows(controls, dependency):
    return [(c["at"], c["until"]) for c in controls
            if c["type"] in ("dependency_unavailable", "dependency_degraded") and c["dependency"] == dependency]


def percentile(values, q):
    values = sorted(values)
    if not values:
        return 0
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def run_replay(pack, replay, out_dir, limit=None):
    controls = replay["control_events"]
    model = MockModelProvider(faults=replay["model_faults"], outages=windows(controls, "model_provider"))
    state_dir = os.path.join(out_dir, "viewer_state")
    os.makedirs(state_dir, exist_ok=True)
    for old in glob.glob(os.path.join(state_dir, "*.json")):
        os.remove(old)                               # every run starts from clean state
    world = World(pack, model, StateStore(state_dir), model_outages=model.outages,
                  store_degraded=windows(controls, "user_data_store"))

    triggers = replay["triggers"][:limit] if limit else replay["triggers"]
    triggered = {t["user_id"] for t in triggers}
    timeline = [(c["at"], 0, "control", c) for c in controls]
    timeline += [(e["deliver_at"], 1, "live", e) for e in replay["live_events"]]
    timeline += [(t["deliver_at"], 2, "trigger", t) for t in triggers]
    timeline.sort(key=lambda x: (x[0], x[1]))

    for at, _, kind, item in timeline:
        if kind == "trigger":
            world.agent(item["user_id"]).on_trigger(item)
        elif kind == "live":
            if item["user_id"] in triggered:
                world.agent(item["user_id"]).on_live_event(item)
            else:
                world.events.setdefault(item["user_id"], []).append(dict(item, _live=True))
        elif item["type"] == "title_withdrawn":
            world.title_withdrawn(item)
        elif item["type"] == "goal_rule_corrected":
            world.goal_rule_corrected(item)
        else:
            world.incidents.append({"at": at, "type": item["type"], "dependency": item["dependency"],
                                    "until": item["until"], "detail": item["detail"]})
    return world, triggers


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def latency_report(world, triggers, path):
    real = [d for d in world.decisions if d["status"] != "DUPLICATE_IGNORED"]
    ms = [d["decided_in_ms"] for d in real]
    per_min, per_10 = Counter(), Counter()
    for t in triggers:
        dt = parse(t["deliver_at"])
        per_min[dt.strftime("%Y-%m-%dT%H:%M")] += 1
        per_10[dt.strftime("%Y-%m-%dT%H") + str(dt.minute // 10)] += 1
    rep = {
        "decisions": len(world.decisions), "decisions_timed": len(ms),
        "p50_ms": percentile(ms, 0.50), "p95_ms": percentile(ms, 0.95), "p99_ms": percentile(ms, 0.99),
        "max_ms": max(ms) if ms else 0, "budget_ms": 60000,
        "decisions_over_60s": sum(1 for x in ms if x > 60000), "decisions_over_5min": sum(1 for x in ms if x > 300000),
        "decisions_with_fallback": sum(1 for d in real if d["fallback_used"]),
        "fallbacks": dict(Counter(f.split(":")[0] for d in real for f in d["fallback_used"])),
        "busiest_minute_triggers": max(per_min.values()) if per_min else 0,
        "busiest_10_minutes_triggers": max(per_10.values()) if per_10 else 0,
        "max_model_calls_in_a_decision": max((d["model_calls"] for d in real), default=0),
        "max_tool_calls_in_a_decision": max((d["tool_calls"] for d in real), default=0),
        "note": ("Real processing time on this machine plus simulated waiting for timeouts (the mock does not sleep). "
                 "published_at = deliver_at + decided_in_ms."),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    return rep


def write_final_report(path, pack, world, triggers, lat, impact, rows, sim_report):
    real = [d for d in world.decisions if d["status"] != "DUPLICATE_IGNORED"]
    reasons = Counter((d["no_action_reason"] or "").split(":")[0] for d in real if d["status"] == "NO_ACTION")
    goals = Counter(d["goal"]["id"] for d in real if d["goal"] and d["status"] == "PUBLISHED")
    actions = Counter(d["action"] for d in real if d["status"] == "PUBLISHED")
    st = pack["stats"]
    inc = world.incidents
    purchases = [i for i in inc if i["type"] == "purchase_replan"]
    wd = [i for i in inc if i["type"] == "title_withdrawn"]
    rc = [i for i in inc if i["type"] == "goal_rule_corrected"]
    fb = lat["fallbacks"]
    L = ["# Final report\n", "## What ran\n",
         "%d triggers replayed (%d duplicates ignored). Published decisions: %d. Everything else is a recorded "
         "'no action' with a reason.\n" % (len(triggers), world.counters["duplicate_trigger_ignored"], world.counters["published"]),
         "| Published action | Count |", "|---|---|"]
    L += ["| %s | %d |" % (k, v) for k, v in actions.most_common()]
    L += ["\n| Reason for no action | Count |", "|---|---|"]
    L += ["| %s | %d |" % (k, v) for k, v in reasons.most_common()]
    L += ["\n| Goal on published decisions | Count |", "|---|---|"]
    L += ["| %s | %d |" % (k, v) for k, v in goals.most_common()]
    L += ["\n## Latency\n",
          "p50 %d ms, p95 %d ms, p99 %d ms, max %d ms (budget 60,000 ms). Over 60 s: %d. Over 5 min: %d. "
          "Busiest minute: %d triggers; busiest 10 minutes: %d triggers.\n" % (
              lat["p50_ms"], lat["p95_ms"], lat["p99_ms"], lat["max_ms"], lat["decisions_over_60s"],
              lat["decisions_over_5min"], lat["busiest_minute_triggers"], lat["busiest_10_minutes_triggers"]),
          "## Data issues found and how they were handled\n",
          "| Issue | Found | Handling |", "|---|---|---|",
          "| Duplicate events (same event_id) | %d in events.jsonl, %d duplicate triggers | first one kept; repeat triggers recorded as DUPLICATE_IGNORED, no second action |" % (st["duplicate_events_dropped"], world.counters["duplicate_trigger_ignored"]),
          "| Users with two profile rows | %d | latest updated_at wins |" % st["profile_rows_duplicated"],
          "| Profile is a day stale / missing for new viewers | 50 viewers with events but no row, plus new signups on replay day | newer events override the profile; no row = facts from events, marked as assumptions, no consent assumed |",
          "| Wrong phone clocks | many events | ordering and freshness use received_at only; trigger age = sent_at - occurred_at (both on the phone clock, so the error cancels) |",
          "| Offline-buffered app opens | %d triggers older than 10 min | treated as stale: no action, recorded |" % world.counters["stale_trigger"],
          "| Same visit delivered as several triggers | %d duplicate sessions | one decision per session |" % world.counters["duplicate_session"],
          "| Dialect conflicts | profile / app setting / campaign link | app setting used (it decides what the viewer sees); others kept in `conflicts` |",
          "| Titles with several title_ids | %d content_slugs | one title per content_slug per page |" % st["slugs_with_several_title_ids"],
          "| Instructions hidden in descriptions | %s | titles quarantined, text dropped, never obeyed, never shown |" % ", ".join(st["quarantined_titles"]),
          "| Goals G10, G11 cannot be measured | G10: no success event; G11: tv_app_open does not exist | never chosen |",
          "| Unclear goal rules | G06 'recently', G12/G02 history | assumptions written in goal_picker.py |",
          "| Pilot report compares served vs everyone else | see impact_report.md | recomputed with the randomised arms |",
          "\n## Surprises on the replay day\n",
          "| Surprise | What happened |", "|---|---|",
          "| Offline-buffered app opens | %d stale triggers skipped |" % world.counters["stale_trigger"],
          "| Purchase after the decision | %d viewers re-planned; goal closed, pending messages cancelled (%d) |" % (len(purchases), sum(i["messages_cancelled"] for i in purchases)),
          "| Unsupported model claim | %d rejected (`unsupported_model_claim`); goal came from the rules |" % world.counters["unsupported_model_claim"],
          "| Broken model JSON | %d handled with rule fallback |" % world.counters["model_malformed"],
          "| Model timeout / outage | fallbacks: %s |" % json.dumps(fb),
          "| Title withdrawn | %s |" % ("; ".join("%s: %d pending messages cancelled, %d live pages republished" % (i["title_id"], len(i["pending_messages_cancelled"]), len(i["live_pages_republished_for"])) for i in wd) or "none"),
          "| Goal rule corrected | %s |" % ("; ".join("%s: %d open goals updated, nobody else reprocessed" % (i["goal_id"], i["open_goals_updated"]) for i in rc) or "none"),
          "| Data store degraded | cached-state fallbacks: %d (these decisions send no messages) |" % sum(1 for d in world.decisions if any(f.startswith("data_store_timeout") for f in d["fallback_used"])),
          "| Injected instruction in a description | quarantined, see above |",
          "\n## Impact\n"]
    if impact["main"]:
        m = impact["main"]
        L.append("Agent arm %s vs holdout %s: uplift %s (95%% CI %s to %s). Details and the comparison that misleads are in impact_report.md.\n" % (
            impact_report.pct(m["pa"]), impact_report.pct(m["pb"]), impact_report.pts(m["diff"]),
            impact_report.pts(m["lo"]), impact_report.pts(m["hi"])))
    if sim_report:
        L.append("Simulator policy problems on our actions: %d.\n" % sim_report.get("policy_problem_count", 0))
    L.append("See KNOWN_LIMITATIONS.md for what was left out on purpose.\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pack", default=os.path.join(here, "..", "..", "pack"), help="folder with data/ config/ replay/")
    ap.add_argument("--out", default=os.path.join(here, "..", "sample_run"), help="output folder")
    ap.add_argument("--seed", default="oua-practice", help="simulator seed")
    ap.add_argument("--no-sim", action="store_true", help="skip the simulator and the impact report")
    ap.add_argument("--limit", type=int, default=None, help="only replay the first N triggers (for quick tests)")
    a = ap.parse_args()
    pack_dir = os.path.abspath(find_pack_root(a.pack))
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    print("loading pack ...")
    pack = load_pack(pack_dir)
    replay = load_replay(pack_dir)
    print("replaying %d triggers ..." % (a.limit or len(replay["triggers"])))
    world, triggers = run_replay(pack, replay, out, a.limit)

    write_jsonl(os.path.join(out, "decisions.jsonl"), world.decisions)
    actions = sorted(world.actions, key=lambda x: x["published_at"])
    write_jsonl(os.path.join(out, "actions.jsonl"), actions)
    write_jsonl(os.path.join(out, "incidents.jsonl"), world.incidents + [dict(c, type="cancelled_action") for c in world.cancelled])
    lat = latency_report(world, triggers, os.path.join(out, "latency_report.json"))
    print("decisions: %d | published: %d | actions: %d | max latency: %d ms" % (
        len(world.decisions), world.counters["published"], len(actions), lat["max_ms"]))
    if a.no_sim:
        return

    sim_dir = os.path.join(out, "sim")
    sim = os.path.join(pack_dir, "simulator", "simulate_outcomes.py")
    print("running the simulator ...")
    subprocess.run([sys.executable, sim, "--pack", pack_dir, "--actions", os.path.join(out, "actions.jsonl"),
                    "--out", sim_dir, "--seed", a.seed], check=True)
    with open(os.path.join(sim_dir, "simulator_report.json"), encoding="utf-8") as f:
        sim_report = json.load(f)

    rows, data_end = outcome_judge.judge_all(world, os.path.join(sim_dir, "outcome_events.jsonl"),
                                             os.path.join(out, "outcomes.jsonl"))
    impact = impact_report.build(rows, world, pack_dir, os.path.join(out, "impact_report.md"), sim_report)
    write_final_report(os.path.join(out, "final_report.md"), pack, world, triggers, lat, impact, rows, sim_report)
    res = Counter(r["result"] for r in rows if r["primary"])
    print("outcomes (first goal per viewer):", dict(res))
    if impact["main"]:
        m = impact["main"]
        print("agent %s vs holdout %s -> uplift %s (95%% CI %s to %s)" % (
            impact_report.pct(m["pa"]), impact_report.pct(m["pb"]), impact_report.pts(m["diff"]),
            impact_report.pts(m["lo"]), impact_report.pts(m["hi"])))
    print("simulator policy problems:", sim_report.get("policy_problem_count"))
    print("done. See", out)


if __name__ == "__main__":
    main()
