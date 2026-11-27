#!/usr/bin/env python3
"""Set up the Claude + Codex teamwork kit on this computer (macOS; Linux works without the auto-start services).

  python3 install.py --list-threads                       show recent Codex chats (pick the partner's)
  python3 install.py --thread <CHAT-ID> [--partner-name codex] [--work-dir PATH ...] [--no-services] [--skip-tests]

Setup is all-or-nothing: everything is validated first, and if any later step fails, config.json, your Claude settings
and the services are put back exactly as they were, so mail keeps going where it went before.
  1. self-tests (tests/run_tests.py)
  2. config.json (names, the partner's Codex chat, the folders where Claude listens)
  3. 3 hooks in ~/.claude/settings.json (owner-only backup first)
  4. wake daemon + health monitor as login services (launchd), verified running
  5. a first message to the partner with the team rules
One copy of the kit = one partner chat. To pair with a different Codex chat, use a fresh copy of the kit in another folder
(the old mailbox is never shown to a new chat).   Undo everything: python3 uninstall.py"""
import argparse, fcntl, json, os, plistlib, re, shlex, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "team"))
from common import DATA, CONFIG, find_codex, codex_home, get_json, put_json, team_cmd, msgs

SETTINGS = os.path.expanduser("~/.claude/settings.json")
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
SERVICES = {"wakerd": "team/wakerd.py", "commhealth": "team/commhealth.py"}
LAUNCHCTL = os.environ.get("TEAMKIT_LAUNCHCTL", "launchctl")   # tests substitute a fake
NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")


class SetupError(Exception):
    pass


def threads():
    latest = {}
    p = os.path.join(codex_home(), "session_index.jsonl")
    if os.path.exists(p):
        for line in open(p):
            try:
                d = json.loads(line); u, i, n = d.get("updated_at", ""), d["id"], d.get("thread_name") or "(unnamed)"
            except Exception:
                continue
            if i not in latest or u > latest[i][0]:
                latest[i] = (u, i, n)
    return sorted(latest.values(), reverse=True)


def hook_cmd(name):
    return "python3 " + shlex.quote(os.path.join(ROOT, "team", "hooks", name))   # quoted: folders with spaces work


def load_settings():
    """refuse (never overwrite) a settings file that is not a valid JSON object with well-formed hooks, down to each hook"""
    if not os.path.exists(SETTINGS):
        return {}
    try:
        with open(SETTINGS) as f:
            s = json.load(f)
    except Exception as e:
        raise SetupError(f"{SETTINGS} could not be read as JSON ({e}): fix it first. Nothing was changed.")
    H = s.get("hooks", {}) if isinstance(s, dict) else None
    ok = isinstance(H, dict) and all(
        isinstance(groups, list) and all(
            isinstance(g, dict) and isinstance(g.get("hooks", []), list)
            and all(isinstance(h, dict) and isinstance(h.get("command", ""), str) for h in g.get("hooks", []))
            for g in groups)
        for groups in H.values())
    if not ok:
        raise SetupError(f"{SETTINGS} has an unexpected shape (hooks -> lists of groups -> lists of hooks): fix it first. Nothing was changed.")
    return s


def backup_settings():
    """unique, owner-only (0600) backup created BEFORE any byte is copied into it; returns (path, original bytes)"""
    if not os.path.exists(SETTINGS):
        return None, None
    with open(SETTINGS, "rb") as f:
        data = f.read()
    b = os.path.join(DATA, f"settings.backup.{time.strftime('%Y%m%d-%H%M%S')}.{os.getpid()}.{os.urandom(3).hex()}.json")
    fd = os.open(b, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    print("  backup of your Claude settings:", b)
    return b, data


def restore_file(path, data):
    """put a file back to its exact earlier bytes (None = it did not exist)"""
    if data is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = f"{path}.restore.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def add_hooks():
    s = load_settings()
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
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


def plist_owned(label, script):
    """True if no plist exists or it is OURS (same label + same program in this folder); False = someone else's file"""
    p = os.path.join(AGENTS, label + ".plist")
    if not os.path.exists(p):
        return True
    try:
        with open(p, "rb") as f:
            d = plistlib.load(f)
        return d.get("Label") == label and (d.get("ProgramArguments") or [None, None])[1:2] == [os.path.join(ROOT, script)]
    except Exception:
        return False


def service_pid(label):
    out = subprocess.run([LAUNCHCTL, "list", label], capture_output=True, text=True).stdout
    pid = next((l.split("=")[1].strip(" ;") for l in out.splitlines() if '"PID"' in l), None)
    return int(pid) if pid and pid.isdigit() else None


def stop_service(n):
    """unload this copy's service and wait until its process is really gone; returns True when stopped"""
    label = f"com.claudecodexteam.{n}"; p = os.path.join(AGENTS, label + ".plist")
    if not os.path.exists(p):
        return True
    pid = service_pid(label)
    subprocess.run([LAUNCHCTL, "unload", p], capture_output=True)
    for _ in range(20):
        if service_pid(label) is None and (pid is None or subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0):
            return True
        time.sleep(0.5)
    return False


def start_services():
    """(re)start both services; raises SetupError unless both run OUR program and the daemon we launched writes a fresh heartbeat"""
    if sys.platform != "darwin" and LAUNCHCTL == "launchctl":
        print("  not macOS: start these yourself and keep them running:")
        for s in SERVICES.values():
            print(f"    nohup python3 {shlex.quote(os.path.join(ROOT, s))} run >/dev/null 2>&1 &")
        return
    os.makedirs(AGENTS, exist_ok=True); pids = {}; t_launch = time.time()
    for n, s in SERVICES.items():
        label, data = plist(n, s); p = os.path.join(AGENTS, label + ".plist")
        if not stop_service(n):
            raise SetupError(f"the old {label} did not stop")
        with open(p, "wb") as f:
            f.write(data)
        r = subprocess.run([LAUNCHCTL, "load", p], capture_output=True, text=True)
        if r.returncode != 0:
            raise SetupError(f"{label} failed to load: {r.stderr.strip()}")
        pid = None
        for _ in range(20):
            pid = service_pid(label)
            if pid:
                break
            time.sleep(0.5)
        args = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True).stdout if pid else ""
        if not pid or os.path.join(ROOT, s) not in args:
            raise SetupError(f"{label} is not running our program (see {os.path.join(DATA, 'logs', n + '.log')})")
        pids[n] = pid; print(f"  service {label}: running (pid {pid})")
    hb_p = os.path.join(DATA, "wake", "heartbeat.json")
    for _ in range(30):   # the heartbeat must come from the daemon we just launched, after its launch
        hb = get_json(hb_p) or {}
        if hb.get("pid") == pids["wakerd"] and hb.get("t", 0) >= t_launch:
            print("  wake daemon heartbeat: OK (from the launched pid)"); return
        time.sleep(0.5)
    raise SetupError("no fresh heartbeat from the launched wake daemon after 15 s")


def remove_services():
    for n in SERVICES:
        p = os.path.join(AGENTS, f"com.claudecodexteam.{n}.plist")
        if os.path.exists(p) and stop_service(n):
            os.remove(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-threads", action="store_true"); ap.add_argument("--thread")
    ap.add_argument("--partner-name", default="codex"); ap.add_argument("--claude-name", default="claude")
    ap.add_argument("--work-dir", action="append", default=[]); ap.add_argument("--no-services", action="store_true")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    if sys.version_info < (3, 8):
        sys.exit("Python 3.8 or newer is needed")
    codex = find_codex(); T = threads()
    if a.list_threads or not a.thread:
        if not T:
            print("No Codex chats found. Open Codex (VS Code extension or app), start a chat, send it one message, then run this again.")
        else:
            print("Recent Codex chats (newest first). Pick the partner's chat and rerun with --thread <id>:")
            for u, i, n in T[:15]:
                print(f"  {i}  {u[:16].replace('T', ' ')}  {n}")
        if not codex:
            print("WARNING: the Codex command-line tool was not found (it ships inside the Codex VS Code extension / app).")
        return
    lk = open(os.path.join(DATA, "locks", "install.lock"), "w")   # one install/uninstall at a time
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("another install or uninstall of this kit is running")
    # ---- validate EVERYTHING before changing anything
    try:
        if not codex:
            raise SetupError("The Codex command-line tool was not found. Install the Codex extension in VS Code (or the Codex app), open it once, then rerun.")
        a.partner_name, a.claude_name = a.partner_name.lower(), a.claude_name.lower()
        for v in (a.partner_name, a.claude_name):
            if not NAME_RE.fullmatch(v):
                raise SetupError(f"bad name {v!r}: lowercase letters, digits, _ or -, starting with a letter")
        if a.partner_name in (a.claude_name, "owner") or a.claude_name == "owner":
            raise SetupError("pick a different --partner-name")
        if a.thread not in {i for _, i, _ in T}:
            raise SetupError(f"--thread {a.thread!r} is not one of your Codex chats (exact id from --list-threads)")
        prev = (get_json(CONFIG, {}) or {}).get("codex_thread")
        if prev and prev != a.thread:
            raise SetupError("This copy of the kit is already paired with another Codex chat. To pair a different chat, "
                             "unzip a fresh copy of the kit into a new folder and set that one up. Nothing was changed.")
        load_settings()
        if not a.no_services:
            for n, sc in SERVICES.items():
                if not plist_owned(f"com.claudecodexteam.{n}", sc):
                    raise SetupError(f"~/Library/LaunchAgents/com.claudecodexteam.{n}.plist belongs to another copy of this kit: run that copy's uninstall.py first. Nothing was changed.")
    except SetupError as e:
        sys.exit(str(e))
    if not a.skip_tests:
        print("1. self-tests ...")
        if subprocess.run([sys.executable, os.path.join(ROOT, "tests", "run_tests.py")]).returncode != 0:
            sys.exit("Self-tests failed: nothing was installed or changed. Show this output to Claude.")
    work = [os.path.realpath(os.path.expanduser(d)) for d in (a.work_dir or [ROOT])]
    if ROOT not in work:
        work.append(ROOT)
    # ---- transaction: remember the exact earlier state, roll back on any failure
    W = os.path.join(DATA, "wake")
    keep = {CONFIG: read_bytes(CONFIG), SETTINGS: read_bytes(SETTINGS),
            os.path.join(W, "baseline.json"): read_bytes(os.path.join(W, "baseline.json")),
            os.path.join(W, "partner_log.json"): read_bytes(os.path.join(W, "partner_log.json"))}
    had_services = {n: read_bytes(os.path.join(AGENTS, f"com.claudecodexteam.{n}.plist")) for n in SERVICES}
    was_running = {n: service_pid(f"com.claudecodexteam.{n}") is not None for n in SERVICES} if not a.no_services else {}
    old = get_json(CONFIG, {}) or {}
    cfg = {"claude_name": a.claude_name, "partner_name": a.partner_name, "codex_thread": a.thread, "work_dirs": work,
           "installed_at": old.get("installed_at") or time.time(),
           "handoff_thread": old.get("handoff_thread")}
    try:
        if not a.no_services:
            for n in SERVICES:   # nothing may deliver while routing changes
                if not stop_service(n):
                    raise SetupError(f"could not stop the running {n}")
        ids = [int(m["id"][1:]) for m in msgs() if str(m.get("id", ""))[1:].isdigit()]
        if not os.path.exists(os.path.join(W, "baseline.json")):   # wake only for mail sent from now on (incl. the first message below)
            put_json(os.path.join(W, "baseline.json"), {"id": max(ids or [0]), "t": time.time(), "chat": a.thread})
        put_json(CONFIG, cfg); print("2. config written:", CONFIG)
        print("3. Claude hooks"); backup_settings(); add_hooks()
        if not a.no_services:
            print("4. background services"); start_services()
        if cfg.get("handoff_thread") == a.thread:
            print("5. first message already sent to this chat earlier (not repeated)")
        else:
            body = (f"Hello {a.partner_name}, this is {a.claude_name}. We now share a mailbox so we can work as a team. "
                    f"Rules: {os.path.join(ROOT, 'AGENTS.md')} (please read it once). Every message reaches you as a wake in this chat. Reply with: "
                    f"{team_cmd()} send --as {a.partner_name} --to {a.claude_name} --type ANSWER --re <id> --subject '...' --body '...'. "
                    f"During long work, check {team_cmd()} inbox --as {a.partner_name} between steps. Please ANSWER this message to prove the link works.")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", a.claude_name, "--to", a.partner_name,
                                "--type", "HANDOFF", "--re", "none", "--subject", "Team link is live: please read the rules and answer", "--body", body],
                               capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.startswith("sent "):
                raise SetupError("the first message could not be sent: " + (r.stdout + r.stderr).strip()[-300:])
            cfg["handoff_thread"] = a.thread; put_json(CONFIG, cfg)
            print("5. first message to the partner:", r.stdout.strip())
    except (SetupError, OSError, subprocess.SubprocessError) as e:
        print(f"\nSETUP FAILED: {e}\nPutting everything back as it was ...")
        if not a.no_services:
            remove_services()
        for path, data in keep.items():
            restore_file(path, data)
        bad = []
        if not a.no_services:
            for n, data in had_services.items():   # put back each service file, and its running/stopped state
                p = os.path.join(AGENTS, f"com.claudecodexteam.{n}.plist")
                if data is not None:
                    with open(p, "wb") as f:
                        f.write(data)
                if was_running.get(n):
                    subprocess.run([LAUNCHCTL, "load", p], capture_output=True)
                    for _ in range(20):
                        if service_pid(f"com.claudecodexteam.{n}"):
                            break
                        time.sleep(0.5)
                    else:
                        bad.append(n)
        if bad:
            sys.exit(f"Rolled back config and Claude settings, but could NOT restart: {', '.join(bad)}. Show this output to Claude.")
        sys.exit("Rolled back: config, Claude settings and services are as before. Show this output to Claude.")
    off = os.path.join(DATA, "hooks", "OFF")
    if os.path.exists(off) and open(off).read().strip() == "uninstall":   # a manual OFF is yours: it stays
        os.remove(off); print("  hooks re-armed (the earlier uninstall had turned them off)")
    elif os.path.exists(off):
        print("  NOTE: hooks stay OFF because team-data/hooks/OFF exists (you created it). Delete it to turn them on.")
    print(f"\nDone. Restart Claude Code in {ROOT} so the hooks load, then check the link with:\n"
          f"  python3 {shlex.quote(os.path.join(ROOT, 'team', 'commhealth.py'))} once")


if __name__ == "__main__":
    main()
