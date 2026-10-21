# Known limitations (left out on purpose)

* **Mock model only.** The provider interface (`choose_goal`) is the seam for a hosted model, but no hosted model
  is wired in. The mock always returns the first rule-eligible goal, so it adds no intelligence.
* **No WhatsApp or in-app messages.** Only home page and push. WhatsApp templates and in-app messages were skipped
  to keep the loop small and safe.
* **Sequential processing.** The traffic spike is handled by being fast (busiest 10 minutes = 240 triggers), not by
  concurrency. A real system needs workers and a queue; failure windows are simulated by time and a hash, not by real I/O.
* **Assumptions about unclear rules.** G06 'recently lapsed': any lapsed status counts (no lapse date exists).
  G02 'never completed': only the 30 days of events. G12: counted from received_at. Subscription becomes 'unknown'
  if a plan end date has passed with no renewal event.
* **Conservative consent.** Any opt-out event, at any time, blocks that channel; no profile row means no consent.
* **Goal correction is generic only for the success event.** The corrected G03 rule swaps the success event; the extra
  'later calendar day' condition is hard-coded for G03. In the sample run the correction touched 0 viewers because G03
  needs someone who signed up in the last 2 days AND already played (otherwise G01 wins), which is rare; the unit test
  covers the case where it applies.
* **Design choices informed by reading the simulator** (10-minute stale limit, more genre rows, premium titles
  first for paid goals). They are reasonable product logic but their real-world value is not proven.
* **One replay day, one seed.** Uplift is about zero with a wide confidence interval; we report it as is.
* **State is rebuilt each run.** viewer_state files are written durably but a run starts from clean state (no resume).
* **Parts of the pilot analysis** use only the columns in pilot_outcomes.csv; no covariate adjustment.
