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
import argparse, json, os, re, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "team"))
from common import DATA, CONFIG, find_codex, codex_home, get_json, put_json, team_cmd, msgs, binding as valid_binding
import compat, services

SETTINGS = os.environ.get("TEAMKIT_CLAUDE_SETTINGS") or os.path.expanduser("~/.claude/settings.json")
SERVICES = services.NAMES
BINDING = os.path.join(DATA, "binding.json")   # which chat + names this mailbox belongs to; kept with the mail, not in config
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


def hook_path(name):
    return os.path.join(ROOT, "team", "hooks", name)


def hook_spec(name):
    """exec form: Claude Code starts python directly with the script as its argument - no shell involved, so it behaves the same
    on macOS, Linux and Windows (Git Bash or PowerShell), and folder names with spaces need no quoting"""
    return {"type": "command", "command": sys.executable, "args": [hook_path(name)]}


REGISTERED = os.path.join(DATA, "hooks", "registered.json")   # the exact hook entries this copy wrote into the settings


def recorded():
    r = get_json(REGISTERED, []) or []
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def is_ours(h, name):
    """exactly this kit's hook for `name`, and nothing else: an entry this copy recorded when it wrote it (so a later Python move
    is still migrated exactly), the entry it would write now, or the plain `python3 <script>` form of the first versions.
    Never a pattern: another tool that happens to run the same script (e.g. a checker) is left alone."""
    p = hook_path(name)
    if h in [x for x in recorded() if x.get("args") == [p] or p in str(x.get("command", ""))]:
        return True
    if h.get("args") is not None:
        return h == want_hook(name)
    import shlex
    return h.get("command") in {f"python3 {p}", f"python3 {shlex.quote(p)}"} and set(h) <= {"type", "command", "timeout", "asyncRewake"}


def ours_any(h):
    return is_ours(h, "idle_wake.py") or is_ours(h, "midturn.py")


def want_hook(name):
    return dict(hook_spec(name), timeout=21600, asyncRewake=True) if name == "idle_wake.py" else dict(hook_spec(name), timeout=10)


def hook_cmd(name):   # kept for older callers
    return hook_spec(name)["command"]


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
            and all(isinstance(h, dict) and isinstance(h.get("command", ""), str)
                    and (h.get("args") is None or (isinstance(h["args"], list) and all(isinstance(x, str) for x in h["args"])))
                    for h in g.get("hooks", []))
            for g in groups)
        for groups in H.values())
    if not ok:
        raise SetupError(f"{SETTINGS} has an unexpected shape (hooks -> lists of groups -> lists of hooks): fix it first. Nothing was changed.")
    return s


def protected_new(src, path):
    """create `path` EMPTY and exclusively, give it src's protection and verify that, and only then return - so no byte is
    ever written into a file that is less private than src. Removes it and raises SetupError on failure."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    if not compat.copy_protection(src, path):
        os.remove(path)
        raise SetupError(f"could not give {path} the same permissions as {src}: nothing was changed"
                         + (f" ({compat.LAST_ERROR})" if compat.LAST_ERROR else ""))


def fill(path, data):
    """write bytes into an existing (already protected) file; on failure the file is removed"""
    try:
        with open(path, "r+b") as f:
            f.write(data); f.truncate()
    except OSError:
        os.remove(path); raise


def backup_settings():
    """unique backup created BEFORE any byte is copied into it, kept NEXT TO the settings file (in your user profile, so it is
    exactly as private as the original on every system - never in the shareable kit folder); 0600 where the OS supports it"""
    if not os.path.exists(SETTINGS):
        return None, None
    with open(SETTINGS, "rb") as f:
        data = f.read()
    d = os.path.join(os.path.dirname(SETTINGS), "teamkit-backups"); os.makedirs(d, mode=0o700, exist_ok=True)
    b = os.path.join(d, f"settings.backup.{time.strftime('%Y%m%d-%H%M%S')}.{os.getpid()}.{os.urandom(3).hex()}.json")
    protected_new(SETTINGS, b)   # the backup is never less private than the original, not even for a moment
    fill(b, data)
    print("  backup of your Claude settings:", b)
    return b, data


def restore_file(path, data):
    """put a file back to its exact earlier bytes (None = it did not exist); when the file exists, its current protection
    (mode / Windows ACL) is carried over; if that fails, the bytes are written in place so the file keeps its own ACL"""
    if data is None:
        if os.path.exists(path):
            os.remove(path)
        return
    if os.path.exists(path) and os.path.abspath(path) == os.path.abspath(SETTINGS):   # only the user's own settings carry a protection to keep
        tmp = f"{path}.restore.{os.getpid()}.{os.urandom(3).hex()}"
        try:
            protected_new(path, tmp)
        except SetupError:
            tmp = None
        if tmp:
            fill(tmp, data); os.replace(tmp, path); return
        with open(path, "r+b") as f:   # in place: the file object (and its ACL) stays the same
            f.write(data); f.truncate()
        return
    tmp = f"{path}.restore.{os.getpid()}.{os.urandom(3).hex()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
    for ev in list(H):   # migrate: drop every earlier registration of this kit's hooks (old shell form, old Python path), keep everything else
        H[ev] = [dict(g, hooks=[x for x in g.get("hooks", []) if not ours_any(x)]) for g in H[ev]]
        H[ev] = [g for g in H[ev] if g["hooks"]]
    want = {"Stop": (want_hook("idle_wake.py"), None, "idle_wake.py"),
            "SessionStart": (want_hook("idle_wake.py"), None, "idle_wake.py"),
            "PostToolUse": (want_hook("midturn.py"), "*", "midturn.py")}
    for ev, (h, matcher, name) in want.items():
        groups = H.setdefault(ev, [])
        g = {"hooks": [h]}
        if matcher:
            g["matcher"] = matcher
        groups.append(g)
    put_json(REGISTERED, [want_hook("idle_wake.py"), want_hook("midturn.py")])   # recorded BEFORE the settings change
    write_settings(s); print("  hooks added to", SETTINGS)


def write_settings(obj):
    """atomic rewrite that keeps the settings file's own protection: POSIX mode bits, and on Windows its exact ACL (verified).
    Refuses (raises SetupError, file untouched) if that protection cannot be carried over."""
    if not os.path.exists(SETTINGS):
        put_json(SETTINGS, obj); return
    tmp = f"{SETTINGS}.teamkit.{os.getpid()}.{os.urandom(3).hex()}.tmp"
    protected_new(SETTINGS, tmp)   # protected and verified while still empty
    fill(tmp, json.dumps(obj, indent=2).encode())
    os.replace(tmp, SETTINGS)


def start_services():
    """(re)start both services and require a fresh heartbeat from the wake daemon we just started; raises SetupError"""
    if services.backend() == "manual":
        print("  this system has no supported auto-start: start these yourself and keep them running:")
        for c in services.manual_commands():
            print("    " + c)
        print("  to stop them later: python3 uninstall.py (it checks each one really exited)")
        return
    t0 = time.time(); pids = {}
    for n in SERVICES:
        if not services.stop(n):
            raise SetupError(f"the old {n} did not stop")
        try:
            pids[n] = services.start(n)
        except RuntimeError as e:
            raise SetupError(str(e))
        print(f"  service {n}: running (pid {pids[n]}, {services.backend()})")
    for _ in range(60):   # the heartbeat must come from the daemon we just started, after it started
        hb = get_json(os.path.join(DATA, "wake", "heartbeat.json")) or {}
        if hb.get("pid") == pids["wakerd"] and hb.get("t", 0) >= t0:
            print("  wake daemon heartbeat: OK (from the started pid)"); return
        time.sleep(0.25)
    raise SetupError("no fresh heartbeat from the started wake daemon after 15 s")


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
    try:   # one install/uninstall at a time
        lk = compat.lock(os.path.join(DATA, "locks", "install.lock"), blocking=False)  # noqa: F841
    except compat.Busy:
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
        bind = valid_binding() if os.path.exists(BINDING) else None
        has_mail = bool(msgs())
        if os.path.exists(BINDING) and bind is None:
            raise SetupError(f"{BINDING} is damaged, so this mailbox's partner cannot be verified. Use a fresh copy of the kit. Nothing was changed.")
        if bind is None and has_mail:
            raise SetupError("This mailbox already holds messages but no record of which chat they belong to. "
                             "Use a fresh copy of the kit. Nothing was changed.")
        if bind and bind.get("codex_thread") != a.thread:
            raise SetupError("This copy of the kit is already paired with another Codex chat. To pair a different chat, "
                             "unzip a fresh copy of the kit into a new folder and set that one up. Nothing was changed.")
        if bind and (bind.get("partner_name"), bind.get("claude_name")) != (a.partner_name, a.claude_name):
            raise SetupError(f"This copy's names are fixed: partner {bind.get('partner_name')!r}, Claude {bind.get('claude_name')!r} "
                             "(renaming would hide unanswered mail). Rerun with those names, or use a fresh copy. Nothing was changed.")
        load_settings()
        if not a.no_services:
            for n in SERVICES:
                if services.backend() != "manual" and services.running_pid(n) == services.UNKNOWN:
                    raise SetupError("could not inspect running processes, so services cannot be checked safely. Nothing was changed.")
                if not services.owned(n):
                    raise SetupError(f"{services.unit_file(n)} belongs to another copy of this kit: run that copy's uninstall.py first. Nothing was changed.")
    except SetupError as e:
        sys.exit(str(e))
    if not a.skip_tests:
        print("1. self-tests ...")
        if subprocess.run([sys.executable, os.path.join(ROOT, "tests", "run_tests.py")]).returncode != 0:
            sys.exit("Self-tests failed: nothing was installed or changed. Show this output to Claude.")
    work = [os.path.realpath(os.path.expanduser(d)) for d in (a.work_dir or [ROOT])]
    if os.path.realpath(ROOT) not in work:
        work.append(os.path.realpath(ROOT))
    # ---- transaction: remember the exact earlier state, roll back on any failure
    W = os.path.join(DATA, "wake")
    keep = {CONFIG: read_bytes(CONFIG), SETTINGS: read_bytes(SETTINGS),
            os.path.join(W, "baseline.json"): read_bytes(os.path.join(W, "baseline.json")),
            os.path.join(W, "partner_log.json"): read_bytes(os.path.join(W, "partner_log.json"))}
    use_svc = not a.no_services and services.backend() != "manual"
    had_services = {n: read_bytes(services.unit_file(n)) for n in SERVICES} if use_svc else {}
    was_running = {n: services.running_pid(n) is not None for n in SERVICES} if use_svc else {}
    keep[BINDING] = read_bytes(BINDING)
    keep[REGISTERED] = read_bytes(REGISTERED)
    old = get_json(CONFIG, {}) or {}
    cfg = {"claude_name": a.claude_name, "partner_name": a.partner_name, "codex_thread": a.thread, "work_dirs": work,
           "installed_at": old.get("installed_at") or time.time(),
           "handoff_thread": old.get("handoff_thread"),
           "install_id": os.urandom(6).hex()}   # a new id every install: team/verify_link.py then needs a fresh round trip
    try:
        if use_svc:
            for n in SERVICES:   # nothing may deliver while routing changes
                if not services.stop(n):
                    raise SetupError(f"could not stop the running {n}")
        ids = [int(m["id"][1:]) for m in msgs() if str(m.get("id", ""))[1:].isdigit()]
        if not os.path.exists(os.path.join(W, "baseline.json")):   # wake only for mail sent from now on (incl. the first message below)
            put_json(os.path.join(W, "baseline.json"), {"id": max(ids or [0]), "t": time.time(), "chat": a.thread})
        put_json(BINDING, {"codex_thread": a.thread, "partner_name": a.partner_name, "claude_name": a.claude_name})
        put_json(CONFIG, cfg); print("2. config written:", CONFIG)
        print("3. Claude hooks"); backup_settings(); add_hooks()
        if not a.no_services:
            print("4. background services"); start_services()
        if cfg.get("handoff_thread") == a.thread or (bind or {}).get("handoff_sent") is True:
            print("5. first message already sent to this chat earlier (not repeated)")
        else:
            body = (f"Hello {a.partner_name}, this is {a.claude_name}. We now share a mailbox so we can work as a team. "
                    f"Rules: {os.path.join(ROOT, 'AGENTS.md')} (please read it once). Every message reaches you as a wake in this chat. Reply with: "
                    f"{team_cmd(True)} send --as {a.partner_name} --to {a.claude_name} --type ANSWER --re <id> --subject '...' --body '...'. "
                    f"During long work, check {team_cmd(True)} inbox --as {a.partner_name} between steps. Please ANSWER this message to prove the link works.")
            r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", a.claude_name, "--to", a.partner_name,
                                "--type", "HANDOFF", "--re", "none", "--subject", "Team link is live: please read the rules and answer", "--body", body],
                               capture_output=True, text=True)
            if r.returncode != 0 or not r.stdout.startswith("sent "):
                raise SetupError("the first message could not be sent: " + (r.stdout + r.stderr).strip()[-300:])
            cfg["handoff_thread"] = a.thread; put_json(CONFIG, cfg)
            put_json(BINDING, dict(valid_binding() or {}, handoff_sent=True))
            print("5. first message to the partner:", r.stdout.strip())
    except (SetupError, OSError, subprocess.SubprocessError) as e:
        print(f"\nSETUP FAILED: {e}\nPutting everything back as it was ...")
        if use_svc:
            for n in SERVICES:
                services.remove(n)
        for path, data in keep.items():
            restore_file(path, data)
        bad = []
        if use_svc:
            for n, data in had_services.items():   # put back each service's unit file, and its running/stopped state
                if was_running.get(n):
                    try:
                        services.start(n)
                    except RuntimeError:
                        bad.append(n)
                p = services.unit_file(n)
                if data is not None:
                    with open(p, "wb") as f:
                        f.write(data)
                elif os.path.exists(p) and not was_running.get(n):
                    os.remove(p)
        if bad:
            sys.exit(f"Rolled back config and Claude settings, but could NOT restart: {', '.join(bad)}. Show this output to Claude.")
        sys.exit("Rolled back: config, Claude settings and services are as before. Show this output to Claude.")
    off = os.path.join(DATA, "hooks", "OFF")
    if os.path.exists(off) and open(off).read().strip() == "uninstall":   # a manual OFF is yours: it stays
        os.remove(off); print("  hooks re-armed (the earlier uninstall had turned them off)")
    elif os.path.exists(off):
        print("  NOTE: hooks stay OFF because team-data/hooks/OFF exists (you created it). Delete it to turn them on.")
    print(f"\nDone. Restart Claude Code in {ROOT} so the hooks load, then check the link with:\n"
          f"  {compat.q(sys.executable)} {compat.q(os.path.join(ROOT, 'team', 'commhealth.py'))} once")


if __name__ == "__main__":
    main()
