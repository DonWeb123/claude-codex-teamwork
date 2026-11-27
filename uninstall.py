#!/usr/bin/env python3
"""Undo install.py: stop the two services and remove this kit's hooks from ~/.claude/settings.json (backup first).
Your messages in team-data/ are kept; delete the folder yourself if you want them gone."""
import os, shutil, subprocess, sys, time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "team"))
from common import DATA, get_json, put_json

SETTINGS = os.path.expanduser("~/.claude/settings.json")
if sys.platform == "darwin":
    for n in ("wakerd", "commhealth"):
        p = os.path.expanduser(f"~/Library/LaunchAgents/com.claudecodexteam.{n}.plist")
        if os.path.exists(p):
            subprocess.run(["launchctl", "unload", p], capture_output=True); os.remove(p); print("stopped", n)
s = get_json(SETTINGS)
if isinstance(s, dict) and s.get("hooks"):
    b = os.path.join(DATA, f"settings.backup.{time.strftime('%Y%m%d-%H%M%S')}.json")
    shutil.copyfile(SETTINGS, b); os.chmod(b, 0o600)
    mine = os.path.join(ROOT, "team", "hooks")
    for ev in list(s["hooks"]):
        groups = []
        for g in s["hooks"][ev]:
            g["hooks"] = [h for h in g.get("hooks", []) if mine not in h.get("command", "")]
            if g["hooks"]:
                groups.append(g)
        if groups:
            s["hooks"][ev] = groups
        else:
            del s["hooks"][ev]
    put_json(SETTINGS, s); print("hooks removed from", SETTINGS)
print("uninstalled (team-data/ kept)")
