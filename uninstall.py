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
open(os.path.join(H, "OFF"), "w").close(); print("hooks disarmed (team-data/hooks/OFF)")
w = get_json(os.path.join(H, "waiter.json")) or {}
if isinstance(w.get("pid"), int):
    args = subprocess.run(["ps", "-p", str(w["pid"]), "-o", "args="], capture_output=True, text=True).stdout
    if os.path.join(ROOT, "team", "hooks", "idle_wake.py") in args:   # only OUR waiter, never a reused pid
        os.kill(w["pid"], signal.SIGTERM); print("stopped the waiting idle hook", w["pid"])
if sys.platform == "darwin" or install.LAUNCHCTL != "launchctl":
    for n, script in install.SERVICES.items():
        label = f"com.claudecodexteam.{n}"; p = os.path.join(install.AGENTS, label + ".plist")
        if not os.path.exists(p):
            continue
        if not install.plist_owned(label, script):
            print(f"left {p} alone: it belongs to another copy or program"); continue
        if install.stop_service(n):
            os.remove(p); print("stopped", n)
        else:
            print(f"WARNING: {n} did not stop; its plist was kept: {p}")
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
print("uninstalled (team-data/ kept; delete team-data/hooks/OFF if you install again)")
