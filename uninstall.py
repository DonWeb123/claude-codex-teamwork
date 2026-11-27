#!/usr/bin/env python3
"""Undo install.py: stop the two services (only if they are this copy's) and remove exactly this kit's hooks from
~/.claude/settings.json (owner-only backup first). Your messages in team-data/ are kept; delete the folder yourself if you want them gone."""
import os, subprocess, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import install  # noqa: E402
from common import put_json  # noqa: E402

if sys.platform == "darwin":
    for n, script in install.SERVICES.items():
        label = f"com.claudecodexteam.{n}"; p = os.path.join(install.AGENTS, label + ".plist")
        if not os.path.exists(p):
            continue
        if not install.plist_owned(label, script):
            print(f"left {p} alone: it belongs to another copy or program"); continue
        subprocess.run(["launchctl", "unload", p], capture_output=True); os.remove(p); print("stopped", n)
if os.path.exists(install.SETTINGS):
    s = install.load_settings()   # refuses a broken file instead of guessing
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
print("uninstalled (team-data/ kept)")
