"""Settings in one place, so budgets are configurable (the assignment asks for this)."""

MAX_MODEL_CALLS = 8          # per decision
MAX_TOOL_CALLS = 20          # per decision (data reads)
LATENCY_BUDGET_MS = 60_000   # publish within 60 s of the trigger
STALE_TRIGGER_S = 600        # an app open that is older than 10 min is not a live visit
MODEL_TIMEOUT_MS = 3_000     # we stop waiting for the model after 3 s
STORE_TIMEOUT_MS = 2_000     # we stop waiting for a data-store read after 2 s
PUSH_DELAY_HOURS = 3         # do not push right after the user opened the app
HOME_PAGE_TTL_MIN = 120
MAX_ROW_TITLES = 8
