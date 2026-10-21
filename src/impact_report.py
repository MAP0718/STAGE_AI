
import csv
import json
import math
import os
from collections import defaultdict


def rate(s, f):
    return s / (s + f) if (s + f) else float("nan")


def compare(a_s, a_f, b_s, b_f):
    """Difference in success rate a - b among conclusive viewers, with a 95% CI and a two-sided p-value."""
    na, nb = a_s + a_f, b_s + b_f
    if not na or not nb:
        return None
    pa, pb = a_s / na, b_s / nb
    se = math.sqrt(pa * (1 - pa) / na + pb * (1 - pb) / nb)
    diff = pa - pb
    p = math.erfc(abs(diff / se) / math.sqrt(2)) if se > 0 else 1.0
    return {"pa": pa, "pb": pb, "diff": diff, "lo": diff - 1.96 * se, "hi": diff + 1.96 * se, "p": p, "na": na, "nb": nb}


def pct(x):
    return "n/a" if x != x else "%.1f%%" % (100 * x)


def pts(x):
    return "%+.1f pts" % (100 * x)


def tally(rows):
    c = {"succeeded": 0, "failed": 0, "inconclusive": 0}
    for r in rows:
        c[r["result"]] += 1
    return c


def pilot_reanalysis(pack_dir):
    path_o = os.path.join(pack_dir, "pilot", "pilot_outcomes.csv")
    if not os.path.exists(path_o):
        return None
    with open(path_o, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    yes = lambda r: r["succeeded"] == "true"   # noqa: E731
    served = [r for r in rows if r["served"] == "true"]
    others = [r for r in rows if r["served"] != "true"]
    treat = [r for r in rows if r["arm"] == "treatment"]
    hold = [r for r in rows if r["arm"] == "holdout"]
    t_unserved = [r for r in treat if r["served"] != "true"]

    def r_(group):
        return sum(1 for r in group if yes(r)) / len(group) if group else float("nan")

    itt = compare(sum(map(yes, treat)), len(treat) - sum(map(yes, treat)),
                  sum(map(yes, hold)), len(hold) - sum(map(yes, hold)))
    return {"served_n": len(served), "served": r_(served), "others_n": len(others), "others": r_(others),
            "treat_n": len(treat), "treat": r_(treat), "hold_n": len(hold), "hold": r_(hold),
            "treat_unserved_n": len(t_unserved), "treat_unserved": r_(t_unserved), "itt": itt}


def build(rows, world, pack_dir, out_path, sim_report=None):
    primary = [r for r in rows if r["primary"]]
    agent = [r for r in primary if r["arm"] == "agent"]
    hold = [r for r in primary if r["arm"] == "holdout"]
    ta, th = tally(agent), tally(hold)
    main = compare(ta["succeeded"], ta["failed"], th["succeeded"], th["failed"])

    served_ids = {a["user_id"] for a in world.actions}
    served = [r for r in agent if r["viewer_id"] in served_ids]
    not_served_agent = [r for r in agent if r["viewer_id"] not in served_ids]
    others = not_served_agent + hold
    ts, to = tally(served), tally(others)
    naive = compare(ts["succeeded"], ts["failed"], to["succeeded"], to["failed"])
    tn = tally(not_served_agent)

    L = []
    L.append("# Impact report\n")
    L.append("Replay day 2026-10-01 IST. Each viewer is counted once, by the FIRST goal the agent committed to for them "
             "(holdout viewers get the same goal rule as a *shadow goal* but no action). "
             "Rates use conclusive outcomes (succeeded + failed); inconclusive goals are listed separately.\n")
    L.append("## 1. Main result: agent arm vs holdout arm (intention-to-treat)\n")
    L.append("| Arm | Viewers with a goal | Succeeded | Failed | Inconclusive | Success rate |")
    L.append("|---|---|---|---|---|---|")
    L.append("| Agent | %d | %d | %d | %d | %s |" % (len(agent), ta["succeeded"], ta["failed"], ta["inconclusive"], pct(rate(ta["succeeded"], ta["failed"]))))
    L.append("| Holdout | %d | %d | %d | %d | %s |" % (len(hold), th["succeeded"], th["failed"], th["inconclusive"], pct(rate(th["succeeded"], th["failed"]))))
    if main:
        verdict = ("The confidence interval includes 0, so we cannot tell this apart from no effect."
                   if main["lo"] <= 0 <= main["hi"] else "The confidence interval excludes 0.")
        L.append("\n**Uplift: %s** (95%% CI %s to %s, p = %.3f). %s\n" % (pts(main["diff"]), pts(main["lo"]), pts(main["hi"]), main["p"], verdict))
        inc_a = ta["succeeded"] / len(agent) if agent else float("nan")
        inc_h = th["succeeded"] / len(hold) if hold else float("nan")
        L.append("Sensitivity (inconclusive counted as not succeeded): agent %s vs holdout %s." % (pct(inc_a), pct(inc_h)))
    L.append("\nBoth arms contain viewers the agent chose not to act on (no valid content, stale trigger, no consent for messages), "
             "so this number is the effect of *running the agent*, not of one action.\n")

    L.append("## 2. By goal (small groups: read with care)\n")
    L.append("| Goal | Agent n | Agent rate | Holdout n | Holdout rate | Difference |")
    L.append("|---|---|---|---|---|---|")
    by_goal = defaultdict(lambda: {"agent": [], "holdout": []})
    for r in primary:
        by_goal[r["goal_id"]][r["arm"]].append(r)
    for g in sorted(by_goal):
        a, h = tally(by_goal[g]["agent"]), tally(by_goal[g]["holdout"])
        c = compare(a["succeeded"], a["failed"], h["succeeded"], h["failed"])
        L.append("| %s | %d | %s | %d | %s | %s |" % (g, a["succeeded"] + a["failed"], pct(rate(a["succeeded"], a["failed"])),
                                                     h["succeeded"] + h["failed"], pct(rate(h["succeeded"], h["failed"])),
                                                     pts(c["diff"]) if c else "n/a"))

    L.append("\n## 3. The comparison that misleads: served vs everyone else\n")
    L.append("| Group | Viewers | Success rate |")
    L.append("|---|---|---|")
    L.append("| Served by the agent (got at least one action) | %d | %s |" % (len(served), pct(rate(ts["succeeded"], ts["failed"]))))
    L.append("| Everyone else (agent arm not served + holdout) | %d | %s |" % (len(others), pct(rate(to["succeeded"], to["failed"]))))
    if naive:
        L.append("\nNaive 'uplift': **%s**. The honest figure is %s." % (pts(naive["diff"]), pts(main["diff"]) if main else "n/a"))
    L.append("\nLook inside the agent arm: served viewers succeed at %s, unserved ones at %s. That gap of %s cannot be an "
             "effect of the agent on the unserved (they got nothing); it only shows that the agent serves a different, "
             "more active kind of viewer. The randomised comparison in section 1 does not have this problem." %
             (pct(rate(ts["succeeded"], ts["failed"])), pct(rate(tn["succeeded"], tn["failed"])),
              pts(rate(ts["succeeded"], ts["failed"]) - rate(tn["succeeded"], tn["failed"]))))

    pr = pilot_reanalysis(pack_dir)
    if pr:
        L.append("\n## 4. The earlier pilot report, recomputed\n")
        L.append("| Comparison | Group A | Group B | Difference |")
        L.append("|---|---|---|---|")
        L.append("| Pilot report (served vs everyone else) | %s (n=%d) | %s (n=%d) | %s |" % (pct(pr["served"]), pr["served_n"], pct(pr["others"]), pr["others_n"], pts(pr["served"] - pr["others"])))
        if pr["itt"]:
            i = pr["itt"]
            L.append("| Randomised arms (treatment vs holdout, everyone) | %s (n=%d) | %s (n=%d) | %s (95%% CI %s to %s) |" % (pct(pr["treat"]), pr["treat_n"], pct(pr["hold"]), pr["hold_n"], pts(i["diff"]), pts(i["lo"]), pts(i["hi"])))
        L.append("\nIn the pilot, treatment viewers who were NOT served succeeded at %s (n=%d), against %s for the holdout: "
                 "the 'served' group was selected, so the headline 2.3x is mostly selection, not effect." % (pct(pr["treat_unserved"]), pr["treat_unserved_n"], pct(pr["hold"])))

    if sim_report:
        L.append("\n## 5. Policy problems found by the simulator\n")
        L.append("Policy problems reported for our actions: **%d** (consent, quiet hours, frequency cap, holdout). "
                 "Home pages accepted: %s, rows dropped below minimum: %s." % (
                     sim_report.get("policy_problem_count", "?"),
                     sim_report["counts"].get("home_page_accepted", 0),
                     sim_report["counts"].get("home_page_row_dropped_below_minimum", 0)))
    L.append("\n## Honest caveats\n")
    L.append("* One replay day and one simulator seed: expect noise. Evaluators will rerun with another seed.")
    L.append("* The simulator is a model of viewers, not real viewers. It shows the pipeline is measured correctly; it does not prove real-world lift.")
    L.append("* Goals have different windows (1 to 14 days) and some are still open at the end of the data (inconclusive).")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return {"main": main, "naive": naive, "agent": ta, "holdout": th}
