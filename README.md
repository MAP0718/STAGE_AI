# One User, One Agent

A per-viewer agent for an OTT platform. For every app open it assembles what is known about the viewer, chooses **one
measurable goal**, decides **what to change (home page, one push, or nothing)**, has an independent verifier check the
decision, publishes inside the time budget, later judges the goal from attributable events, and measures its impact
against the randomised holdout.

The core is deterministic and rule-based. A mock model acts only as an advisor, and its answers are never treated as
evidence. No API key is needed.

## Requirements

* Python 3.9 or newer. Standard library only, so there is nothing to `pip install`.
* The data pack folder (`data/`, `config/`, `replay/`, `simulator/`, `pilot/`). It is personal data and is **not** part of this repository.

A virtual environment is optional:

```
# Windows (PowerShell)                       # macOS / Linux
python -m venv .venv                         python3 -m venv .venv
.venv\Scripts\Activate.ps1                   source .venv/bin/activate
```

## Quick start

Expected layout (the pack sits next to this folder):

```
stage_ai/
  pack/                 data pack (not submitted)
  submission/           this repository
```

Run the whole pipeline with one command, from `submission/src`:

```
python replay.py
```

It replays all triggers, runs the simulator, judges outcomes and writes everything to `sample_run/`.
Useful options: `--pack <folder>` (pack location), `--out <folder>`, `--seed <text>` (simulator seed),
`--limit 300` (quick test on 300 triggers), `--no-sim` (skip simulator and impact report).
Each run starts from clean viewer state.

Run the tests from `submission`:

```
python -m unittest discover -s tests -v
```

A successful run ends with `simulator policy problems: 0` and `done`; the tests end with `Ran 18 tests` and `OK`.

## Repository layout

```
README.md  ARCHITECTURE.md  AI_COLLABORATION.md  KNOWN_LIMITATIONS.md
src/        application code
tests/      automated tests
sample_run/ output of one full replay
```

## Source files (`src/`)

* `replay.py` is the entry point. It replays triggers, live events and control events in delivery order, then runs the simulator, the outcome judge and the reports.
* `agent.py` holds one `ViewerAgent` per viewer plus the shared `World`, state store and call budgets. It runs the decision flow and handles purchases, withdrawn titles and corrected goal rules.
* `loader.py` reads the pack and fixes duplicate events, two-row profiles and catalogue entries that contain instructions. It orders events by server time (`received_at`), not by the phone clock.
* `fact_sheet.py` builds each viewer's facts with source, time and freshness, and keeps conflicts visible. It applies the precedence rules (app setting, newer events over the stale profile, opt-outs over consent).
* `goal_picker.py` decides which goals a viewer is eligible for, using evidence only, and creates the goal with its deadline and success signal. It never chooses the two goals that cannot be measured.
* `action_picker.py` builds the home page rows and the push text from titles that are valid for the viewer's dialect. It skips unavailable, withdrawn and quarantined titles and one-title-per-show duplicates.
* `verifier.py` independently re-checks every decision from raw data and can block it. It covers eligibility, dialect, content rules, consent, quiet hours, the daily cap, copy rules, holdout and duplicate actions.
* `model_provider.py` is the mock model (default) with the injected failures: timeout, broken JSON, unsupported claim and outage. It is the single place to plug in a hosted model.
* `outcome_judge.py` judges every goal as succeeded, failed or inconclusive using only events inside the goal window. It records what was learned for each viewer.
* `impact_report.py` compares all agent-arm viewers with all holdout viewers and explains why "served vs everyone else" misleads. It also recomputes the earlier pilot report.
* `config.py` holds the settings in one place: model and tool call budgets, latency budget and timeouts. Change a value here to change the behaviour.
* `timeutil.py` has the time helpers for UTC and IST. It implements quiet hours (22:00 to 08:00 IST) and moving a send time to the next 08:00.

## Tests (`tests/`)

* `test_agent.py` has 18 tests on a small made-up pack, so no data files are needed. They cover duplicate and late triggers, conflicting sources, model and store failures, and a corrected goal rule, plus consent, quiet hours, the daily cap, holdout, quarantined titles and purchase re-planning.

## Output (`sample_run/`)

* `decisions.jsonl` has one record per trigger: goal, action or reason for no action, evidence, confidence with a reason, assumptions, check results and timing.
* `actions.jsonl` lists the published home pages and pushes. This is the file the simulator scores.
* `viewer_state/` holds one JSON file per viewer: facts with provenance, goal history, decisions, actions, outcomes and what was learned.
* `outcomes.jsonl` has the judgment of every goal. Each record shows the result, the signal event, the rule version and whether the viewer was exposed to an action.
* `latency_report.json` has p50, p95, p99 and max decision time, budget checks, fallbacks used and the busiest minutes.
* `impact_report.md` is the agent versus holdout comparison with a confidence interval, the misleading comparison, and the pilot re-analysis.
* `final_report.md` summarises the run: decisions by type, data issues found and how each was handled, and how each replay surprise was handled.
* `incidents.jsonl` records purchase re-plans, the withdrawn title, the corrected goal rule, outage windows and cancelled actions.

## How a decision is made

1. Ignore exact duplicate triggers and repeat visits in the same session; skip stale offline-buffered opens.
2. Build facts with source and freshness; resolve conflicts with written rules and keep them visible.
3. Find eligible goals from evidence; the model may advise, but the pick is accepted only if the rules agree.
4. Holdout viewers get no action (their goal is recorded to measure impact); others get a home page and, for some goals, one push.
5. The verifier re-checks everything and can drop the push, the page or the whole decision.
6. Publish with `published_at = deliver_at + decided_in_ms`, falling back to a smaller action when a dependency fails.

## Results of the included run

* 2,769 triggers replayed; 902 published decisions, the rest recorded as no action with a reason.
* All decisions published well inside the 60 s budget (the maximum includes a simulated 3 s model timeout).
* The simulator reports 0 policy problems (consent, quiet hours, frequency cap, holdout).
* Agent arm 11.9% vs holdout 11.7%: uplift +0.2 points (95% CI -3.1 to +3.5), i.e. no detectable effect on this replay.

## Further reading

* `ARCHITECTURE.md`: ownership, planning, memory, verification, latency, failure recovery, trust boundaries.
* `AI_COLLABORATION.md`: how AI tools were used and how their output was checked, corrected or rejected.
* `KNOWN_LIMITATIONS.md`: what was left out on purpose and the assumptions made.

## Data handling

The pack is treated as personal data. It is never copied into this repository, and nothing is sent to external
services (the default mock model runs locally). Keep this repository private and share it only with the reviewers.