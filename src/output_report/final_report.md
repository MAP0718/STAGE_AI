# Final report

## What ran

2769 triggers replayed (90 duplicates ignored). Published decisions: 902. Everything else is a recorded 'no action' with a reason.

| Published action | Count |
|---|---|
| HOME_PAGE+PUSH | 469 |
| HOME_PAGE | 433 |

| Reason for no action | Count |
|---|---|
| holdout_arm | 997 |
| stale_trigger | 424 |
| duplicate_session | 198 |
| page_already_live | 91 |
| no_valid_content | 49 |
| dialect_unknown | 18 |

| Goal on published decisions | Count |
|---|---|
| G07 | 493 |
| G02 | 134 |
| G06 | 88 |
| G01 | 59 |
| G08 | 40 |
| G05 | 39 |
| G12 | 31 |
| G09 | 16 |
| G03 | 1 |
| G04 | 1 |

## Latency

p50 0 ms, p95 2 ms, p99 3 ms, max 3001 ms (budget 60,000 ms). Over 60 s: 0. Over 5 min: 0. Busiest minute: 79 triggers; busiest 10 minutes: 240 triggers.

## Data issues found and how they were handled

| Issue | Found | Handling |
|---|---|---|
| Duplicate events (same event_id) | 2176 in events.jsonl, 90 duplicate triggers | first one kept; repeat triggers recorded as DUPLICATE_IGNORED, no second action |
| Users with two profile rows | 30 | latest updated_at wins |
| Profile is a day stale / missing for new viewers | 50 viewers with events but no row, plus new signups on replay day | newer events override the profile; no row = facts from events, marked as assumptions, no consent assumed |
| Wrong phone clocks | many events | ordering and freshness use received_at only; trigger age = sent_at - occurred_at (both on the phone clock, so the error cancels) |
| Offline-buffered app opens | 424 triggers older than 10 min | treated as stale: no action, recorded |
| Same visit delivered as several triggers | 198 duplicate sessions | one decision per session |
| Dialect conflicts | profile / app setting / campaign link | app setting used (it decides what the viewer sees); others kept in `conflicts` |
| Titles with several title_ids | 514 content_slugs | one title per content_slug per page |
| Instructions hidden in descriptions | T0002, T0522 | titles quarantined, text dropped, never obeyed, never shown |
| Goals G10, G11 cannot be measured | G10: no success event; G11: tv_app_open does not exist | never chosen |
| Unclear goal rules | G06 'recently', G12/G02 history | assumptions written in goal_picker.py |
| Pilot report compares served vs everyone else | see impact_report.md | recomputed with the randomised arms |

## Surprises on the replay day

| Surprise | What happened |
|---|---|
| Offline-buffered app opens | 424 stale triggers skipped |
| Purchase after the decision | 13 viewers re-planned; goal closed, pending messages cancelled (3) |
| Unsupported model claim | 1 rejected (`unsupported_model_claim`); goal came from the rules |
| Broken model JSON | 1 handled with rule fallback |
| Model timeout / outage | fallbacks: {"unsupported_model_claim": 1, "model_unavailable (HTTP 503)": 17, "model_timeout": 1, "model_malformed_output": 1, "data_store_timeout": 1} |
| Title withdrawn | T0001: 0 pending messages cancelled, 12 live pages republished |
| Goal rule corrected | G03: 0 open goals updated, nobody else reprocessed |
| Data store degraded | cached-state fallbacks: 1 (these decisions send no messages) |
| Injected instruction in a description | quarantined, see above |

## Impact

Agent arm 11.9% vs holdout 11.7%: uplift +0.2 pts (95% CI -3.1 pts to +3.5 pts). Details and the comparison that misleads are in impact_report.md.

Simulator policy problems on our actions: 0.

See KNOWN_LIMITATIONS.md for what was left out on purpose.

