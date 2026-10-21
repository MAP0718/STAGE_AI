
import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from datetime import timedelta

import action_picker
import config
import goal_picker
import verifier
from fact_sheet import build_facts
from model_provider import ModelTimeout, ModelUnavailable, parse_model_answer
from timeutil import iso, next_allowed, parse

PUSH_GOALS = {"G01", "G04", "G06", "G07", "G12"}   # goals where a message adds value (others: page only)


def hash01(text):
    """A repeatable pseudo-random number in [0, 1) so runs give the same result."""
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) / 16 ** 8


class BudgetExceeded(Exception):
    pass


class Budget:
    """Counts model calls and tool (data-read) calls for ONE decision."""

    def __init__(self):
        self.model_calls = 0
        self.tool_calls = 0

    def model(self):
        if self.model_calls >= config.MAX_MODEL_CALLS:
            raise BudgetExceeded("model call budget")
        self.model_calls += 1

    def tool(self):
        if self.tool_calls >= config.MAX_TOOL_CALLS:
            raise BudgetExceeded("tool call budget")
        self.tool_calls += 1


class StateStore:
    """Durable per-viewer state: one JSON file per viewer (kept in memory too)."""

    def __init__(self, directory=None):
        self.dir = directory
        self.states = {}
        if directory:
            os.makedirs(directory, exist_ok=True)

    def get(self, uid):
        if uid not in self.states:
            self.states[uid] = {
                "viewer_id": uid, "arm": None, "facts": {}, "conflicts": [], "goal": None,
                "goal_history": [], "decisions": [], "actions": [], "cancelled_actions": [],
                "outcomes": [], "learned": [],
            }
        return self.states[uid]

    def save(self, uid):
        if self.dir:
            with open(os.path.join(self.dir, uid + ".json"), "w", encoding="utf-8") as f:
                json.dump(self.states[uid], f, indent=1, ensure_ascii=False)


class World:
    """Everything the agents share."""

    def __init__(self, pack, model, store, model_outages=None, store_degraded=None):
        self.pack = pack
        self.model = model
        self.store = store
        self.goals_by_id = {g["goal_id"]: g for g in pack["goals"]}
        # live copy of each viewer's events (the pack data + events that arrive during the replay)
        self.events = {u: list(evs) for u, evs in pack["events"].items()}
        self.store_degraded = store_degraded or []     # [(start, until)]
        self.model_outages = model_outages or []
        self.withdrawn = {}            # title_id -> time it was withdrawn
        self.overrides = {}            # goal_id -> corrected success rule
        self.decisions = []
        self.actions = []              # every published action still valid (cancelled ones are removed)
        self.cancelled = []            # actions we cancelled, with the reason
        self.incidents = []            # what happened on control events and who was affected
        self.seen_triggers = set()
        self.seen_keys = set()
        self.sessions = defaultdict(set)
        self.scheduled = defaultdict(list)   # viewer -> send_at of our pending messages
        self.live_pages = {}           # viewer -> last home page action
        self.open_goals = defaultdict(set)   # goal_id -> viewers with that goal open
        self.counters = Counter()
        self._d = 0
        self._a = 0
        self._agents = {}

    def agent(self, uid):
        if uid not in self._agents:
            self._agents[uid] = ViewerAgent(uid, self)
        return self._agents[uid]

    def next_decision_id(self):
        self._d += 1
        return "D-%04d" % self._d

    def next_action_id(self):
        self._a += 1
        return "A-%04d" % self._a

    def store_down_at(self, now):
        return any(s <= now < u for s, u in self.store_degraded)

    # ---------- control events ----------
    def title_withdrawn(self, c):
        tid, at = c["title_id"], c["at"]
        self.withdrawn[tid] = at
        cancelled_pushes, fixed_pages, unfixable = [], [], []
        for act in list(self.actions):
            if act["type"] == "push" and act["title_id"] == tid and act["send_at"] >= at:
                self.cancel(act, "title %s withdrawn" % tid)
                cancelled_pushes.append(act["action_id"])
            elif act["type"] == "home_page" and self.live_pages.get(act["user_id"]) is act:
                still_live = parse(at) < parse(act["published_at"]) + timedelta(minutes=config.HOME_PAGE_TTL_MIN)
                if still_live and tid in action_picker.page_title_ids(act):
                    page = action_picker.strip_title(act, tid)
                    if not page["rows"]:
                        unfixable.append(act["user_id"])
                        continue
                    new = self.new_page_action(act["decision_id"], act["user_id"], act["trigger_event_id"],
                                               at, page)
                    new["republished_because"] = "title %s withdrawn" % tid
                    self.actions.append(new)
                    self.live_pages[act["user_id"]] = new
                    self.store.get(act["user_id"])["actions"].append(new["action_id"])
                    self.store.get(act["user_id"])["learned"].append(
                        "title %s was withdrawn at %s; live home page republished without it" % (tid, at))
                    self.store.save(act["user_id"])
                    fixed_pages.append(act["user_id"])
        self.incidents.append({"at": at, "type": "title_withdrawn", "title_id": tid,
                               "pending_messages_cancelled": cancelled_pushes,
                               "live_pages_republished_for": fixed_pages, "pages_not_fixable": unfixable})

    def goal_rule_corrected(self, c):
        gid, at = c["goal_id"], c["at"]
        self.overrides[gid] = {"new_success": c["new_success"], "at": at}
        touched = []
        for uid in sorted(self.open_goals[gid]):        # only viewers whose goal is open: no full reprocessing
            st = self.store.get(uid)
            goal = st["goal"]
            if goal and goal["id"] == gid and goal["status"] == "open" and goal["deadline"] >= at:
                goal["success"] = c["new_success"]
                goal["rule_version"] = "v2-corrected"
                st["learned"].append("success rule of %s corrected at %s; goal now judged on %s"
                                     % (gid, at, c["new_success"]["event"]))
                self.store.save(uid)
                touched.append(uid)
        self.incidents.append({"at": at, "type": "goal_rule_corrected", "goal_id": gid,
                               "new_success": c["new_success"], "open_goals_updated": len(touched),
                               "viewers": touched})

    # ---------- helpers ----------
    def cancel(self, act, reason):
        if act in self.actions:
            self.actions.remove(act)
        if act["type"] == "push" and act["send_at"] in self.scheduled[act["user_id"]]:
            self.scheduled[act["user_id"]].remove(act["send_at"])
        self.cancelled.append({"action_id": act["action_id"], "viewer_id": act["user_id"], "reason": reason})
        st = self.store.get(act["user_id"])
        st["cancelled_actions"].append({"action_id": act["action_id"], "reason": reason})
        self.store.save(act["user_id"])

    def new_page_action(self, decision_id, uid, trigger_id, published_at, page):
        act = {"action_id": self.next_action_id(), "decision_id": decision_id, "user_id": uid,
               "trigger_event_id": trigger_id, "type": "home_page", "published_at": published_at,
               "dialect": page["dialect"], "rows": page["rows"]}
        if page.get("hero_title_id"):
            act["hero_title_id"] = page["hero_title_id"]
        return act


class ViewerAgent:
    def __init__(self, uid, world):
        self.uid = uid
        self.w = world

    @property
    def state(self):
        return self.w.store.get(self.uid)

    # ---------- live events (purchase etc.) ----------
    def on_live_event(self, ev):
        w, st = self.w, self.state
        e = dict(ev)
        e["_live"] = True
        w.events.setdefault(self.uid, []).append(e)
        w.events[self.uid].sort(key=lambda x: (x["received_at"], x["event_id"]))
        goal = st["goal"]
        if ev["event"] == "purchase_completed" and goal and goal["status"] == "open" \
                and goal["id"] in goal_picker.PAID_GOALS:
            goal["status"] = "closed_by_purchase"
            goal["closed_at"] = ev["received_at"]
            st["goal_history"].append(goal)
            st["goal"] = None
            w.open_goals[goal["id"]].discard(self.uid)
            cancelled = 0
            for act in list(w.actions):
                if act["user_id"] == self.uid and act["type"] == "push" and act["send_at"] > ev["received_at"]:
                    w.cancel(act, "purchase arrived; goal %s no longer makes sense" % goal["id"])
                    cancelled += 1
            st["learned"].append("purchase at %s closed goal %s; %d pending message(s) cancelled; will re-plan"
                                 % (ev["received_at"], goal["id"], cancelled))
            w.incidents.append({"at": ev["received_at"], "type": "purchase_replan", "viewer_id": self.uid,
                                "closed_goal": goal["id"], "messages_cancelled": cancelled})
        w.store.save(self.uid)

    # ---------- the decision ----------
    def on_trigger(self, trig):
        w, st, uid = self.w, self.state, self.uid
        t0 = time.perf_counter()
        sim_ms = 0                       # waiting time we simulate (timeouts) without really sleeping
        now = trig["deliver_at"]
        budget = Budget()
        fallbacks, assumptions = [], []

        decision = {
            "decision_id": w.next_decision_id(), "viewer_id": uid,
            "trigger": {"event": trig["event"], "event_id": trig["event_id"], "received_at": trig["received_at"],
                        "deliver_at": now,
                        "event_age_s": round((parse(trig["sent_at"]) - parse(trig["occurred_at"])).total_seconds())},
            "arm": w.pack["arms"].get(uid, "holdout"),   # unknown arm = treat as holdout (safe)
            "goal": None, "action": "NONE", "dialect": None, "content": [], "confidence": None,
            "confidence_reason": "", "evidence": [], "assumptions": [], "conflicts": [],
            "checks": {"passed": [], "failed": []}, "no_action_reason": None, "fallback_used": [],
            "model_calls": 0, "tool_calls": 0, "judged_by": None, "decided_in_ms": None,
            "published_at": None, "status": "NO_ACTION",
        }

        def finish(reason, confidence, why, status="NO_ACTION"):
            decision["no_action_reason"] = reason if status == "NO_ACTION" else None
            decision["confidence"], decision["confidence_reason"] = confidence, why
            decision["status"] = status
            return self._close(decision, trig, t0, sim_ms, budget, fallbacks, assumptions)

        # 1. idempotency: the same trigger twice never creates a second decision or action
        if trig["event_id"] in w.seen_triggers:
            w.counters["duplicate_trigger_ignored"] += 1
            decision["no_action_reason"] = "duplicate_trigger (event_id already handled)"
            decision["status"] = "DUPLICATE_IGNORED"
            decision["confidence"], decision["confidence_reason"] = 1.0, "exact repeat of a handled trigger"
            return self._close(decision, trig, t0, sim_ms, budget, fallbacks, assumptions, record_event=False)
        w.seen_triggers.add(trig["event_id"])
        st["arm"] = decision["arm"]

        # 2. stale / duplicate session: an old offline-buffered open is not a live visit
        if decision["trigger"]["event_age_s"] > config.STALE_TRIGGER_S:
            w.counters["stale_trigger"] += 1
            return finish("stale_trigger: app open is %d s old (buffered offline), not a live visit"
                          % decision["trigger"]["event_age_s"], 0.95,
                          "age = sent_at - occurred_at (same phone clock, so clock error cancels out)")
        if trig["session_id"] in w.sessions[uid]:
            w.counters["duplicate_session"] += 1
            return finish("duplicate_session: this visit was already handled", 0.95, "same session_id seen before")
        w.sessions[uid].add(trig["session_id"])

        # 3. read state (the data store can be slow or down)
        fresh = True
        try:
            budget.tool()
            budget.tool()
        except BudgetExceeded:
            fresh = False
        if w.store_down_at(now):
            u = hash01(trig["event_id"] + "store")
            if u < 0.20:
                sim_ms += config.STORE_TIMEOUT_MS
                fresh = False
                fallbacks.append("data_store_timeout: used cached state, no messages")
            elif u < 0.25:
                sim_ms += 8000
            else:
                sim_ms += 400
        all_events = w.events.get(uid, [])
        view = [e for e in all_events if e["received_at"] < now and (fresh or not e.get("_live"))]
        profile = w.pack["profiles"].get(uid)

        # 4. facts
        sheet = build_facts(uid, profile, view, trig, now)
        facts = sheet["facts"]
        st["facts"], st["conflicts"] = facts, sheet["conflicts"]
        assumptions += sheet["assumptions"]
        decision["dialect"] = {"used": facts["dialect"]["value"], "source": facts["dialect"]["source"],
                               "conflicts": sheet["conflicts"]}
        decision["evidence"] = list(sheet["evidence"])
        decision["conflicts"] = sheet["conflicts"]
        if not fresh:
            assumptions.append("state may be missing events from the last minutes (store degraded)")

        # 5. goal: keep the single active goal unless the situation changed
        eligible = goal_picker.eligible_goals(facts, view, w.pack["titles"], w.pack["episodes"], now)
        cand_ids = [g["goal_id"] for g in eligible]
        goal = st["goal"]
        if goal and goal["status"] == "open":
            if now >= goal["deadline"]:
                self._retire(goal, "deadline_passed")
                goal = None
            elif goal["id"] not in cand_ids:
                self._retire(goal, "no_longer_eligible")
                goal = None
        chosen = None
        if goal is None:
            # same path for both arms (only publishing differs), so the comparison stays fair
            chosen = self._ask_model(trig, now, cand_ids, budget, fallbacks, decision)
            sim_ms += self._model_wait
            if chosen is None and cand_ids:
                chosen = cand_ids[0]                      # rule priority order
        if goal is None and chosen:
            ctx = next(g["context"] for g in eligible if g["goal_id"] == chosen)
            goal = goal_picker.make_goal(chosen, w.goals_by_id, w.overrides, now, ctx)
            goal["shadow"] = decision["arm"] == "holdout"
            goal["evidence"] = next(g["evidence"] for g in eligible if g["goal_id"] == chosen)
            st["goal"] = goal
            w.open_goals[chosen].add(uid)
        if goal:
            decision["goal"] = {"id": goal["id"], "deadline": goal["deadline"], "started_at": goal["started_at"],
                                "rule_version": goal["rule_version"], "shadow": goal.get("shadow", False)}
            decision["judged_by"] = {"deadline": goal["deadline"], "signal": goal["success"]}
            decision["evidence"] += ["goal_rule:%s-%s" % (goal["id"], goal["rule_version"])] + goal.get("evidence", [])

        # 6. holdout viewers get nothing
        if decision["arm"] == "holdout":
            w.counters["holdout_no_action"] += 1
            return finish("holdout_arm: randomised control group receives no action" +
                          ("" if goal else " (and no eligible goal)"), 1.0,
                          "assignment comes from replay/arms.csv; shadow goal recorded only to measure impact")
        if goal is None:
            w.counters["no_eligible_goal"] += 1
            return finish("no_eligible_goal: not enough evidence for any measurable goal", 0.6,
                          "no goal rule is supported by the viewer's facts")
        dialect = facts["dialect"]["value"]
        if not dialect or facts["dialect"]["source"] == "profile":
            return finish("dialect_unknown: no current app setting, and a stale profile dialect may differ from "
                          "what the app will request", 0.9, "app setting decides what the viewer sees")

        # 7. action: avoid re-publishing while a page is still live (one active plan)
        last_page = w.live_pages.get(uid)
        if last_page and last_page["dialect"] == dialect and \
                parse(now) < parse(last_page["published_at"]) + timedelta(minutes=config.HOME_PAGE_TTL_MIN):
            w.counters["page_already_live"] += 1
            return finish("page_already_live: a personalised page for this goal is still live (120 min)", 0.9,
                          "avoids churn; the plan stays the same")
        budget.tool()
        page = action_picker.build_home_page(w.pack, view, dialect, facts["language"]["value"],
                                             goal["id"], now, w.withdrawn)
        push = None
        dialect_is_live = facts["dialect"]["source"] == "app_setting"   # push only if the app setting is current
        if page and goal["id"] in PUSH_GOALS and fresh and dialect_is_live and facts["push_consent"]["value"]:
            tid = self._push_title(goal, page, dialect, now)
            if tid:
                send_at = iso(next_allowed(parse(now) + timedelta(hours=config.PUSH_DELAY_HOURS)))
                push = {"send_at": send_at, "title_id": tid,
                        "text": action_picker.push_text(w.pack["titles"][tid]["title"]),
                        "deeplink": "ott://title/" + tid}
        keys = ["%s:%s:%s" % (uid, trig["event_id"], k) for k in ("home_page", "push")]
        plan = {"arm": decision["arm"], "goal_id": goal["id"], "home_page": page, "push": push, "keys": keys}

        # 8. independent verification
        env = {"profile": profile, "events": all_events, "trigger": trig, "now": now, "titles": w.pack["titles"],
               "quarantined": w.pack["quarantined"], "withdrawn": w.withdrawn, "seen_keys": w.seen_keys,
               "scheduled": w.scheduled[uid]}
        result = verifier.verify(plan, env)
        decision["checks"] = {"passed": sorted(set(result["passed"])), "failed": result["failed"]}
        if result["block_all"]:
            w.counters["blocked_by_verifier"] += 1
            if any(f["check"] == "goal_eligibility" for f in result["failed"]):
                self._retire(goal, "failed_verification")
            return finish("blocked_by_verifier: " + "; ".join(f["reason"] for f in result["failed"]), 0.9,
                          "the independent check disagreed with the planner")
        if result["drop_home_page"]:
            page = None
        if result["drop_push"]:
            push = None
        if page is None and push is None:
            w.counters["no_content"] += 1
            return finish("no_valid_content: fewer than 3 valid titles for any row, or checks failed", 0.8,
                          "a row with fewer than 3 valid titles is not shown")

        # 9. publish
        confidence, why = self._confidence(facts, sheet, fresh, profile)
        decision["confidence"], decision["confidence_reason"] = confidence, why
        elapsed_ms = (time.perf_counter() - t0) * 1000 + sim_ms
        published_at = iso(parse(now) + timedelta(milliseconds=elapsed_ms))
        acts = []
        if page:
            a = w.new_page_action(decision["decision_id"], uid, trig["event_id"], published_at, page)
            acts.append(a)
            w.live_pages[uid] = a
            decision["content"] += ["title:" + t for t in action_picker.page_title_ids(page)]
        if push:
            a = {"action_id": w.next_action_id(), "decision_id": decision["decision_id"], "user_id": uid,
                 "trigger_event_id": trig["event_id"], "type": "push", "published_at": published_at,
                 "send_at": push["send_at"], "title_id": push["title_id"], "text": push["text"],
                 "deeplink": push["deeplink"]}
            acts.append(a)
            w.scheduled[uid].append(push["send_at"])
            decision["content"].append("push:title:" + push["title_id"])
        for k in keys:
            if (k.endswith("home_page") and page) or (k.endswith("push") and push):
                w.seen_keys.add(k)
        w.actions.extend(acts)
        st["actions"] += [a["action_id"] for a in acts]
        decision["action"] = "+".join(("HOME_PAGE" if page else "", "PUSH" if push else "")).strip("+")
        decision["evidence"] += ["catalogue:" + t[6:] for t in decision["content"] if t.startswith("title:")][:12]
        decision["status"] = "PUBLISHED"
        w.counters["published"] += 1
        return self._close(decision, trig, t0, sim_ms, budget, fallbacks, assumptions)

    # ---------- small helpers ----------
    _model_wait = 0

    def _ask_model(self, trig, now, cand_ids, budget, fallbacks, decision):
        """Ask the (mock) model for a goal. Return a goal id that the RULES support, or None."""
        self._model_wait = 0
        try:
            budget.model()
            raw = self.w.model.choose_goal(trig["event_id"], now, cand_ids)
        except ModelTimeout as ex:
            self._model_wait = ex.simulated_ms
            fallbacks.append("model_timeout: used rule priority order")
            return None
        except ModelUnavailable:
            fallbacks.append("model_unavailable (HTTP 503): used rule priority order")
            return None
        except BudgetExceeded:
            fallbacks.append("model_budget_exceeded: used rule priority order")
            return None
        answer, problem = parse_model_answer(raw)
        if problem:
            fallbacks.append("model_malformed_output: used rule priority order")
            self.w.counters["model_malformed"] += 1
            return None
        claimed = answer.get("goal_id")
        if claimed is None:
            return None
        if claimed not in cand_ids:
            fallbacks.append("unsupported_model_claim: model said %s is eligible but no evidence supports it "
                             "(model reason ignored)" % claimed)
            self.w.counters["unsupported_model_claim"] += 1
            return None
        return claimed

    def _push_title(self, goal, page, dialect, now):
        w = self.w
        if goal["id"] == "G07":
            slug = goal["context"].get("series_slug")
            for t in w.pack["by_dialect"].get(dialect, []):
                if t["content_slug"] == slug and action_picker.title_usable(t, dialect, now, w.withdrawn,
                                                                           w.pack["quarantined"]):
                    return t["title_id"]
        return page.get("hero_title_id") or page["rows"][0]["title_ids"][0]

    def _retire(self, goal, reason):
        st = self.state
        goal["status"] = reason
        st["goal_history"].append(goal)
        if st["goal"] is goal:
            st["goal"] = None
        self.w.open_goals[goal["id"]].discard(self.uid)
        st["learned"].append("goal %s retired: %s" % (goal["id"], reason))

    def _confidence(self, facts, sheet, fresh, profile):
        score, parts = 0.5, ["base 0.50"]
        if facts["subscription"]["source"].startswith("event"):
            score += 0.15
            parts.append("+0.15 subscription confirmed by a recent event")
        if profile and (facts["subscription"]["age_hours"] or 0) < 36:
            score += 0.10
            parts.append("+0.10 profile is under 36 h old")
        if sheet["conflicts"]:
            score -= 0.15
            parts.append("-0.15 sources disagree (%s)" % ", ".join(sheet["conflicts"]))
        if not profile:
            score -= 0.20
            parts.append("-0.20 no profile row")
        if not fresh:
            score -= 0.15
            parts.append("-0.15 cached state")
        return round(min(0.95, max(0.05, score)), 2), "; ".join(parts)

    def _close(self, decision, trig, t0, sim_ms, budget, fallbacks, assumptions, record_event=True):
        """Finish a decision: timing, state, bookkeeping."""
        w, st = self.w, self.state
        decision["decided_in_ms"] = round((time.perf_counter() - t0) * 1000 + sim_ms)
        decision["model_calls"], decision["tool_calls"] = budget.model_calls, budget.tool_calls
        decision["fallback_used"] = fallbacks
        decision["assumptions"] = assumptions
        if decision["published_at"] is None:
            decision["published_at"] = iso(parse(trig["deliver_at"]) + timedelta(milliseconds=decision["decided_in_ms"]))
        w.decisions.append(decision)
        st["decisions"].append(decision["decision_id"])
        if record_event:                 # the trigger itself becomes part of the viewer's history
            e = dict(trig)
            e["_live"] = True
            w.events.setdefault(self.uid, []).append(e)
            w.events[self.uid].sort(key=lambda x: (x["received_at"], x["event_id"]))
        w.store.save(self.uid)
        return decision
