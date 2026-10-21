# Impact report

Replay day 2026-10-01 IST. Each viewer is counted once, by the FIRST goal the agent committed to for them (holdout viewers get the same goal rule as a *shadow goal* but no action). Rates use conclusive outcomes (succeeded + failed); inconclusive goals are listed separately.

## 1. Main result: agent arm vs holdout arm (intention-to-treat)

| Arm | Viewers with a goal | Succeeded | Failed | Inconclusive | Success rate |
|---|---|---|---|---|---|
| Agent | 758 | 90 | 668 | 0 | 11.9% |
| Holdout | 746 | 87 | 658 | 1 | 11.7% |

**Uplift: +0.2 pts** (95% CI -3.1 pts to +3.5 pts, p = 0.906). The confidence interval includes 0, so we cannot tell this apart from no effect.

Sensitivity (inconclusive counted as not succeeded): agent 11.9% vs holdout 11.7%.

Both arms contain viewers the agent chose not to act on (no valid content, stale trigger, no consent for messages), so this number is the effect of *running the agent*, not of one action.

## 2. By goal (small groups: read with care)

| Goal | Agent n | Agent rate | Holdout n | Holdout rate | Difference |
|---|---|---|---|---|---|
| G01 | 59 | 37.3% | 51 | 29.4% | +7.9 pts |
| G02 | 111 | 17.1% | 95 | 9.5% | +7.6 pts |
| G03 | 1 | 0.0% | 0 | n/a | n/a |
| G04 | 1 | 0.0% | 1 | 0.0% | +0.0 pts |
| G05 | 35 | 5.7% | 42 | 7.1% | -1.4 pts |
| G06 | 78 | 9.0% | 92 | 10.9% | -1.9 pts |
| G07 | 398 | 2.3% | 376 | 4.3% | -2.0 pts |
| G08 | 30 | 33.3% | 37 | 24.3% | +9.0 pts |
| G09 | 11 | 27.3% | 7 | 28.6% | -1.3 pts |
| G12 | 34 | 52.9% | 44 | 52.3% | +0.7 pts |

## 3. The comparison that misleads: served vs everyone else

| Group | Viewers | Success rate |
|---|---|---|
| Served by the agent (got at least one action) | 710 | 12.5% |
| Everyone else (agent arm not served + holdout) | 794 | 11.1% |

Naive 'uplift': **+1.4 pts**. The honest figure is +0.2 pts.

Look inside the agent arm: served viewers succeed at 12.5%, unserved ones at 2.1%. That gap of +10.5 pts cannot be an effect of the agent on the unserved (they got nothing); it only shows that the agent serves a different, more active kind of viewer. The randomised comparison in section 1 does not have this problem.

## 4. The earlier pilot report, recomputed

| Comparison | Group A | Group B | Difference |
|---|---|---|---|
| Pilot report (served vs everyone else) | 40.1% (n=217) | 17.7% (n=3674) | +22.4 pts |
| Randomised arms (treatment vs holdout, everyone) | 18.3% (n=1912) | 19.6% (n=1979) | -1.3 pts (95% CI -3.8 pts to +1.2 pts) |

In the pilot, treatment viewers who were NOT served succeeded at 15.5% (n=1695), against 19.6% for the holdout: the 'served' group was selected, so the headline 2.3x is mostly selection, not effect.

## 5. Policy problems found by the simulator

Policy problems reported for our actions: **0** (consent, quiet hours, frequency cap, holdout). Home pages accepted: 914, rows dropped below minimum: 0.

## Honest caveats

* One replay day and one simulator seed: expect noise. Evaluators will rerun with another seed.
* The simulator is a model of viewers, not real viewers. It shows the pipeline is measured correctly; it does not prove real-world lift.
* Goals have different windows (1 to 14 days) and some are still open at the end of the data (inconclusive).
