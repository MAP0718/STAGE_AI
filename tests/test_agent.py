"""Tests that run on a tiny made-up pack (no data files needed).

Run from the submission folder:   python -m unittest discover -s tests -v
Required tests: late/duplicate trigger, conflicting source, tool failure, corrected goal rule.
"""
import os
import sys
import unittest
from collections import defaultdict
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(HERE, "..", "src"), os.path.join(HERE, "..")):
    if os.path.exists(os.path.join(cand, "agent.py")):
        sys.path.insert(0, os.path.abspath(cand))

from agent import StateStore, World          # noqa: E402
from model_provider import MockModelProvider  # noqa: E402
from outcome_judge import judge_goal          # noqa: E402
from timeutil import iso, parse               # noqa: E402

NOW = "2026-10-01T06:00:00.000Z"              # 11:30 IST, not quiet hours
NIGHT = "2026-10-01T17:00:00.000Z"            # 22:30 IST, quiet hours


def title(tid, dialect, genre, slug=None, quarantined=False):
    return {"title_id": tid, "content_slug": slug or "slug-" + tid, "title": "Title " + tid, "dialect": dialect,
            "language": "hin", "content_type": "film", "genres": [genre], "release_date": "2026-09-20",
            "available_from": "2026-01-01T00:00:00.000Z", "available_until": "", "is_premium": "false",
            "description": ""}


def make_pack(arms):
    titles = {}
    for i in range(1, 16):
        titles["T03%02d" % i] = title("T03%02d" % i, "d3", ["Drama", "Comedy", "Thriller"][i % 3])
    for i in range(1, 6):
        titles["T01%02d" % i] = title("T01%02d" % i, "d1", "Drama")
    popularity = {t: 100 - int(t[1:]) % 100 for t in titles}
    by_dialect = defaultdict(list)
    for t in titles.values():
        by_dialect[t["dialect"]].append(t)
    for d in by_dialect:
        by_dialect[d].sort(key=lambda t: (-popularity[t["title_id"]], t["title_id"]))
    win = {"G01": 24, "G02": 72, "G03": 48, "G04": 168, "G05": 168, "G06": 336, "G07": 72, "G08": 168,
           "G09": 336, "G12": 168}
    goals = [{"goal_id": g, "name": g, "success": {"event": "play_started", "condition": "any", "window_hours": h}}
             for g, h in win.items()]
    goals[2]["success"] = {"event": "app_open", "condition": "later IST day", "window_hours": 48}   # G03 original rule
    return {"events": {}, "profiles": {}, "titles": titles, "quarantined": set(), "episodes": {},
            "popularity": popularity, "by_dialect": dict(by_dialect), "goals": goals, "interventions": {},
            "arms": arms, "stats": {}}


def profile(uid, status="never", dialect="d3", push="true", signup="2025-01-01T00:00:00.000Z"):
    return {"user_id": uid, "signup_at": signup, "subscription_status": status, "plan_started_at": "",
            "plan_ends_at": "", "preferred_dialect": dialect, "preferred_language": "hin",
            "notifications_opt_in": push, "whatsapp_opt_in": "false", "platform": "android",
            "updated_at": "2026-09-29T00:00:00.000Z", "snapshot_at": "2026-09-30T00:00:00.000Z"}


def event(uid, eid, name, at, dialect="d3", props=None):
    return {"event_id": eid, "user_id": uid, "event": name, "occurred_at": at, "sent_at": at, "received_at": at,
            "session_id": "x" + eid, "app_dialect": dialect, "app_language": "hin", "platform": "android",
            "app_version": "6", "properties": props or {}}


def trigger(uid, eid, now=NOW, dialect="d3", age_s=0, session="s1"):
    t = event(uid, eid, "app_open", now, dialect)
    t["occurred_at"] = iso(parse(now) - timedelta(seconds=age_s))
    t["session_id"], t["deliver_at"] = session, now
    return t


def make_world(users, arms=None, faults=None, outages=None, degraded=None):
    """users: {uid: (profile_row, [events])}"""
    pack = make_pack(arms or {u: "agent" for u in users})
    for uid, (prof, evs) in users.items():
        if prof:
            pack["profiles"][uid] = prof
        pack["events"][uid] = evs
    model = MockModelProvider(faults=faults, outages=outages)
    return World(pack, model, StateStore(None), model_outages=outages, store_degraded=degraded)


OLD_OPEN = event("U", "e0", "app_open", "2026-09-29T10:00:00.000Z")   # keeps G12 away


class TestTriggers(unittest.TestCase):
    def test_duplicate_trigger_creates_one_action(self):
        w = make_world({"U": (profile("U"), [OLD_OPEN])})
        t = trigger("U", "t1")
        first = w.agent("U").on_trigger(t)
        second = w.agent("U").on_trigger(dict(t))
        self.assertEqual(first["status"], "PUBLISHED")
        self.assertEqual(second["status"], "DUPLICATE_IGNORED")
        self.assertEqual(len([a for a in w.actions if a["type"] == "home_page"]), 1)

    def test_same_session_second_trigger_is_skipped(self):
        w = make_world({"U": (profile("U"), [OLD_OPEN])})
        w.agent("U").on_trigger(trigger("U", "t1", session="same"))
        d = w.agent("U").on_trigger(trigger("U", "t2", session="same"))
        self.assertTrue(d["no_action_reason"].startswith("duplicate_session"))

    def test_stale_offline_buffered_trigger_is_not_a_live_visit(self):
        w = make_world({"U": (profile("U"), [OLD_OPEN])})
        d = w.agent("U").on_trigger(trigger("U", "t1", age_s=3 * 3600))
        self.assertEqual(d["status"], "NO_ACTION")
        self.assertTrue(d["no_action_reason"].startswith("stale_trigger"))
        self.assertEqual(w.actions, [])


class TestConflictingSources(unittest.TestCase):
    def test_app_setting_beats_profile_and_conflict_is_visible(self):
        w = make_world({"U": (profile("U", dialect="d1"), [OLD_OPEN])})
        d = w.agent("U").on_trigger(trigger("U", "t1", dialect="d3"))
        self.assertEqual(d["dialect"]["used"], "d3")
        self.assertEqual(d["dialect"]["source"], "app_setting")
        self.assertIn("profile_dialect:d1", d["dialect"]["conflicts"])
        page = [a for a in w.actions if a["type"] == "home_page"][0]
        self.assertEqual(page["dialect"], "d3")
        for tid in [t for r in page["rows"] for t in r["title_ids"]]:
            self.assertEqual(w.pack["titles"][tid]["dialect"], "d3")

    def test_newer_purchase_beats_stale_profile(self):
        buy = event("U", "p1", "purchase_completed", "2026-09-30T10:00:00.000Z",
                    props={"plan_id": "P_MONTH", "price_inr": 149, "is_trial": False})
        w = make_world({"U": (profile("U", status="never"), [OLD_OPEN, buy])})
        d = w.agent("U").on_trigger(trigger("U", "t1"))
        self.assertNotEqual(d["goal"]["id"], "G05")     # never offer 'first paid plan' to someone who paid
        self.assertIn("profile_subscription:never", d["conflicts"])


class TestSafetyAndAbstention(unittest.TestCase):
    def test_holdout_gets_nothing_but_a_shadow_goal(self):
        w = make_world({"U": (profile("U"), [OLD_OPEN])}, arms={"U": "holdout"})
        d = w.agent("U").on_trigger(trigger("U", "t1"))
        self.assertTrue(d["no_action_reason"].startswith("holdout_arm"))
        self.assertEqual(w.actions, [])
        self.assertIsNotNone(w.store.get("U")["goal"])

    def test_quiet_hours_push_is_moved_to_morning(self):
        w = make_world({"U": (profile("U"), [])})        # no app_open in 14 days -> goal G12 (push allowed)
        d = w.agent("U").on_trigger(trigger("U", "t1", now=NIGHT))
        push = [a for a in w.actions if a["type"] == "push"]
        self.assertEqual(d["goal"]["id"], "G12")
        self.assertEqual(len(push), 1)
        self.assertEqual(push[0]["send_at"], "2026-10-02T02:30:00.000Z")   # 08:00 IST next day

    def test_no_consent_no_push_and_frequency_cap(self):
        w = make_world({"A": (profile("A", push="false"), []), "B": (profile("B"), [])})
        w.agent("A").on_trigger(trigger("A", "ta"))
        self.assertEqual([a for a in w.actions if a["type"] == "push"], [])
        w.events["B"].append(event("B", "m1", "message_delivered", "2026-10-01T03:00:00.000Z",
                                           props={"channel": "push", "campaign_id": "c", "title_id": "T0301"}))
        d = w.agent("B").on_trigger(trigger("B", "tb"))
        self.assertEqual([a for a in w.actions if a["user_id"] == "B" and a["type"] == "push"], [])
        self.assertIn("frequency_cap", [f["check"] for f in d["checks"]["failed"]])

    def test_injected_title_is_never_used(self):
        w = make_world({"U": (profile("U"), [OLD_OPEN])})
        w.pack["quarantined"].add("T0301")
        w.agent("U").on_trigger(trigger("U", "t1"))
        page = [a for a in w.actions if a["type"] == "home_page"][0]
        self.assertNotIn("T0301", [t for r in page["rows"] for t in r["title_ids"]] + [page.get("hero_title_id")])

    def test_not_enough_titles_means_no_page(self):
        w = make_world({"U": (profile("U", dialect="d5"), [event("U", "e1", "app_open", "2026-09-29T10:00:00.000Z", "d5")])})
        d = w.agent("U").on_trigger(trigger("U", "t1", dialect="d5"))
        self.assertEqual(d["status"], "NO_ACTION")
        self.assertTrue(d["no_action_reason"].startswith("no_valid_content"))

    def test_purchase_after_decision_cancels_pending_push(self):
        w = make_world({"U": (profile("U", status="lapsed"), [OLD_OPEN])})
        d = w.agent("U").on_trigger(trigger("U", "t1"))
        self.assertEqual(d["goal"]["id"], "G06")
        self.assertEqual(len([a for a in w.actions if a["type"] == "push"]), 1)
        buy = event("U", "p1", "purchase_completed", "2026-10-01T06:02:00.000Z",
                    props={"plan_id": "P_MONTH", "price_inr": 149, "is_trial": False})
        w.agent("U").on_live_event(buy)
        self.assertEqual([a for a in w.actions if a["type"] == "push"], [])
        self.assertIsNone(w.store.get("U")["goal"])
        self.assertEqual(len(w.cancelled), 1)


class TestToolFailures(unittest.TestCase):
    def _run(self, **kw):
        w = make_world({"U": (profile("U"), [OLD_OPEN])}, **kw)
        return w, w.agent("U").on_trigger(trigger("U", "t1"))

    def test_model_outage_falls_back_to_rules(self):
        w, d = self._run(outages=[("2026-10-01T05:00:00.000Z", "2026-10-01T07:00:00.000Z")])
        self.assertEqual(d["status"], "PUBLISHED")
        self.assertTrue(any("model_unavailable" in f for f in d["fallback_used"]))
        self.assertEqual(d["goal"]["id"], "G02")

    def test_model_timeout_is_counted_in_latency_and_falls_back(self):
        w, d = self._run(faults={"t1": {"fault": "timeout", "seconds": 45}})
        self.assertEqual(d["status"], "PUBLISHED")
        self.assertGreaterEqual(d["decided_in_ms"], 3000)
        self.assertLess(d["decided_in_ms"], 60000)

    def test_malformed_model_output_is_parsed_safely(self):
        w, d = self._run(faults={"t1": {"fault": "malformed_output", "model_output": '{"goal_id": "G02", "eligible": tr'}})
        self.assertEqual(d["status"], "PUBLISHED")
        self.assertTrue(any("malformed" in f for f in d["fallback_used"]))

    def test_unsupported_model_claim_is_rejected(self):
        claim = {"goal_id": "G04", "eligible": True, "confidence": 0.97, "reason": "trial ends tomorrow (confirmed in billing)"}
        w, d = self._run(faults={"t1": {"fault": "unsupported_claim", "model_output": claim}})
        self.assertNotEqual(d["goal"]["id"], "G04")
        self.assertTrue(any("unsupported_model_claim" in f for f in d["fallback_used"]))

    def test_store_degraded_uses_cache_and_sends_no_message(self):
        win = [("2026-10-01T05:00:00.000Z", "2026-10-01T07:00:00.000Z")]
        sent = 0
        for i in range(40):                              # 20% of reads time out; find one that did
            uid = "U%d" % i
            w = make_world({uid: (profile(uid), [])}, degraded=win)
            d = w.agent(uid).on_trigger(trigger(uid, "t%d" % i))
            if any(f.startswith("data_store_timeout") for f in d["fallback_used"]):
                sent += len([a for a in w.actions if a["type"] == "push"])
                self.assertEqual(d["status"], "PUBLISHED")
                self.assertGreaterEqual(d["decided_in_ms"], 2000)
                break
        else:
            self.fail("no timeout reproduced")
        self.assertEqual(sent, 0)


class TestCorrectedGoalRule(unittest.TestCase):
    def test_only_open_goals_of_that_rule_are_updated_and_judged_on_new_rule(self):
        a_events = [OLD_OPEN, event("A", "pl", "play_started", "2026-09-30T10:00:00.000Z", props={"title_id": "T0301", "episode_id": "e"}),
                    event("A", "o2", "app_open", "2026-09-30T11:00:00.000Z")]
        users = {"A": (profile("A", signup="2026-09-30T00:00:00.000Z"), a_events),
                 "B": (profile("B"), [event("B", "b0", "app_open", "2026-09-29T10:00:00.000Z")])}
        w = make_world(users)
        w.agent("A").on_trigger(trigger("A", "ta"))
        w.agent("B").on_trigger(trigger("B", "tb"))
        self.assertEqual(w.store.get("A")["goal"]["id"], "G03")
        self.assertEqual(w.store.get("B")["goal"]["id"], "G02")
        before_b = dict(w.store.get("B")["goal"])
        w.goal_rule_corrected({"at": "2026-10-01T08:30:00.000Z", "goal_id": "G03",
                               "new_success": {"event": "play_started", "condition": "later IST day"}})
        ga, gb = w.store.get("A")["goal"], w.store.get("B")["goal"]
        self.assertEqual(ga["rule_version"], "v2-corrected")
        self.assertEqual(ga["success"]["event"], "play_started")
        self.assertEqual(gb, before_b)                                   # nobody else was touched
        self.assertEqual(w.incidents[-1]["open_goals_updated"], 1)
        # judging: an app_open on a later day was a success under the old rule, but not under the corrected one
        later_open = [event("A", "x1", "app_open", "2026-10-02T09:00:00.000Z")]
        old_goal = dict(ga, success={"event": "app_open", "condition": "later IST day"}, rule_version="v1")
        self.assertEqual(judge_goal(old_goal, later_open, w.pack["titles"], "2026-10-20T00:00:00.000Z")["result"], "succeeded")
        self.assertEqual(judge_goal(ga, later_open, w.pack["titles"], "2026-10-20T00:00:00.000Z")["result"], "failed")

    def test_goal_that_ends_after_the_data_is_inconclusive(self):
        goal = {"id": "G05", "started_at": "2026-10-14T00:00:00.000Z", "deadline": "2026-10-21T00:00:00.000Z",
                "success": {"event": "purchase_completed"}, "context": {}}
        self.assertEqual(judge_goal(goal, [], {}, "2026-10-15T00:00:00.000Z")["result"], "inconclusive")


if __name__ == "__main__":
    unittest.main()
