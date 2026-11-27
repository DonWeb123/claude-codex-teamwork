#!/usr/bin/env python3
"""Isolated install/uninstall regression tests: fake home folder, fake Codex CLI and a fake launchctl that really starts
and stops the two daemons. Your real settings, services and Codex are never touched. Exit 0 = all passed."""
import json, os, signal, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "team"))
import compat  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="teamkit-install-")
HOME = os.path.join(T, "home"); os.makedirs(os.path.join(HOME, ".claude")); os.makedirs(os.path.join(HOME, "Library", "LaunchAgents"))
CH = os.path.join(HOME, ".codex"); os.makedirs(os.path.join(CH, "sessions", "2026", "01", "01"))
A, B = "aaaaaaaa-0000-4000-8000-00000000000a", "bbbbbbbb-0000-4000-8000-00000000000b"
with open(os.path.join(CH, "session_index.jsonl"), "w") as f:
    for i, n in ((A, "Partner A"), (B, "Partner B")):
        f.write(json.dumps({"id": i, "thread_name": n, "updated_at": "2026-01-01T00:00:00Z"}) + "\n")
CALLS = os.path.join(T, "codex_calls.jsonl")
CODEX = os.path.join(T, "codex.py")
open(CODEX, "w").write(f"#!{sys.executable}\nimport json,sys\nopen({CALLS!r},'a').write(json.dumps(sys.argv[1:])+'\\n')\nprint('queued 11111111-2222-3333-4444-555555555555')\n")
STATE = os.path.join(T, "launchd"); os.makedirs(STATE)
LCTL = os.path.join(T, "launchctl.py")
open(LCTL, "w").write(f'''#!{sys.executable}
import json, os, plistlib, signal, subprocess, sys
sys.path.insert(0, {os.path.join(ROOT, "team")!r}); import compat
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
elif cmd == "unload" and os.path.exists(os.path.join(S, "STUCK")):
    pass   # pretend to unload but leave the process running
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
    print('{{\\n\\t"PID" = %d;\\n}};' % pid if not compat.gone(pid) else "{{}}")
''')
os.chmod(CODEX, 0o755); os.chmod(LCTL, 0o755)
DATA, CONFIG = os.path.join(T, "data"), os.path.join(T, "config.json")
ENV = dict(os.environ, HOME=HOME, USERPROFILE=HOME, TEAMKIT_CLAUDE_SETTINGS=os.path.join(HOME, ".claude", "settings.json"),
           TEAMKIT_LAUNCH_AGENTS=os.path.join(HOME, "Library", "LaunchAgents"), TEAMKIT_DATA=DATA, TEAMKIT_CONFIG=CONFIG, TEAMKIT_CODEX=CODEX, TEAMKIT_CODEX_HOME=CH, TEAMKIT_LAUNCHCTL=LCTL)
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
    return isinstance(pid, int) and not compat.gone(pid)   # never os.kill(pid, 0): on Windows that sends Ctrl+C


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
    legacy = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f"python3 {os.path.join(ROOT, 'team', 'hooks', 'idle_wake.py')}"}]},
                                 {"hooks": [{"type": "command", "command": "echo other"}]}]}}
    good = json.dumps(legacy); open(SETTINGS, "w").write(good)
    # first install whose first message cannot be sent -> full rollback to "never installed"
    blocker = os.path.join(DATA, "msgs", "M0001.json")   # a folder where the first message must go: the send fails on every OS
    os.makedirs(blocker)
    rc, out = install(A)
    os.rmdir(blocker)
    check("failed first install: rolled back to never-installed", rc != 0 and cfg() is None and open(SETTINGS).read() == good
          and not os.listdir(os.path.join(HOME, "Library", "LaunchAgents")), out[-300:])
    rc, out = install(A)
    pid = pid_of("com.claudecodexteam.wakerd")
    check("fresh install: both services running our program", rc == 0 and alive(pid) and alive(pid_of("com.claudecodexteam.commhealth")), out[-400:])
    check("fresh install: heartbeat came from the launched daemon", "from the started pid" in out, out[-300:])
    hooks = json.load(open(SETTINGS))["hooks"]
    flat = [h for g in hooks.values() for x in g for h in x["hooks"]]
    check("hooks use exec form (python + script path, no shell) and the old shell-form hook was migrated away",
          any(h.get("command") == sys.executable and h.get("args") == [os.path.join(ROOT, "team", "hooks", "idle_wake.py")] for h in flat)
          and not any(h.get("command", "").startswith("python3 ") for h in flat) and any(h.get("command") == "echo other" for h in flat), flat)
    bks = os.listdir(os.path.join(HOME, ".claude", "teamkit-backups"))
    check("settings backups live next to the settings (user profile), not in the kit folder",
          bks and not [f for f in os.listdir(DATA) if f.startswith("settings.backup")], bks)
    time.sleep(1.5)
    check("fresh install: first message woke chat A", any(A in c for c in calls()), calls())
    # start-up failure on a reinstall -> services back to RUNNING, settings byte-identical
    before = open(SETTINGS, "rb").read(); open(os.path.join(STATE, "FAIL_START"), "w").write("1")
    rc, out = install(A)
    check("failed reinstall: rolled back", rc != 0 and "Rolled back" in out and cfg()["codex_thread"] == A, out[-300:])
    check("failed reinstall: Claude settings byte-identical", open(SETTINGS, "rb").read() == before)
    check("failed reinstall: services restored to their earlier running state", alive(pid_of("com.claudecodexteam.wakerd")), out[-200:])
    # pairing a different chat is refused: a new chat can never read this mailbox
    rc, out = install(B)
    check("pairing a different chat refused, nothing changed", rc != 0 and "fresh copy" in out and cfg()["codex_thread"] == A
          and not any(B in c for c in calls()), out[-200:])
    cb = open(CONFIG).read(); os.remove(CONFIG)
    rc, out = install(B)
    check("config.json lost: pairing a different chat still refused", rc != 0 and "fresh copy" in out and not any(B in c for c in calls()), out[-200:])
    open(CONFIG, "w").write("{broken")
    rc, out = install(B)
    check("config.json damaged: pairing a different chat still refused", rc != 0 and not any(B in c for c in calls()), out[-200:])
    open(CONFIG, "w").write(cb)
    bb = open(os.path.join(DATA, "binding.json")).read(); open(os.path.join(DATA, "binding.json"), "w").write("{}")
    rc, out = install(B)
    check("empty binding: pairing a different chat refused", rc != 0 and not any(B in c for c in calls()), out[-200:])
    open(os.path.join(DATA, "binding.json"), "w").write(bb)
    c = json.load(open(CONFIG)); c["codex_thread"] = B; json.dump(c, open(CONFIG, "w"))
    n1 = len(calls())
    subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", "claude", "--to", "astra", "--type", "FYI", "--re", "none",
                    "--subject", "ROUTE-TEST", "--body", "x"], env=ENV, capture_output=True)
    time.sleep(1.5)
    check("config edited to chat B: the wake daemon still refuses to deliver to B", not any(B in c for c in calls()[n1:])
          and "disagrees with this mailbox's binding on codex_thread" in open(os.path.join(DATA, "wake", "alerts.jsonl")).read())
    open(CONFIG, "w").write(cb)
    c = json.load(open(CONFIG)); c["partner_name"] = "renamed"; json.dump(c, open(CONFIG, "w"))
    n2 = len(calls())
    subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", "claude", "--to", "astra", "--type", "FYI", "--re", "none",
                    "--subject", "DRIFT-TEST", "--body", "x"], env=ENV, capture_output=True)
    time.sleep(1.5)
    check("names edited in config.json: delivery stops with an alert instead of guessing", not calls()[n2:]
          and "disagrees with this mailbox's binding" in open(os.path.join(DATA, "wake", "alerts.jsonl")).read(), calls()[n2:])
    open(CONFIG, "w").write(cb)
    rc, out = run(os.path.join(ROOT, "install.py"), "--thread", A, "--partner-name", "renamed", "--skip-tests")
    check("renaming the partner on the same chat refused", rc != 0 and "names are fixed" in out, out[-200:])
    rc, out = install(A)
    check("reinstall on the same chat: first message not repeated", rc == 0 and "not repeated" in out, out[-200:])
    # the Python path changed since the last install (upgrade / different interpreter): no duplicates, old ones migrated
    st = json.load(open(SETTINGS))
    for g in st["hooks"].values():
        for x in g:
            for h in x["hooks"]:
                if h.get("args"):
                    h["command"] = "/old/python/bin/python3"
    REG = os.path.join(DATA, "hooks", "registered.json")   # what that earlier install recorded: the old Python path
    reg = json.load(open(REG)); [r.update(command="/old/python/bin/python3") for r in reg]; open(REG, "w").write(json.dumps(reg))
    checker = {"type": "command", "command": "/usr/local/bin/some-checker", "args": [os.path.join(ROOT, "team", "hooks", "idle_wake.py")]}
    st["hooks"]["Stop"].append({"hooks": [checker]})   # another tool that runs our script as its argument: never ours
    open(SETTINGS, "w").write(json.dumps(st))
    rc, out = install(A)
    flat = [h for g in json.load(open(SETTINGS))["hooks"].values() for x in g for h in x["hooks"] if h.get("args") and h != checker]
    check("changed Python path: recorded hooks replaced exactly, not duplicated", rc == 0 and len(flat) == 3 and all(h["command"] == sys.executable for h in flat), flat)
    check("an unrelated checker running the same script is kept", any(h == checker for g in json.load(open(SETTINGS))["hooks"].values() for x in g for h in x["hooks"]))
    # a settings file more private than its folder keeps exactly its protection (and so does the backup)
    bdir = os.path.join(HOME, ".claude", "teamkit-backups"); before_bk = set(os.listdir(bdir))
    if compat.WINDOWS:
        subprocess.run(["icacls", SETTINGS, "/inheritance:r", "/grant:r", f"{os.environ.get('USERNAME')}:(F)"], capture_output=True)
        want = compat.acl_rules(SETTINGS)
    else:
        os.chmod(SETTINGS, 0o640); want = 0o640
    rc, out = install(A)
    new_bk = [os.path.join(bdir, f) for f in set(os.listdir(bdir)) - before_bk]
    got = compat.acl_rules(SETTINGS) if compat.WINDOWS else (os.stat(SETTINGS).st_mode & 0o777)
    got_bk = [compat.acl_rules(f) if compat.WINDOWS else (os.stat(f).st_mode & 0o777) for f in new_bk]
    check("restricted settings file keeps its exact protection after rewrite, and its backup gets the same",
          rc == 0 and want and got == want and got_bk and all(x == want for x in got_bk), (want, got, got_bk, out[-200:]))
    open(os.path.join(DATA, "hooks", "OFF"), "w").close()
    rc, out = install(A)
    check("a manual OFF switch survives a reinstall", rc == 0 and os.path.exists(os.path.join(DATA, "hooks", "OFF")), out[-200:])
    os.remove(os.path.join(DATA, "hooks", "OFF"))
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
    check("uninstall: exactly our hooks removed, look-alike and other hooks kept", sorted(cmds) == sorted(["echo other", "/usr/local/bin/some-checker", f"python3 {ROOT}/team/hooks/idle_wake.py --someone-elses"]), cmds)
    check("uninstall: hooks disarmed", os.path.exists(os.path.join(DATA, "hooks", "OFF")))
    rc, out = install(A)
    check("install after uninstall re-arms the hooks it disarmed", rc == 0 and not os.path.exists(os.path.join(DATA, "hooks", "OFF")), out[-200:])
    open(os.path.join(STATE, "STUCK"), "w").close()
    rc, out = run(os.path.join(ROOT, "uninstall.py"))
    os.remove(os.path.join(STATE, "STUCK"))
    check("uninstall reports a service that will not stop (non-zero exit, plist kept)", rc != 0 and "INCOMPLETE" in out
          and os.listdir(os.path.join(HOME, "Library", "LaunchAgents")), out[-300:])
    rc, out = run(os.path.join(ROOT, "uninstall.py"))
    open(SETTINGS, "w").write("{broken")
    rc, out = run(os.path.join(ROOT, "uninstall.py"))
    check("uninstall: broken settings stop it before any change", rc != 0 and open(SETTINGS).read() == "{broken", out[-200:])
    # Linux-style machine: no launchctl at all -> manual start instructions, no crash
    open(SETTINGS, "w").write(good)
    E2 = dict(ENV, TEAMKIT_DATA=os.path.join(T, "data-linux"), TEAMKIT_CONFIG=os.path.join(T, "config-linux.json"), TEAMKIT_NO_LAUNCHD="1")
    E2.pop("TEAMKIT_LAUNCHCTL")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), "--thread", A, "--partner-name", "astra", "--skip-tests"],
                       capture_output=True, text=True, env=E2, cwd=ROOT, timeout=60)
    check("no launchctl (Linux): setup completes with manual start commands", r.returncode == 0 and "nohup" in r.stdout, (r.stdout + r.stderr)[-300:])
    json.dump({"claude_name": "claude", "partner_name": "astra", "codex_thread": A, "work_dirs": [ROOT]}, open(E2["TEAMKIT_CONFIG"], "w"))
    procs = [subprocess.Popen([sys.executable, os.path.join(ROOT, sc), "run"], env=E2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for sc in ("team/wakerd.py", "team/commhealth.py")]
    time.sleep(1.5)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "uninstall.py")], capture_output=True, text=True, env=E2, cwd=ROOT, timeout=60)
    time.sleep(0.3)
    check("no launchctl (Linux): uninstall stops manually started daemons and says so", r.returncode == 0 and r.stdout.count("stopped") >= 2
          and all(p.poll() is not None for p in procs), (r.stdout, [p.poll() for p in procs]))
    for p in procs:
        if p.poll() is None:
            p.kill()
    # a second health monitor is refused while one runs (Linux-style manual start twice)
    first = subprocess.Popen([sys.executable, os.path.join(ROOT, "team", "commhealth.py"), "run"], env=E2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    second = subprocess.run([sys.executable, os.path.join(ROOT, "team", "commhealth.py"), "run"], env=E2, capture_output=True, text=True, timeout=30)
    check("a second health monitor refuses to start", second.returncode != 0 and "already running" in second.stderr, second.stderr[-200:])
    r = subprocess.run([sys.executable, os.path.join(ROOT, "uninstall.py")], capture_output=True, text=True, env=E2, cwd=ROOT, timeout=60)
    time.sleep(0.3)
    check("...and uninstall stops the one that runs", r.returncode == 0 and first.poll() is not None, r.stdout[-200:])
    if first.poll() is None:
        first.kill()
    # Windows back-end (Startup folder + windowless start), exercised on every OS
    SD = os.path.join(T, "startup")
    E3 = dict(ENV, TEAMKIT_DATA=os.path.join(T, "data-win"), TEAMKIT_CONFIG=os.path.join(T, "config-win.json"),
              TEAMKIT_SERVICE_BACKEND="winstartup", TEAMKIT_STARTUP_DIR=SD)
    E3.pop("TEAMKIT_LAUNCHCTL")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "install.py"), "--thread", A, "--partner-name", "astra", "--skip-tests"],
                       capture_output=True, text=True, env=E3, cwd=ROOT, timeout=120)
    recs = [json.load(open(os.path.join(T, "data-win", "wake", f"{n}.pid.json"))) for n in ("wakerd", "commhealth")
            if os.path.exists(os.path.join(T, "data-win", "wake", f"{n}.pid.json"))]
    check("Windows back-end: setup starts both services and writes their Startup files",
          r.returncode == 0 and len(recs) == 2 and all(alive(x["pid"]) for x in recs) and len(os.listdir(SD)) == 2
          and "from the started pid" in r.stdout, (r.stdout + r.stderr)[-400:])
    r = subprocess.run([sys.executable, os.path.join(ROOT, "uninstall.py")], capture_output=True, text=True,
                       env=dict(E3, TEAMKIT_SIMULATE_PS_FAILURE="1"), cwd=ROOT, timeout=120)
    check("Windows back-end: unknown process state -> uninstall reports incomplete and keeps the Startup files",
          r.returncode != 0 and "INCOMPLETE" in (r.stdout + r.stderr) and len(os.listdir(SD)) == 2 and all(alive(x["pid"]) for x in recs),
          (r.stdout + r.stderr)[-300:])
    r = subprocess.run([sys.executable, os.path.join(ROOT, "uninstall.py")], capture_output=True, text=True, env=E3, cwd=ROOT, timeout=120)
    time.sleep(0.5)
    check("Windows back-end: uninstall stops both and removes the Startup files",
          r.returncode == 0 and not any(alive(x["pid"]) for x in recs) and not os.listdir(SD), (r.stdout + r.stderr)[-400:])
finally:
    for n in ("wakerd", "commhealth"):
        x = os.path.join(T, "data-win", "wake", f"{n}.pid.json")
        if os.path.exists(x):
            try:
                os.kill(json.load(open(x))["pid"], signal.SIGTERM)
            except Exception:
                pass
    for f in os.listdir(STATE):
        try:
            os.kill(int(open(os.path.join(STATE, f)).read()), signal.SIGTERM)
        except Exception:
            pass
n = sum(RES); print(f"\n{n}/{len(RES)} install tests passed  (temp folder: {T})")
sys.exit(0 if n == len(RES) else 1)
