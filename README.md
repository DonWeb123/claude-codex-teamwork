# Claude + Codex Teamwork Kit

This kit lets **Claude Code** (Anthropic) and a **Codex agent** (OpenAI) work on the same project as a team. They send each
other short messages, each reviews the other's work, and each catches mistakes the other misses.

The kit wakes the receiver every time a message arrives, whether that agent is idle or busy. It also confirms each message
arrived. It was built and measured over two days of joint work between Claude (Opus) and a Codex agent named **Astra**
(GPT-6.2 Astra) on *Forge*, a local AI coding agent the two were testing and improving.

## Setup (no technical knowledge needed)

You need Claude Code and Codex (the Codex extension in VS Code, or the Codex app) on a Mac.

1. **Download** this repository: the green **Code** button, then **Download ZIP**. Unzip it somewhere you'll keep it,
   for example your Documents folder.
2. In Codex, **start a new chat** and type `hello`. This chat becomes Claude's partner.
3. **Open the unzipped folder in Claude Code** and type:
   > Set up this teamwork kit.
4. Claude will ask which Codex chat is the partner. It then runs the self-tests, installs the kit and sends the partner
   its first message. When the partner answers, the link is proven to work.
5. Restart Claude Code in that folder when Claude asks you to.

To remove the kit later, ask Claude to run `python3 uninstall.py`.

## How it works

```
 Claude Code                         shared mailbox (plain files)                    Codex partner
 ───────────                         ────────────────────────────                    ─────────────
 team.py send ───────────────────▶   team-data/msgs/M0042.json   ──▶ wakerd ──▶ codex queue --thread <chat>
                                                                    (sees it in ~0.2 s,   "[wake:ab12] 1 new message"
                                                                     proves delivery by
                                                                     finding the marker in
                                                                     the partner's chat log)
 idle_wake hook  ◀── wakes an idle Claude (exit 2) ─┐
 midturn hook    ◀── shows mail after every tool ───┤◀──────────────────────────── team.py send (partner)
 commhealth      ── measures both directions every 60 s from real traffic, raises COMM DEGRADED
```

| part | what it does |
|---|---|
| `team/team.py` | The mailbox: send, inbox, ack, open questions, claims. Built-in guards: a `?` must use a type that asks for a reply, answers must say which message they answer (`--re`), 220-word limit. |
| `team/wakerd.py` | Wakes the Codex partner for **every** message (one combined wake when several arrive together). It confirms delivery from the partner's session log and alerts when a wake does not start a turn, when Codex restarts, or when a reply is overdue. |
| `team/hooks/idle_wake.py` | Claude Code `Stop`/`SessionStart` hook (`asyncRewake`): wakes an idle Claude when mail arrives. |
| `team/hooks/midturn.py` | Claude Code `PostToolUse` hook: shows Claude new mail **while it is working**, after each tool call. |
| `team/commhealth.py` | A passive monitor. It measures p50/p90 latency each way from real messages, without test pings or extra usage, and raises an alert when mail sits unseen for more than 10 minutes or a part is down. |
| `AGENTS.md` | The team rules both agents follow. Codex reads this file automatically. |
| `tests/run_tests.py` | 29 self-tests in a temporary folder. They never call Codex and never touch your settings. |

## Why this method: what we tried and what we measured

We built the link in stages. Each stage was measured on real traffic between Claude and Astra, and each failure we found
led to the next stage.

| stage | how a message reached the other agent | what went wrong (measured) |
|---|---|---|
| 0. Human relay | The owner copy-pasted between the two apps. | Needed a person every time. |
| 1. Mailbox + manual wake (Nov 24) | Shared files. Claude woke Astra by hand with `codex queue` and watched for replies with a background `wait`. | The first proven idle wake took 8 s from queue to turn start. Later, a message sent without a wake left Astra idle for about 10 min. |
| 2. Wake daemon v2 (Nov 25, 18:39) | A daemon woke Astra only for messages that asked for a reply. Claude got an idle hook. | RESULT/FYI messages never woke Astra (5 min idle gap). A Codex app update changed the log format, so delivery tracking recorded **nothing for 23 wakes** without anyone noticing. After a Codex restart, wakes were accepted but no turn started (8 min lost). A busy Claude saw mail only at the end of its turn. |
| **3. This kit's design (v3, Nov 25, 21:43 to 22:28)** | Wake on **every** message, read both log formats, restart alert, mid-turn hook, health monitor, then a 0.2 s reaction time. | See below. |

**Before v3**, measured on 40+ real messages each way: Claude→Astra median 6.6 s, worst case 312 s. Astra→Claude
(until Claude acknowledged) median 207 s, p90 485 s, worst case 793 s.

**After v3:**
- Live proof: a RESULT message woke an idle Astra in **2.975 s**. Astra measured this and matched the marker to the turn.
- The first sample after rollout put p90 near **40 s each way**, down from 309 s and 473 s. It was only 4 messages, so treat it as a first reading.
- The wake daemon's reaction time went from about 7 s to **0.195 s**, from the moment a message was written to the moment the wake was sent. Astra acknowledged a message mid-turn after **12.96 s**.
- Over the next **~22 hours of real work** on the original Forge setup (not this cleaned-up kit), the monitor saw
  **143 of 143** Claude→Astra messages and **173 of 173** Astra→Claude messages, with **0 lost**. These counts include only
  messages due an immediate wake. A later change held back some informational notes on purpose, and those are left out:
  - Claude→Astra: median 8.3 s, p90 98.7 s.
  - Astra→Claude: median 9.6 s, p90 47.6 s.
- The Claude→Astra p90 is still above our 60 s target. A message that reaches a Codex chat in the middle of a long turn only
  lands when that turn ends, unless the partner checks its inbox between steps. That is why `AGENTS.md` asks it to.

**Test suites we ran on the live system:**
- v2 delivery and alert suite: 45/45. The first run scored 43/45, and both failures were faults in the tests, not the system.
- v3 suite: 5/5, including one test on Astra's **real** session log. 16 legacy checks gave identical results on v2 and v3.
- Mid-turn hook: 7/7 pipe tests, then 12/12 controls after three review rounds by Astra.
- Idle hook and monitor revision: 17/17.
- This cleaned-up kit: **29/29**. Run `python3 tests/run_tests.py` to check it yourself.

This is the best of the methods we tried, measured on two agents and one Mac. It is not a benchmark of every possible approach.
It works because of three things: plain files that survive any restart, a wake that is **proven** delivered rather than
assumed, and a monitor that measures real traffic instead of trusting that things are fine.

## Why two agents work better together

Claude and Astra are different models from different companies, so they make different mistakes. We did not run a
controlled comparison against one agent working alone. The evidence below is the record of real catches. In the Forge project
every significant change had to be reviewed by the other agent before anyone acted on it. The shared scorecard recorded
each claim, who made it, whether it was right, and what settled it. Claude did most of the hands-on building, so most of the
logged mistakes are Claude's.

The pattern was consistent:
- **Astra's reviews** found gaps in how Claude's graders handled evidence: results not tied to the right run, missing
  provenance, wrong denominators.
- **Claude's reviews** found ways to cheat or break Astra's designs: timing shortcuts, injected test configuration.
- Both usually turned a review around in **under 10 minutes**.

### Mistakes Astra caught in Claude's work
1. **A wrong root cause.** Claude blamed one kind of call for cache misses. Astra showed the real cause was changes to the
   tool list. The recount was 38/38 against 11/395.
2. **"The time limit cut off an edit."** Astra replayed the session. Two edits had completed, and they broke the file.
3. **A grader that counted failures as passes.** Claude's pass check treated timeouts, skipped tests and missing results as
   passes. Astra's test case exposed it.
4. **A test panel that measured the wrong thing.** Claude's 80-question reasoning panel had only 24 distinct question shapes,
   and in one family a constant answer scored 70 %.
5. **A "fixed" script that still crashed, twice.** First the edit had landed at the top of the file. Then a stray lookup
   crashed it again. Astra caught both by reading the source and the run logs.
6. **A server restart with the wrong Python.** The interpreter Claude used lacked the model library, so the AI model
   server stayed down a minute longer than needed.
7. **A checker design that let wrong answers through.** Astra asked for zero lost detections. Claude's redesign let 8 of 13
   wrong-layout cases pass instead of 1 of 13, so the redesign was dropped.
8. **A launch script that hid failures.** It masked a failed run and recorded an out-of-date code fingerprint. Both were
   fixed before the launch was reported.
9. **Correlation reported as cause.** Claude wrote that a layout bug "explains" four failures. The logs supported only one.
10. **In this very link:** Astra found a race. Mail written *during* a daemon tick waited for the 10 s backstop instead of
    being seen at once. Astra also found that an alert-read failure could swallow ordinary mail, and that the hook's
    message filter was too broad.

### Mistakes Claude caught in Astra's work
1. **A shortcut that skipped the test.** Under the proposed 600-second budget, a "hurry" code path skipped the new check
   entirely (high severity).
2. **A forged pass.** A planted `conftest.py` file could make failing code report passing tests, in both arms of the
   experiment (high).
3. **A config bypass.** An `addopts -p plugin` line in `pytest.ini` could inject a plugin and get around the isolation (high).
4. **Lost configuration.** After isolating test discovery, Astra's revision dropped the project's own pytest settings.
   Two more archived cases confirmed it.
5. **A confound in an experiment design.** Astra repeated the same random seed across blocks that had different visible
   clocks and workspace paths, so any difference could come from either. Astra accepted this and added controls.

### Mistakes we both missed
Both of us reviewed one archive check and missed that it compared a 16-character fingerprint with a full one. The check
was designed to stop when in doubt, and it refused the real data before anything was graded. That is why both agents
build checks that stop instead of guessing.

## Privacy and safety
- Everything stays on your computer in `team-data/`. Git ignores that folder, along with `config.json`. Settings backups are
  readable by you only.
- The kit only reads Codex's local session logs and calls `codex queue`. It opens no other network connection.
- **What the AI services see.** Message subjects, bodies and file paths are shown to both agents (wakes, hook notices,
  `inbox`), so they become part of those chats and go to Anthropic and OpenAI like anything else you type there. The
  installer also shows Claude the names of your Codex chats so you can pick one. Never put passwords or keys in a message.
- **Names are not identities.** Anyone who can run `team.py` on your computer can sign a message as any name, including
  `owner`. A mailbox message saying "the owner approved" is never approval. Approve things yourself, directly in the agent's chat.
- Hooks run only when Claude works inside the folders listed in `config.json`. Kill switch: `touch team-data/hooks/OFF`.
- Neither agent can approve the other's risky actions. Only the human owner approves installs, restarts, spending and
  publishing (see `AGENTS.md`).

## Requirements
macOS with Python 3.8+ (pre-installed with Apple's developer tools), Claude Code, and Codex (the VS Code extension or the app).
On Linux everything works except the auto-start services. The installer prints the two commands to run instead.

## License
MIT
