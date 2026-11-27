#!/usr/bin/env python3
"""Undo install.py, in a safe order:
  1. check ~/.claude/settings.json first (a broken file stops everything; nothing is changed)
  2. disarm Claude's hooks (team-data/hooks/OFF) and stop a waiting idle hook, verified by process identity
  3. stop this copy's services and wait until their processes are gone, THEN delete their plists (never another copy's)
  4. remove exactly this kit's hook commands (owner-only backup first)
Your messages in team-data/ are kept; delete the folder yourself if you want them gone."""
import os, signal, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import install  # noqa: E402
from common import DATA, get_json, put_json  # noqa: E402

try:
    s = install.load_settings()
except install.SetupError as e:
    sys.exit(str(e))
H = os.path.join(DATA, "hooks")
import fcntl  # noqa: E402
lk = open(os.path.join(DATA, "locks", "install.lock"), "w")
try:
    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit("another install or uninstall of this kit is running")
failed = []
off = os.path.join(H, "OFF")
if not os.path.exists(off):
    open(off, "w").write("uninstall")   # marks it as ours, so a later install may re-arm; a manual OFF is never touched
print("hooks disarmed (team-data/hooks/OFF)")
w = get_json(os.path.join(H, "waiter.json")) or {}
if isinstance(w.get("pid"), int):
    ps = lambda f: subprocess.run(["ps", "-ww", "-p", str(w["pid"]), "-o", f + "="], capture_output=True, text=True).stdout.strip()
    # only OUR waiter: same program AND same process start time as it recorded (a reused pid has a different start)
    if os.path.join(ROOT, "team", "hooks", "idle_wake.py") in ps("args") and w.get("start") and ps("lstart") == w["start"]:
        os.kill(w["pid"], signal.SIGTERM); print("stopped the waiting idle hook", w["pid"])
if install.HAS_LAUNCHD:
    for n, script in install.SERVICES.items():
        label = f"com.claudecodexteam.{n}"; p = os.path.join(install.AGENTS, label + ".plist")
        if not os.path.exists(p):
            continue
        if not install.plist_owned(label, script):
            print(f"left {p} alone: it belongs to another copy or program"); continue
        if install.stop_service(n):
            os.remove(p); print("stopped", n)
        else:
            failed.append(n); print(f"WARNING: {n} did not stop; its plist was kept: {p}")
else:   # no launchd (Linux): stop manually started daemons, verified by program AND start time
    import time as _t
    for n, script in install.SERVICES.items():
        r = get_json(os.path.join(DATA, "wake", f"{n}.pid.json")) or {}
        if not isinstance(r.get("pid"), int):
            continue
        ps = lambda f: subprocess.run(["ps", "-ww", "-p", str(r["pid"]), "-o", f + "="], capture_output=True, text=True).stdout.strip()
        if os.path.join(ROOT, script) not in ps("args") or ps("lstart") != r.get("start"):
            continue   # not running, or the pid now belongs to something else
        os.kill(r["pid"], signal.SIGTERM)
        for _ in range(20):
            if install.gone(r["pid"]):
                print("stopped", n); break
            _t.sleep(0.5)
        else:
            failed.append(n); print(f"WARNING: {n} (pid {r['pid']}) did not stop")
mine = {install.hook_cmd("idle_wake.py"), install.hook_cmd("midturn.py")}
if any(h.get("command") in mine for g in s.get("hooks", {}).values() for x in g for h in x.get("hooks", [])):
    install.backup_settings()
    for ev in list(s["hooks"]):
        groups = []
        for g in s["hooks"][ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if h.get("command") not in mine]   # exact match only
            if g["hooks"]:
                groups.append(g)
        if groups:
            s["hooks"][ev] = groups
        else:
            del s["hooks"][ev]
    put_json(install.SETTINGS, s); print("hooks removed from", install.SETTINGS)
if failed:
    sys.exit(f"UNINSTALL INCOMPLETE: {', '.join(failed)} still running. Show this output to Claude.")
print("uninstalled (team-data/ kept)")
