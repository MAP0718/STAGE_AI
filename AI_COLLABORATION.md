# AI collaboration log

## Tools
Claude (chat) did the planning help and wrote the code. I set the direction, made the design decisions
listed below, ran everything on my own laptop, and questioned the parts I did not understand until I could explain them.

## How Claude helped
* Explained the brief, the data pack and the traps in plain language, then broke the work into small steps.
* Explored the pack with scripts (duplicates, two-row profiles, clock errors, injected descriptions) and wrote
  the first version of every module: loader, fact sheet, goal picker, action picker, verifier, agent, replay,
  outcome judge, impact report, and the tests.
* Wrote the architecture, limitations and this log from the finished code.

## Decisions that were mine
* **Dialect precedence:** the app setting wins over the profile. My reasoning was that the app is where the viewer
  actually sees the home page; Claude added that the app requests its page by its own setting, so a page built
  for the profile dialect would simply not be shown.
* **Goal priority:** G02 (first full episode) comes before G05 (first paid plan). My reasoning: a viewer who has
  never finished an episode is not ready to pay, so first get them to watch, then convert.
* **Keep it small and explainable:** I asked for simple, plain code I can defend over a clever design.

## How the output was checked
* `python -m unittest discover -s tests -v` on my laptop: 18 tests, all OK.
* `python replay.py` on my laptop: 2,769 triggers replayed, simulator reports 0 policy problems, run again with a
  different seed.
* Claude compared the loader's counts with separate scripts (2,176 duplicate events, 30 duplicate profile rows,
  2 titles with injected instructions) and checked a clean copy of the folders runs with one command.
* I read each module's header and walked one viewer (U-02344) through the decision file.

## What was wrong or changed along the way
1. **Buffered app opens.** The first count used received_at minus occurred_at. Phone clocks are wrong, so that is
   unreliable. The code uses sent_at minus occurred_at: both are on the same phone clock, so the error cancels.
2. **Quiet hours.** Claude first described a night push as "blocked". The guide says to schedule it instead, so the
   code moves the send time to 08:00 IST and a test covers it.
3. **SQLite.** Claude suggested "Python + SQLite" early on. The build uses one JSON file per viewer instead. I
   noticed the mismatch and asked; we kept JSON because it meets the durable-state requirement, is easy to read
   in the interview, and was safer with the deadline. The architecture note says what I would use at scale.
4. **A wrong claim about a viewer.** Claude said U-02344 "never plays anything" after reading only their first 15
   events. The full history shows they started a series, so the rules pick goal G07 for them.
5. **A confusing quiz question.** Claude asked which goal would be wrong after a purchase; my answer showed I
   understood the idea, the wording was unclear. No code change.
6. **A test failure that was the test's fault.** One test added an event after the World had copied its events.
   The code was right; the test was fixed.
7. **No real language model.** The model is a mock advisor behind an interface; every decision is made by rules
   and checked by the verifier. I asked this directly and decided not to add a hosted model before the deadline,
   partly because the brief limits where the pack may be sent.
8. **Holdout path.** Holdout viewers run the same goal and model path as agent viewers and only skip publishing,
   so the two arms stay comparable.
9. **Choices informed by the simulator.** Claude read the simulator source. The 10-minute stale limit, extra genre
   rows and premium-first ordering for paid goals came from that. They are reasonable product ideas, but they
   are not proven on real viewers, and KNOWN_LIMITATIONS.md says so.

## What I would not accept from an AI without checking
Any goal or eligibility claim without an event behind it, any text from a catalogue description or a model reply
used as evidence, and any number in a report that I could not trace back to a file in `sample_run/`.