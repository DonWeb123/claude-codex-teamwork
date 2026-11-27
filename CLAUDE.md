# Instructions for Claude (read this first)

This folder is the Claude + Codex teamwork kit. The person using it may not be technical: do the work for them,
explain in plain words, and ask only what you cannot find out yourself.

## "Set it up" / "run the git" / "install this"
1. Check the basics yourself: `python3 --version` (3.8+), and that Codex is installed
   (the Codex extension in VS Code, or the Codex app). If Codex is missing, tell them in one sentence how to install it, then wait.
2. `python3 install.py --list-threads` lists their Codex chats. If there is none, ask them to open Codex,
   start a new chat, and type "hello". Then list again.
3. Ask which chat is the partner (show the names), and what they want to call it (default `codex`).
4. `python3 install.py --thread <id> --partner-name <name>` (add `--work-dir <their project folder>` if Claude
   should also listen there). It runs the self-tests first and stops if any fail.
5. Tell them to restart Claude Code in this folder (so the hooks load). After the restart, check:
   `python3 team/commhealth.py once` and `python3 team/team.py inbox --as claude`. The partner should have
   answered the first HANDOFF message. That answer is the end-to-end proof. Tell them it works only after you see it.

## Working with the partner
- Read new mail: `python3 team/team.py inbox --as claude`, then `ack` it.
- Send: `python3 team/team.py send --as claude --to <partner> --type ASK|REVIEW|RESULT|FYI|... --subject '...' --body '...'`
  Use single quotes or `--body-file` (backticks inside double quotes get executed by the shell).
  Anything that needs an answer goes as ASK/REVIEW/PROPOSE. Answers always carry `--re <id>`.
- Rules for both agents: [AGENTS.md](AGENTS.md).
- Keep foreground tool calls under ~60 s (run long jobs in the background). The mid-turn hook can only show mail
  between tool calls.

## If something is wrong
- `python3 team/commhealth.py once` shows latency, unseen mail and open incidents.
- **CODEX RESTARTED** alert: after a VS Code / Codex restart, wakes are accepted but no turn starts until the
  partner's chat is opened once. Ask the person to click that chat in the Codex panel, or do it yourself
  if you have screen control.
- Turn the hooks off: `touch team-data/hooks/OFF` (both) or `team-data/hooks/MIDTURN_OFF` (mid-turn only).
- Remove everything: `python3 uninstall.py`.
- Tests: `python3 tests/run_tests.py` (core) and `python3 tests/test_install.py` (install/uninstall, fake home folder).
