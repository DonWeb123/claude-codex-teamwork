#!/usr/bin/env python3
"""Set up the Claude + Codex teamwork kit on this computer (macOS; Linux works without the auto-start services).

  python3 install.py --list-threads                       show recent Codex chats (pick the partner's)
  python3 install.py --thread <CHAT-ID> [--partner-name codex] [--work-dir PATH ...] [--no-services] [--skip-tests]

What it does (each step prints what it changed):
  1. writes config.json (names, the partner's Codex chat, the folders where Claude listens)
  2. runs the self-tests (tests/run_tests.py) - nothing is installed if they fail
  3. adds 3 hooks to ~/.claude/settings.json (a backup is saved first in team-data/)
  4. starts the wake daemon + health monitor as login services (launchd), so they survive restarts
  5. sends the partner a first message with the team rules, which proves the link end to end
Undo everything: python3 uninstall.py"""
import argparse, json, os, plistlib, shlex, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "team"))
from common import DATA, CONFIG, find_codex, codex_home, get_json, put_json, team_cmd

SETTINGS = os.path.expanduser("~/.claude/settings.json")
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
SERVICES = {"wakerd": "team/wakerd.py", "commhealth": "team/commhealth.py"}


def threads():
    out = []
    p = os.path.join(codex_home(), "session_index.jsonl")
    if os.path.exists(p):
        for line in open(p):
            try:
                d = json.loads(line)
                out.append((d.get("updated_at", ""), d["id"], d.get("thread_name") or "(unnamed)"))
            except Exception:
                pass
    latest = {}
    for u, i, n in out:
        if i not in latest or u > latest[i][0]:
            latest[i] = (u, i, n)
    return sorted(latest.values(), reverse=True)


def hook_cmd(name):
    return "python3 " + shlex.quote(os.path.join(ROOT, "team", "hooks", name))   # quoted: folders with spaces work


def load_settings():
    """refuse (never overwrite) a settings file that is not a valid JSON object with well-formed hooks"""
    if not os.path.exists(SETTINGS):
        return {}
    try:
        with open(SETTINGS) as f:
            s = json.load(f)
    except Exception as e:
        sys.exit(f"{SETTINGS} could not be read as JSON ({e}): fix it first. Nothing was changed.")
    H = s.get("hooks", {}) if isinstance(s, dict) else None
    if not isinstance(H, dict) or not all(isinstance(g, list) and all(isinstance(x, dict) and isinstance(x.get("hooks", []), list) for x in g) for g in H.values()):
        sys.exit(f"{SETTINGS} has an unexpected shape (hooks must be an object of lists): fix it first. Nothing was changed.")
    return s


def backup_settings():
    if os.path.exists(SETTINGS):
        b = os.path.join(DATA, f"settings.backup.{time.strftime('%Y%m%d-%H%M%S')}.json")
        shutil.copyfile(SETTINGS, b); os.chmod(b, 0o600)   # settings can hold secrets: owner-only
        print("  backup of your Claude settings:", b)


def add_hooks():
    s = load_settings()
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup_settings()
    H = s.setdefault("hooks", {})
    want = {"Stop": ({"type": "command", "command": hook_cmd("idle_wake.py"), "timeout": 21600, "asyncRewake": True}, None),
            "SessionStart": ({"type": "command", "command": hook_cmd("idle_wake.py"), "timeout": 21600, "asyncRewake": True}, None),
            "PostToolUse": ({"type": "command", "command": hook_cmd("midturn.py"), "timeout": 10}, "*")}
    for ev, (h, matcher) in want.items():
        groups = H.setdefault(ev, [])
        if any(x.get("command") == h["command"] for g in groups for x in g.get("hooks", [])):
            continue
        g = {"hooks": [h]}
        if matcher:
            g["matcher"] = matcher
        groups.append(g)
    put_json(SETTINGS, s); print("  hooks added to", SETTINGS)


def plist(name, script):
    label = f"com.claudecodexteam.{name}"
    log = os.path.join(DATA, "logs", f"{name}.log")
    return label, plistlib.dumps({"Label": label, "ProgramArguments": [sys.executable, os.path.join(ROOT, script), "run"],
                                  "WorkingDirectory": ROOT, "RunAtLoad": True, "KeepAlive": True,
                                  "StandardOutPath": log, "StandardErrorPath": log})


def start_services():
    if sys.platform != "darwin":
        print("  not macOS: start these yourself and keep them running:")
        for n, s in SERVICES.items():
            print(f"    nohup python3 {os.path.join(ROOT, s)} run >/dev/null 2>&1 &")
        return
    os.makedirs(AGENTS, exist_ok=True)
    ok = True
    for n, s in SERVICES.items():
        label, data = plist(n, s); p = os.path.join(AGENTS, label + ".plist")
        subprocess.run(["launchctl", "unload", p], capture_output=True)
        with open(p, "wb") as f:
            f.write(data)
        r = subprocess.run(["launchctl", "load", p], capture_output=True, text=True)
        if r.returncode != 0:
            ok = False; print(f"  service {label}: FAILED to load: {r.stderr.strip()}"); continue
        # verify: launchd runs OUR program (not a stale copy) and it is alive
        pid = None
        for _ in range(20):
            out = subprocess.run(["launchctl", "list", label], capture_output=True, text=True).stdout
            pid = next((l.split("=")[1].strip(" ;") for l in out.splitlines() if '"PID"' in l), None)
            if pid:
                break
            time.sleep(0.5)
        args = subprocess.run(["ps", "-p", pid, "-o", "args="], capture_output=True, text=True).stdout if pid else ""
        if pid and os.path.join(ROOT, s) in args:
            print(f"  service {label}: running (pid {pid})")
        else:
            ok = False; print(f"  service {label}: NOT running: see {os.path.join(DATA, 'logs', n + '.log')}")
    hb = os.path.join(DATA, "wake", "heartbeat.json")
    for _ in range(30):
        if (get_json(hb) or {}).get("t", 0) > time.time() - 30:
            print("  wake daemon heartbeat: OK"); break
        time.sleep(0.5)
    else:
        ok = False; print("  wake daemon heartbeat: MISSING after 15 s")
    if not ok:
        sys.exit("A background service did not start: show this output to Claude. (Hooks are installed; python3 uninstall.py undoes everything.)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-threads", action="store_true"); ap.add_argument("--thread")
    ap.add_argument("--partner-name", default="codex"); ap.add_argument("--claude-name", default="claude")
    ap.add_argument("--work-dir", action="append", default=[]); ap.add_argument("--no-services", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or newer is needed")
    codex = find_codex()
    if a.list_threads or not a.thread:
        T = threads()
        if not T:
            print("No Codex chats found. Open Codex (VS Code extension or app), start a chat, send it one message, then run this again.")
        else:
            print("Recent Codex chats (newest first). Pick the partner's chat and rerun with --thread <id>:")
            for u, i, n in T[:15]:
                print(f"  {i}  {u[:16].replace('T', ' ')}  {n}")
        if not codex:
            print("WARNING: the Codex command-line tool was not found (it ships inside the Codex VS Code extension / app).")
        return
    if not codex:
        sys.exit("The Codex command-line tool was not found. Install the Codex extension in VS Code (or the Codex app), open it once, then rerun.")
    if a.partner_name.lower() in (a.claude_name.lower(), "owner"):
        sys.exit("pick a different --partner-name")
    work = [os.path.realpath(os.path.expanduser(d)) for d in (a.work_dir or [ROOT])]
    if ROOT not in work:
        work.append(ROOT)
    old = get_json(CONFIG, {}) or {}
    cfg = {"claude_name": a.claude_name.lower(), "partner_name": a.partner_name.lower(), "codex_thread": a.thread,
           "work_dirs": work, "installed_at": old.get("installed_at") or time.time()}
    put_json(CONFIG, cfg); print("1. config written:", CONFIG)
    if not a.skip_tests:
        print("2. self-tests ...")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "run_tests.py")])
        if r.returncode != 0:
            sys.exit("Self-tests failed: nothing else was installed. Show this output to Claude.")
    import glob as _g
    bp = os.path.join(DATA, "wake", "baseline.json")
    if not os.path.exists(bp):   # wake for everything sent from now on (incl. the first message below), never for older history
        ids = [int(os.path.basename(x)[1:-5]) for x in _g.glob(os.path.join(DATA, "msgs", "M*.json")) if os.path.basename(x)[1:-5].isdigit()]
        put_json(bp, {"id": max(ids or [0]), "t": time.time()})
    print("3. Claude hooks"); add_hooks()
    if not a.no_services:
        print("4. background services"); start_services()
    from common import msgs as _msgs
    if any(m.get("type") == "HANDOFF" and m.get("subject", "").startswith("Team link is live") and m.get("to") == cfg["partner_name"] for m in _msgs()):
        print("5. first message already sent earlier (not repeated)"); return
    rules = os.path.join(ROOT, "AGENTS.md")
    body = (f"Hello {cfg['partner_name']}, this is {cfg['claude_name']}. We now share a mailbox so we can work as a team. "
            f"Rules: {rules} (please read it once). Every message reaches you as a wake in this chat. Reply with: "
            f"{team_cmd()} send --as {cfg['partner_name']} --to {cfg['claude_name']} --type ANSWER --re <id> --subject '...' --body '...'. "
            f"During long work, check {team_cmd()} inbox --as {cfg['partner_name']} between steps. Please ANSWER this message to prove the link works.")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", cfg["claude_name"], "--to", cfg["partner_name"],
                        "--type", "HANDOFF", "--re", "none", "--subject", "Team link is live: please read the rules and answer", "--body", body],
                       capture_output=True, text=True)
    print("5. first message to the partner:", (r.stdout or r.stderr).strip())
    print(f"\nDone. Restart Claude Code in {ROOT} so the hooks load, then check the link with:\n"
          f"  python3 {os.path.join(ROOT, 'team', 'commhealth.py')} once")


if __name__ == "__main__":
    main()
