"""Keep the two background services (wake daemon, health monitor) running across logins, on each platform.

  launchd     macOS: ~/Library/LaunchAgents/com.claudecodexteam.<name>.plist (KeepAlive)
  winstartup  Windows: a .cmd in the user's Startup folder (no admin rights needed) + started right away, windowless
  manual      Linux/other: prints the commands to run; stop is still verified through the pid files
Every running service is identified by its pid AND its process start time AND its command line, so a reused pid is never
mistaken for ours. Overrides for tests: TEAMKIT_SERVICE_BACKEND, TEAMKIT_LAUNCHCTL, TEAMKIT_STARTUP_DIR."""
import os, plistlib, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compat
from common import DATA, ROOT, get_json

NAMES = {"wakerd": "team/wakerd.py", "commhealth": "team/commhealth.py"}
AGENTS = os.environ.get("TEAMKIT_LAUNCH_AGENTS") or os.path.expanduser("~/Library/LaunchAgents")
STARTUP = os.environ.get("TEAMKIT_STARTUP_DIR") or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                                                                  "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
_lc = os.environ.get("TEAMKIT_LAUNCHCTL", "launchctl")
LAUNCHCTL = [sys.executable, _lc] if _lc.endswith(".py") else [_lc]


def backend():
    b = os.environ.get("TEAMKIT_SERVICE_BACKEND")
    if b:
        return b
    if "TEAMKIT_LAUNCHCTL" in os.environ:
        return "launchd"
    if os.environ.get("TEAMKIT_NO_LAUNCHD") == "1":
        return "manual"
    return "launchd" if sys.platform == "darwin" else "winstartup" if compat.WINDOWS else "manual"


def script(n):
    return os.path.join(ROOT, *NAMES[n].split("/"))


def label(n):
    return f"com.claudecodexteam.{n}"


def unit_file(n):
    """the file that makes the service start at login (None for manual)"""
    b = backend()
    if b == "launchd":
        return os.path.join(AGENTS, label(n) + ".plist")
    if b == "winstartup":
        return os.path.join(STARTUP, f"claude-codex-team-{n}.cmd")
    return None


def unit_bytes(n):
    b = backend()
    if b == "launchd":
        log = os.path.join(DATA, "logs", f"{n}.log")
        return plistlib.dumps({"Label": label(n), "ProgramArguments": [sys.executable, script(n), "run"], "WorkingDirectory": ROOT,
                               "RunAtLoad": True, "KeepAlive": True, "StandardOutPath": log, "StandardErrorPath": log})
    if b == "winstartup":
        return (f'@echo off\r\nrem claude-codex-team service (owner: {script(n)})\r\n'
                f'start "" /B "{compat.python_exe(windowless=True)}" "{script(n)}" run\r\n').encode()
    return None


def owned(n):
    """True if there is no unit file, or it is THIS copy's; False = another copy or program owns that name"""
    p = unit_file(n)
    if not p or not os.path.exists(p):
        return True
    try:
        if backend() == "launchd":
            with open(p, "rb") as f:
                d = plistlib.load(f)
            return d.get("Label") == label(n) and (d.get("ProgramArguments") or [None, None])[1:2] == [script(n)]
        return f"(owner: {script(n)})" in open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return False


def _launchd_pid(n):
    out = subprocess.run(LAUNCHCTL + ["list", label(n)], capture_output=True, text=True).stdout
    pid = next((l.split("=")[1].strip(" ;") for l in out.splitlines() if '"PID"' in l), None)
    return int(pid) if pid and pid.isdigit() else None


UNKNOWN = -1   # the process table could not be inspected


def running_pid(n):
    """pid of OUR running service n, verified by command line (and start time when recorded); None = not running;
    UNKNOWN = cannot tell (inspection failed) - callers must never treat that as stopped"""
    if backend() == "launchd":
        pid = _launchd_pid(n)
        try:
            info = compat.proc_info(pid) if pid else None
        except OSError:
            return UNKNOWN
        return pid if info and compat.args_has(info["args"], script(n)) else None
    rec = get_json(os.path.join(DATA, "wake", f"{n}.pid.json")) or {}
    if not isinstance(rec.get("pid"), int):
        return None
    try:
        info = compat.proc_info(rec["pid"])
    except OSError:
        return UNKNOWN
    if info and compat.args_has(info["args"], script(n)) and info["start"] == rec.get("start"):
        return rec["pid"]
    return None


def stop(n):
    """stop OUR service n and wait until its process is really gone. True = stopped (or was not running)"""
    pid = running_pid(n)
    if pid == UNKNOWN:
        return False   # cannot verify: report incomplete, keep the auto-start file
    if backend() == "launchd":
        p = unit_file(n)
        if os.path.exists(p):
            subprocess.run(LAUNCHCTL + ["unload", p], capture_output=True)
    elif pid:
        compat.kill(pid)
    end = time.time() + 15   # a time limit, not a count: on Windows every process check starts PowerShell (1-2 s)
    while True:
        if (pid is None or compat.gone(pid)) and (backend() != "launchd" or _launchd_pid(n) is None):
            return True
        if time.time() > end:
            return False
        time.sleep(0.25)


def _spawn(n):
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL, "cwd": ROOT}
    if compat.WINDOWS:
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([compat.python_exe(windowless=True), script(n), "run"], **kw).pid


def start(n):
    """install the unit file and start the service now; returns the verified pid or raises RuntimeError"""
    b = backend(); t0 = time.time()
    if b == "manual":
        raise RuntimeError("manual backend: start it yourself")
    p = unit_file(n); os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(unit_bytes(n))
    if b == "launchd":
        r = subprocess.run(LAUNCHCTL + ["load", p], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"{label(n)} failed to load: {r.stderr.strip()}")
    else:
        _spawn(n)
    end = time.time() + 20
    while time.time() < end:
        pid = running_pid(n)
        if pid and pid != UNKNOWN and (b == "launchd" or (get_json(os.path.join(DATA, "wake", f"{n}.pid.json")) or {}).get("t", t0) >= t0 - 1):
            return pid
        time.sleep(0.25)
    raise RuntimeError(f"{n} is not running our program (see {os.path.join(DATA, 'logs', n + '.log')})")


def remove(n):
    """stop, then delete the unit file. True = done"""
    if not stop(n):
        return False
    p = unit_file(n)
    if p and os.path.exists(p):
        os.remove(p)
    return True


def manual_commands():
    return [f'nohup {compat.q(sys.executable)} {compat.q(script(n))} run >/dev/null 2>&1 &' for n in NAMES]
