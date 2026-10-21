# Architecture

## Ownership
`ViewerAgent` (agent.py) is created once per viewer and is the only code that changes that viewer's goal, plan
and history. The viewer has at most one open goal and one live home page at a time. Shared things (data, published
actions, outage windows) live in `World`. Every action carries the decision that produced it.

## Planning
The goal is chosen when the viewer has none, and kept until one of these happens: the deadline passes, the viewer is
no longer eligible (e.g. played, bought), or a purchase arrives (live event) which closes any paid goal and cancels its
pending messages. So the plan is revised when the situation changes, not on a schedule. Goal rules are data
(`goals.json`) plus eligibility code in goal_picker.py, each rule needs evidence from events or the profile.
G10 and G11 are never chosen (no way to measure them).

## Memory
`StateStore` writes one JSON per viewer (`sample_run/viewer_state/`): facts with source, as_of and age, conflicts,
the open goal and goal history, decision ids, action ids, cancelled actions, outcomes, and what was learned.
Every decision in decisions.jsonl lists its evidence ids (`event:`, `profile:`, `catalogue:`, `goal_rule:`) and assumptions.

## Source handling (precedence per fact)
| Fact | Rule |
|---|---|
| dialect | app setting of the trigger (what the app will request); profile and campaign link only kept as conflicts; profile-only dialect means abstain |
| subscription | purchase / renewal / lapse event newer than the snapshot beats the profile; no row = derived from events (assumption) |
| consent | any opt-out event beats the profile; no profile = no consent |
| time | received_at (server). Trigger age = sent_at - occurred_at (both phone clock, so the offset cancels) |

## Verification
`verifier.py` does not import the planner's facts or helpers. It re-reads the raw profile, raw events and catalogue and
checks: holdout gets nothing, goal supported by evidence, dialect, home-page rules (3-10 titles per row, max 6 rows,
unique content_slug, availability, withdrawn, quarantined), consent, quiet hours, one message per IST day,
push copy and deeplink, duplicate action. It can drop the page, drop the push or block the whole decision.
Model text is never evidence: a model answer is accepted only if the rules already found that goal eligible.

## Latency and budgets
The core is deterministic and takes about a millisecond per decision. `config.py` holds the budgets: 8 model calls
and 20 tool calls per decision (counted by `Budget`), 60 s latency budget. Model and store timeouts are simulated
(not slept) but their waiting time is added to `decided_in_ms`, and `published_at = deliver_at + decided_in_ms`.

## Failure recovery
| Failure | Behaviour |
|---|---|
| model down / timeout / broken JSON / unsupported claim | fall back to rule priority order, recorded in `fallback_used` |
| data store timeout | cached state (no live events), page only, no message |
| duplicate trigger / same session | no second decision or action |
| stale (offline-buffered) open | no action, recorded |
| withdrawn title | excluded from then on; pending pushes cancelled; live pages republished without it |
| corrected goal rule | only viewers whose goal of that id is open are updated; nobody is reprocessed |
| no valid content / no goal / no consent | smaller action or recorded no-action |

## Trust boundaries
Trusted: profile and events (data), after cleaning. Untrusted: catalogue descriptions (quarantined if they look like
instructions, text dropped), model output (advisor only), search text and deeplinks (data only). The simulator and
replay files are used to run and score, not as viewer facts.

## Measurement
Holdout viewers go through the same decision path (goal, model call) but publish nothing, so their shadow goal is
judged with the same rule. Impact = all agent-arm viewers vs all holdout viewers (intention-to-treat), with a CI.

## At 10 million viewers and 40 dialects
Decisions are independent per viewer, so shard by viewer_id. Keep facts in a key-value store, load catalogue
per dialect from a cache, and call the model only when more than one goal is eligible. Not built here (see limitations).
