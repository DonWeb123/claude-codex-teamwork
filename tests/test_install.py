#!/usr/bin/env python3
"""Isolated install/uninstall regression tests: fake home folder, fake Codex CLI and a fake launchctl that really starts
and stops the two daemons. Your real settings, services and Codex are never touched. Exit 0 = all passed."""
import json, os, signal, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="teamkit-install-")
HOME = os.path.join(T, "home"); os.makedirs(os.path.join(HOME, ".claude")); os.makedirs(os.path.join(HOME, "Library", "LaunchAgents"))
CH = os.path.join(HOME, ".codex"); os.makedirs(os.path.join(CH, "sessions", "2026", "01", "01"))
A, B = "aaaaaaaa-0000-4000-8000-00000000000a", "bbbbbbbb-0000-4000-8000-00000000000b"
with open(os.path.join(CH, "session_index.jsonl"), "w") as f:
    for i, n in ((A, "Partner A"), (B, "Partner B")):
        f.write(json.dumps({"id": i, "thread_name": n, "updated_at": "2026-01-01T00:00:00Z"}) + "\n")
CALLS = os.path.join(T, "codex_calls.jsonl")
CODEX = os.path.join(T, "codex")
open(CODEX, "w").write(f"#!{sys.executable}\nimport json,sys\nopen({CALLS!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\nprint('queued 11111111-2222-3333-4444-555555555555')\n")
STATE = os.path.join(T, "launchd"); os.makedirs(STATE)
LCTL = os.path.join(T, "launchctl")
open(LCTL, "w").write(f'''#!{sys.executable}
import json, os, plistlib, signal, subprocess, sys
S = {STATE!r}; cmd, arg = sys.argv[1], sys.argv[2]
F = os.path.join(S, "FAIL_START")
if cmd == "load" and os.path.exists(F):   # pretend success but start nothing, for the next N loads only
    n = int(open(F).read() or 1) - 1
    os.remove(F) if n <= 0 else open(F, "w").write(str(n))
    sys.exit(0)
if cmd == "load":
    d = plistlib.load(open(arg, "rb"))
    p = subprocess.Popen(d["ProgramArguments"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    open(os.path.join(S, d["Label"]), "w").write(str(p.pid))
elif cmd == "unload":
    d = plistlib.load(open(arg, "rb")); f = os.path.join(S, d["Label"])
    if os.path.exists(f):
        try: os.kill(int(open(f).read()), signal.SIGTERM)
        except ProcessLookupError: pass
        os.remove(f)
elif cmd == "list":
    f = os.path.join(S, arg)
    if not os.path.exists(f): sys.exit(113)
    pid = int(open(f).read())
    try: os.kill(pid, 0); print('{{\\n\\t"PID" = %d;\\n}};' % pid)
    except ProcessLookupError: print("{{}}")
''')
os.chmod(CODEX, 0o755); os.chmod(LCTL, 0o755)
DATA, CONFIG = os.path.join(T, "data"), os.path.join(T, "config.json")
ENV = dict(os.environ, HOME=HOME, TEAMKIT_DATA=DATA, TEAMKIT_CONFIG=CONFIG, TEAMKIT_CODEX=CODEX, TEAMKIT_CODEX_HOME=CH, TEAMKIT_LAUNCHCTL=LCTL)
SETTINGS = os.path.join(HOME, ".claude", "settings.json")
RES = []


def check(name, ok, detail=""):
    ok = bool(ok); RES.append(ok); print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def run(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True, env=ENV, cwd=ROOT, timeout=120)
    return r.returncode, r.stdout + r.stderr


def install(thread, *extra):
    return run(os.path.join(ROOT, "install.py"), "--thread", thread, "--partner-name", "astra", "--skip-tests", *extra)


def cfg():
    return json.load(open(CONFIG)) if os.path.exists(CONFIG) else None


def pid_of(label):
    f = os.path.join(STATE, label)
    return int(open(f).read()) if os.path.exists(f) else None


def alive(pid):
    try:
        os.kill(pid, 0); return True
    except (ProcessLookupError, TypeError):
        return False


def calls():
    return [json.loads(l) for l in open(CALLS)] if os.path.exists(CALLS) else []


try:
    open(SETTINGS, "w").write(json.dumps({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo other"}]}]}}))
    rc, out = install("not-a-chat")
    check("unknown chat id refused before any change", rc != 0 and cfg() is None, out[-200:])
    good = open(SETTINGS).read()
    open(SETTINGS, "w").write(json.dumps({"hooks": {"Stop": [{"hooks": ["not-an-object"]}]}}))
    rc, out = install(A)
    check("malformed nested hook refused, nothing changed", rc != 0 and cfg() is None and "unexpected shape" in out, out[-200:])
    open(SETTINGS, "w").write(good)
    rc, out = install(A)
    pid = pid_of("com.claudecodexteam.wakerd")
    check("fresh install: both services running our program", rc == 0 and alive(pid) and alive(pid_of("com.claudecodexteam.commhealth")), out[-400:])
    check("fresh install: heartbeat came from the launched daemon", "from the launched pid" in out, out[-300:])
    time.sleep(1.5)
    check("fresh install: first message woke chat A", any(A in c for c in calls()), calls())
    # start-up failure while switching to chat B -> everything back on chat A
    before = open(SETTINGS, "rb").read(); open(os.path.join(STATE, "FAIL_START"), "w").write("1")
    rc, out = install(B)
    check("failed switch: rolled back, mail still routed to chat A", rc != 0 and "Rolled back" in out and cfg()["codex_thread"] == A, out[-300:])
    check("failed switch: Claude settings byte-identical", open(SETTINGS, "rb").read() == before)
    check("failed switch: previous services restarted", alive(pid_of("com.claudecodexteam.wakerd")), out[-200:])
    # handoff send failure -> rollback
    os.chmod(os.path.join(DATA, "msgs"), 0o500)
    rc, out = install(B)
    os.chmod(os.path.join(DATA, "msgs"), 0o700)
    check("failed first message: rolled back to chat A", rc != 0 and cfg()["codex_thread"] == A, out[-300:])
    # unread mail to the partner, then a successful switch: B must not get A's old mail
    subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", "claude", "--to", "astra", "--type", "FYI", "--re", "none",
                    "--subject", "OLD-FOR-A", "--body", "old note"], env=ENV, capture_output=True)
    time.sleep(1.5); n0 = len(calls())
    rc, out = install(B)
    time.sleep(2.0)
    newB = [c for c in calls()[n0:] if B in c]
    check("switch to chat B succeeded with a fresh first message", rc == 0 and cfg()["codex_thread"] == B and newB, out[-300:])
    check("switch to chat B: old unread mail not replayed", newB and not any("OLD-FOR-A" in " ".join(c) for c in newB), newB)
    rc, out = install(B)
    check("reinstall on the same chat: first message not repeated", rc == 0 and "not repeated" in out, out[-200:])
    # uninstall
    s = json.load(open(SETTINGS)); s["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": f"python3 {ROOT}/team/hooks/idle_wake.py --someone-elses"}]})
    open(SETTINGS, "w").write(json.dumps(s))
    p1, p2 = pid_of("com.claudecodexteam.wakerd"), pid_of("com.claudecodexteam.commhealth")
    rc, out = run(os.path.join(ROOT, "uninstall.py"))
    time.sleep(0.5)
    left = json.load(open(SETTINGS))["hooks"]
    cmds = [h["command"] for g in left.values() for x in g for h in x["hooks"]]
    check("uninstall: services stopped before their plists were deleted", rc == 0 and not alive(p1) and not alive(p2)
          and not os.listdir(os.path.join(HOME, "Library", "LaunchAgents")), out[-300:])
    check("uninstall: exactly our hooks removed, look-alike and other hooks kept", sorted(cmds) == sorted(["echo other", f"python3 {ROOT}/team/hooks/idle_wake.py --someone-elses"]), cmds)
    check("uninstall: hooks disarmed", os.path.exists(os.path.join(DATA, "hooks", "OFF")))
    open(SETTINGS, "w").write("{broken")
    rc, out = run(os.path.join(ROOT, "uninstall.py"))
    check("uninstall: broken settings stop it before any change", rc != 0 and open(SETTINGS).read() == "{broken", out[-200:])
finally:
    for f in os.listdir(STATE):
        try:
            os.kill(int(open(os.path.join(STATE, f)).read()), signal.SIGTERM)
        except Exception:
            pass
n = sum(RES); print(f"\n{n}/{len(RES)} install tests passed  (temp folder: {T})")
sys.exit(0 if n == len(RES) else 1)
