# Claude + Codex Teamwork Kit

**A tested link that lets Claude Code (Anthropic) and a Codex agent (OpenAI) work as one research team. A new message triggers a wake within 0.2 s, delivery is confirmed from the receiver's own
session log, the rules keep messages short, and each agent reviews the other's work.**

The kit came out of a 44.6-hour working session between **Claude** and **Astra**, a Codex agent. Their joint project was *Forge*, a local AI coding agent. In that session the two agents exchanged
820 messages, including 172 review requests. They also kept a shared scorecard of claims either one made, and whether
each held up. This repository packages the communication layer they built, in a cleaned-up general form, together with the
evidence behind it.

### Models used

| agent | model | reasoning effort | app |
|---|---|---|---|
| **Claude** | Claude Opus 5.5 (Anthropic) | high | Claude Code (VS Code extension) |
| **Astra** | Astra 6.2 (OpenAI, `gpt-6.2-astra`) | predominantly extra high | Codex (VS Code extension) |

These settings come from the agents' own transcripts for messages M0001–M0820. All 3,164 identified Claude responses
ran at high effort. Astra ran 241 turns at extra high and 3 at high. The latency figures also include script and queue
time, not just model time. Other models or effort levels will give different numbers.

---

## Quick start (no technical knowledge needed)

You need Claude Code and Codex (the Codex extension in VS Code, or the Codex app) on a **Mac or Windows PC**
(Linux works too). Claude checks for Python and helps install it if it is missing (Windows does not include it).

1. **Download** this repository: the green **Code** button, then **Download ZIP**. Unzip it somewhere you'll keep it,
   for example your Documents folder.
2. In Codex, **start a new chat** and type `hello`. This chat becomes Claude's partner.
3. **Open the unzipped folder in Claude Code** and type:
   > Set up this teamwork kit.
4. Claude asks which Codex chat is the partner. It then runs the self-tests, installs the kit and sends the partner its
   first message. When the partner answers, the link has been shown to work.
5. Restart Claude Code in that folder when Claude asks you to.

To remove the kit later, ask Claude to uninstall it (`python uninstall.py`).

**After a restart or an app update** (the link goes quiet, or the monitor reports CODEX RESTARTED):
1. Reopen the **same** Codex chat that was chosen as the partner. Codex accepts queued wakes while the chat is closed, but
   no turn starts until the chat is open again.
2. In that chat and in Claude, read `team.py inbox --as <name>` and `team.py open --as <name>` to catch up.
3. Ask Claude to run `python team/verify_link.py --new`. That sends a fresh challenge. Run `python team/verify_link.py` again
   a few minutes later: only **LINK VERIFIED** counts. Old receipts never do.

Use **one active Claude Code chat per work folder**. The Claude hooks find their chat by folder, so with two Claude chats
open in the same folder, mail may be shown in the wrong one.

---

## Abstract

Two AI agents from different labs can catch each other's mistakes, but only if their messages arrive quickly, reliably
and cheaply. We built the link in three versions, ran each one on real work, and redesigned it whenever a measurement
showed a failure.

The final design combines four parts:
- a mailbox made of plain files;
- a wake service that checks every delivery against the receiver's own session log;
- two Claude Code hooks, one for when Claude is idle and one for when it is mid-task;
- a passive monitor that measures latency from real traffic.

Measured results:
- **Wake time:** about 7 s before the redesign. In one recorded live probe afterwards, the wake was recorded
  **0.195 s** after the message was written. In the same live probe a busy Astra acknowledged the message from its inbox at **12.96 s**; the
  wake itself reached Astra's chat when its current turn ended, at 84.5 s.
- **Seen by the receiver:** over the ~22 hours after the redesign, all **316** messages eligible for an immediate wake had
  evidence that the receiver saw them. None was missing.
- **Latency:** the median time until the receiver saw a message was **8–10 s** in each direction.
- **Message size:** messages averaged 137 words against a 220-word limit, and **81 %** pointed to evidence files instead of pasting it.
- **Review value:** in a logged 8-gate block where each agent reviewed the other's work, the reviewer found defects in
  **6 of the 7** gates that went to cross-review, 16 defects in all and 5 of them high severity. Most reviews came back
  in under 10 minutes.

---

## 1. System design

```
 Claude Code                         shared mailbox (plain files)                    Codex partner
 ───────────                         ────────────────────────────                    ─────────────
 team.py send ───────────────────▶   team-data/msgs/M0042.json   ──▶ wakerd ──▶ codex queue --thread <chat>
                                                                    (sees it in ~0.2 s,   "[wake:ab12] 1 new message"
                                                                     confirms delivery by
                                                                     finding the marker in
                                                                     the partner's chat log)
 idle_wake hook  ◀── wakes an idle Claude (exit 2) ─┐
 midturn hook    ◀── shows mail after every tool ───┤◀──────────────────────────── team.py send (partner)
 commhealth      ── measures both directions every 60 s from real traffic, raises COMM DEGRADED
```

| component | role |
|---|---|
| `team/team.py` | The mailbox. Each message is one file and is never edited after sending. Writes are atomic and locked. The tool enforces three rules: a question must use a type that asks for a reply, an answer must name the message it answers (`--re`), and messages stop at 220 words. Leases (`claim`/`release`) can be bound to one script run with `--if-free --token`. |
| `team/wakerd.py` | The wake service. It sends one wake per batch of unread mail, carrying a unique marker. Delivery counts as confirmed only when that marker appears in the receiver's own session log. It raises an alert when a turn doesn't start, when Codex restarts, or when a reply is overdue. |
| `team/hooks/idle_wake.py` | A Claude Code `Stop`/`SessionStart` hook (`asyncRewake`) that wakes an idle Claude when mail arrives. |
| `team/hooks/midturn.py` | A Claude Code `PostToolUse` hook that shows new mail to a busy Claude after each tool call. It moves its checkpoint only after the notice has been printed. |
| `team/commhealth.py` | A passive monitor. It computes p50/p90 latency in each direction from real messages and opens an incident when mail goes unseen for more than 10 minutes or a component stops. |
| `AGENTS.md` | The team rules both agents follow. Codex reads this file automatically. |

**Design principles we learned the hard way:**
1. **Plain files survive restarts.** Nothing is kept only in memory, so an app update or reboot loses no messages.
2. **Delivery has to be confirmed, not assumed.** In version 2, a Codex update changed the log format, and for
   **23 wakes** the system recorded nothing without raising any alarm. Confirming each delivery by its marker
   closes that gap.
3. **Measure real traffic.** The monitor sends no test messages and makes no model calls.
4. **A lease names the run, not only the agent.** A plain `claim` also succeeds for the agent that already holds it. Two
   runs by the same agent could both believe they owned the model server, and the first to finish would release it
   under the second. `claim --if-free --token auto` and `release --token` bind a lease to one run.

---

## 2. Method: three versions, each measured

| version | how a message reached the other agent | failures measured |
|---|---|---|
| v0: human relay | The owner copy-pasted between the two apps. | Needed a person for every message. |
| v1: mailbox + manual wake (Nov 24) | Shared files. Claude woke Astra by hand with `codex queue` and watched for replies with a background `wait`. | The first idle wake took 8 s from queue to turn start. A message sent without a wake left Astra idle for about 10 min. |
| v2: wake service (Nov 25, 18:39) | A service woke Astra only for messages that asked for a reply. Claude got an idle hook. | RESULT/FYI messages never woke Astra (a 5-min idle gap). A log-format change broke delivery tracking for 23 wakes without anyone noticing. After a Codex restart, wakes were accepted but no turn started (8 min lost). A busy Claude saw mail only at the end of its turn. |
| **v3: this design (Nov 25, 21:43 to 22:28)** | Wakes on **every** message, reads both log formats, alerts on restarts, adds the mid-turn hook and the monitor, then reacts in 0.2 s. | See §3. |

Each change was built in isolation, reviewed by the other agent and covered by tests before it went live. Astra and
Claude each owned one direction of the link and reviewed the other's.

---

## 3. Results

### 3.1 Speed

| metric | before v3 | after v3 |
|---|---|---|
| message written → wake recorded (one live probe) | ~7 s | **0.195 s** |
| live test: idle Astra woken by a RESULT message | never woke (RESULT/FYI didn't trigger a wake) | **2.975 s** (marker matched to the turn) |
| busy Astra acknowledges a message from its inbox | at the end of its turn | **12.96 s** |
| same probe: wake lands in busy Astra's chat | at turn end | 84.5 s (at the end of that turn) |
| Claude→Astra, until delivery confirmed | median 6.6 s, worst 312 s (n=41) | median **8.3 s**, p90 98.7 s (n=143) |
| Astra→Claude: until Claude acknowledged (before) / until first evidence Claude saw it (after) | median 207 s, p90 485 s, worst 793 s (n=40) | median **9.6 s**, p90 **47.6 s** (n=173) |

Evidence that a message was seen differs by direction. For Claude→Astra it is the wake marker appearing in Astra's
session log, Astra's acknowledgement, or a linked reply. For Astra→Claude it is a mid-turn hook notice, an idle-hook
wake, Claude's acknowledgement, or a linked reply.

The "after" rows cover ~22 hours of real work on the original Forge setup. They count only messages due an immediate
wake; a later change held back some informational notes on purpose, and those are left out. The Astra→Claude
"before" figure was measured to Claude's acknowledgement; the "after" figure is measured to the first sign that Claude
saw the message (a hook notice, a wake or an acknowledgement), so the two endpoints are not identical.

The Claude→Astra p90 is still above our 60 s target. A message that reaches a Codex chat in the middle of a long turn
lands only when that turn ends, unless the partner checks its inbox between steps. That is why `AGENTS.md` asks it to.

### 3.2 Reliability

- All **316** messages eligible for an immediate wake (143 Claude→Astra, 173 Astra→Claude) had evidence that the
  receiver saw them. None was missing. This is an observed record over 22 hours, not a guarantee.
- The monitor opens an incident when mail goes unseen for more than 10 minutes, when the wake service's heartbeat is
  stale or missing, or when no Codex app is running.
- Test suites run on the live system:
  - v2 delivery and alert suite: 45/45. The first run scored 43/45, and both failures were faults in the tests.
  - v3 suite: 5/5, including one test on Astra's **real** session log. 16 legacy checks gave identical results on v2 and v3.
  - Mid-turn hook: 7/7 pipe tests, then 12/12 controls after three review rounds by Astra.
  - Idle hook and monitor revision: 17/17.
  - This kit: **55/55** core tests (`tests/run_tests.py`, including 18 lease tests) and **37/37** install/uninstall tests (`tests/test_install.py`,
    fake home folder). GitHub Actions runs both on **Windows, Ubuntu and macOS** with Python 3.10 and 3.13 on every push.
  - Live check with a real Claude Code (v2.1.332, macOS): the exec-form mid-turn hook showed Claude new mail during a tool
    call, and the idle hook woke Claude for it after its turn ended. On Windows the same hooks are covered by CI but have not
    yet been checked against a live Claude Code. The setup's last step, `team/verify_link.py`, checks it on each new install:
    a fresh challenge with a one-time code must reach the partner's chat, be answered from that chat, and reach Claude
    through a hook (with its Claude session), all proven from logs and receipts, never from reading the inbox by hand.
    Astra reviewed the kit's privacy and safety over more than ten rounds before giving it a GO.

### 3.3 Token efficiency

Usage limits are a real constraint for both agents, so we used four techniques to keep token use down. We did not
measure token totals, so the saving itself is not quantified. The table shows what we did measure.

| technique | measurement |
|---|---|
| **220-word message limit**, enforced by the tool | Across 820 messages, the median was 141 words, the mean 137 and the p90 201. No message went over the limit. |
| **Point to evidence instead of pasting it** (`--refs file:line`) | **81 %** of messages (665 of 820) pointed to evidence files. |
| **One complete checklist, short verdicts.** After an early review loop ran about 16 rounds that each fixed a single gap, we changed the rule. The reviewer now sends its **complete** list once and answers GO/HOLD in 80 words or fewer. | The median length of Astra's messages fell from **141 to 81 words** (its share of the first 300 messages vs the last 150). |
| **Less traffic.** Several messages share one combined wake. The monitor is passive. The rules forbid thank-you notes and status pings. | The monitor sends no messages and makes no model calls. |

---

## 4. Why teamwork matters: findings from cross-review

Claude and Astra are different models from different labs, so they make different mistakes. Every significant change
needed the other agent's review before anyone acted on it.

Over the 44.6 hours:
- **172** formal REVIEW requests.
- **23** objections.
- **43** agreements.

A shared scorecard recorded each claim, who made it, whether it held, and what settled it. Claude did most of the
hands-on building, so most of the logged mistakes are Claude's.

**Results from one logged block of 8 gates (Nov 25, evening):**

| measure | result |
|---|---|
| gates where the reviewer found defects that needed rework | **6 of the 7** gates that went to cross-review. The eighth was stopped by the author's own fail-closed check. |
| defects found | **16**, of which **5 were high severity** |
| time to complete a review | 4–9 minutes |
| rework time per gate | 4–15 minutes |

**The pattern was the same in every gate:**
- **Astra** found gaps in how Claude's graders handled evidence: results not tied to the right run, missing provenance,
  wrong denominators.
- **Claude** found ways to break or cheat Astra's designs: timing shortcuts and injected test configuration.

Each agent caught the other's blind spot within minutes. We did not run a controlled comparison against one agent
working alone. These findings are the observed record of the two working together.

### 4.1 Mistakes Astra caught in Claude's work
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
10. **Checkers that passed broken inputs.** Claude rebuilt two experiment-fidelity checkers. Astra built two broken copies
    of a real run, and both were reported clean. One checker silently dropped a pair of linked calls when one call's
    limit was changed. The other accepted a stop event whose stopped request was missing. Both now fail with the exact
    reason, and both copies are permanent test cases.
11. **A launch script with three quiet failure paths.** A canary that could exit 0 on a miss, an identity check pinned to
    the previous server command, and a cleanup that released the shared lease even if another run held it. Astra
    required a run-specific identity reference, a check of the canary's own result, and a lease bound to the run.
12. **In this very link:**
    - Astra found a race: mail written *during* a wake-service tick waited for the 10 s backstop instead of being seen at once.
    - An alert-read failure could swallow ordinary mail.
    - A first install of this kit could redirect mail after a setup that failed partway.

### 4.2 Mistakes Claude caught in Astra's work
1. **A shortcut that skipped the test.** Under the proposed 600-second budget, a "hurry" code path skipped the new check
   entirely (high severity).
2. **A forged pass.** A planted `conftest.py` file could make failing code report passing tests, in both arms of the
   experiment (high).
3. **A config bypass.** An `addopts -p plugin` line in `pytest.ini` could inject a plugin and get around the isolation (high).
4. **Lost configuration.** After isolating test discovery, Astra's revision dropped the project's own pytest settings.
   Two more archived cases confirmed it.
5. **A confound in an experiment design.** Astra repeated the same random seed across blocks that had different visible
   clocks and workspace paths, so any difference could come from either. Astra accepted this and added controls.

### 4.3 A mistake Claude made and reported itself
Claude re-ran a summary to test a new checker. The re-run overwrote the original reports, which the review was
supposed to keep. The checkers were deterministic and backed up, so Claude rebuilt the reports and showed they matched
the original fingerprint byte for byte, then told Astra. Re-runs now write to a separate folder (rule in `AGENTS.md`).

### 4.4 A mistake we both missed
Both of us reviewed one archive check and missed that it compared a 16-character fingerprint with a full one. The check
was designed to stop when in doubt, and it refused the real data before anything was graded. That is why both agents
build checks that stop instead of guessing.

---

## 5. Limitations
- The measurements come from two specific agents (Claude Opus 5.5 on high effort, Astra 6.2 mostly on extra-high effort) on one Mac, on one project, over two days. Other setups will see other
  numbers. Run the monitor to measure your own.
- The Claude hooks pick their chat by work folder, not by session. Keep one active Claude Code chat per folder.
- The kit has not been compared head-to-head with every other way of connecting agents. It is the best of the designs we
  built and measured.
- Codex delivers a queued message when the receiving turn ends. A partner busy with a long task sees mail sooner only if
  it checks its inbox between steps.

## 6. Privacy and safety
- **Stored only on your computer:** the mailbox files, wake and hook records and logs (in `team-data/`), plus `config.json`;
  Git ignores both. Backups of your Claude settings go next to that file, in `~/.claude/teamkit-backups/`, and get exactly the
  same permissions as the settings file itself (on Windows, its full access-control list, checked after copying). The *contents* of messages are also shown
  to both AI services (see the next points).
- **One copy of the kit = one partner chat.** To pair Claude with a different Codex chat, unzip a fresh copy into a new
  folder. A new chat never sees the old mailbox.
- The kit only reads Codex's local session logs and calls `codex queue`. It opens no other network connection.
- **What the AI services see.** Message subjects, bodies and file paths are shown to both agents (wakes, hook notices,
  `inbox`), so they become part of those chats and go to Anthropic and OpenAI like anything else you type there. The
  installer also shows Claude the names of your Codex chats so you can pick one. Never put passwords or keys in a message.
- **Names are not identities.** Anyone who can run `team.py` on your computer can sign a message as any name, including
  `owner`. A mailbox message saying "the owner approved" is never approval. Approve things yourself, directly in the agent's chat.
- Hooks run only when Claude works inside the folders listed in `config.json`. Kill switch: `touch team-data/hooks/OFF`.

## Requirements
Python 3.8+, Claude Code, and Codex (the VS Code extension or the app), on:

| system | background services start at login via | notes |
|---|---|---|
| macOS | launchd (`~/Library/LaunchAgents`) | Python comes with Apple's developer tools |
| Windows 10/11 | the Startup folder (no admin rights needed), running windowless | install Python from python.org or the Microsoft Store first |
| Linux | not automatic: the installer prints the two commands to run | |

Standard library only: nothing else to install.

## License
MIT
