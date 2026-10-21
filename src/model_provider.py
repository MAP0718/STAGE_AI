
import json
from config import MODEL_TIMEOUT_MS


class ModelUnavailable(Exception):
    """The provider is down (HTTP 503)."""


class ModelTimeout(Exception):
    """We stopped waiting. simulated_ms is how long we waited."""
    simulated_ms = MODEL_TIMEOUT_MS


class MockModelProvider:
    def __init__(self, faults=None, outages=None):
        self.faults = faults or {}        # trigger_event_id -> fault record
        self.outages = outages or []      # [(start_iso, until_iso)]
        self.calls = 0

    def down_at(self, now):
        return any(start <= now < until for start, until in self.outages)

    def choose_goal(self, trigger_event_id, now, candidate_goal_ids):
        """Return the model's raw answer (a dict, or a string if the 'model' returned broken text)."""
        self.calls += 1
        if self.down_at(now):
            raise ModelUnavailable("HTTP 503")
        fault = self.faults.get(trigger_event_id)
        if fault:
            if fault["fault"] == "timeout":
                raise ModelTimeout("no answer within %d ms" % MODEL_TIMEOUT_MS)
            return fault["model_output"]          # unsupported_claim (dict) or malformed_output (str)
        if not candidate_goal_ids:
            return {"goal_id": None, "eligible": False, "confidence": 0.5, "reason": "no eligible goal"}
        return {"goal_id": candidate_goal_ids[0], "eligible": True, "confidence": 0.7,
                "reason": "highest priority goal among those the rules found eligible"}


def parse_model_answer(raw):
    """Return (dict_or_None, problem_or_None). Never raises."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, "malformed_output"
    if not isinstance(raw, dict) or "goal_id" not in raw:
        return None, "malformed_output"
    return raw, None
