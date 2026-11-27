#!/usr/bin/env python3
"""Undo install.py, in a safe order (macOS, Linux and Windows):
  1. check ~/.claude/settings.json first (a broken file stops everything; nothing is changed)
  2. disarm Claude's hooks (team-data/hooks/OFF) and stop a waiting idle hook, verified by program AND start time
  3. stop this copy's services and wait until their processes are gone, THEN delete their auto-start files (never another copy's)
  4. remove exactly this kit's hook commands (owner-only backup first)
Exits non-zero if anything could not be stopped. Your messages in team-data/ are kept."""
import os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import install  # noqa: E402
import compat, services  # noqa: E402
from common import DATA, get_json, put_json  # noqa: E402

try:
    s = install.load_settings()
except install.SetupError as e:
    sys.exit(str(e))
try:
    lk = compat.lock(os.path.join(DATA, "locks", "install.lock"), blocking=False)
except compat.Busy:
    sys.exit("another install or uninstall of this kit is running")
failed = []
H = os.path.join(DATA, "hooks"); off = os.path.join(H, "OFF")
if not os.path.exists(off):
    open(off, "w").write("uninstall")   # marks it as ours, so a later install may re-arm; a manual OFF is never touched
print("hooks disarmed (team-data/hooks/OFF)")
w = get_json(os.path.join(H, "waiter.json")) or {}
if isinstance(w.get("pid"), int):
    try:
        info = compat.proc_info(w["pid"])
    except OSError:
        info = None
    # only OUR waiter: same program AND same start time as it recorded (a reused pid has a different start)
    if info and compat.args_has(info["args"], os.path.join(ROOT, "team", "hooks", "idle_wake.py")) and w.get("start") and info["start"] == w["start"]:
        compat.kill(w["pid"]); print("stopped the waiting idle hook", w["pid"])
for n in services.NAMES:
    p = services.unit_file(n)
    if not services.owned(n):
        print(f"left {p} alone: it belongs to another copy or program"); continue
    had = (p and os.path.exists(p)) or services.running_pid(n) not in (None,)
    if services.remove(n):
        if had:
            print("stopped", n)
    else:
        failed.append(n); print(f"WARNING: {n} did not stop; its auto-start file was kept" + (f": {p}" if p else ""))
if any(install.ours_any(h) for g in s.get("hooks", {}).values() for x in g for h in x.get("hooks", [])):
    install.backup_settings()
    for ev in list(s["hooks"]):
        groups = []
        for g in s["hooks"][ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if not install.ours_any(h)]   # exact match only (current + earlier forms)
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
