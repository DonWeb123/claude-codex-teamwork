#!/usr/bin/env python3
"""End-to-end link check, judged from hook and daemon RECEIPTS only (never from someone reading the inbox by hand):
  1. the first message reached the partner's Codex chat (its wake marker appeared in the partner's session log)
  2. the partner answered it (a linked reply exists)
  3. that answer reached Claude THROUGH A HOOK: the mid-turn hook showed it, or the idle hook woke Claude for it
Prints PASS/FAIL per step; exit 0 only if all three pass. Run it after restarting Claude Code once the partner has replied."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, cfg, get_json, msgs


def jl(p):
    import json
    out = []
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def main():
    C = cfg(); ME, P = C["claude_name"], C["partner_name"]; M = msgs()
    hand = [m for m in M if m.get("type") == "HANDOFF" and m.get("from") == ME and m.get("to") == P]
    if not hand:
        print("FAIL no first message to the partner found: run install.py"); return 1
    h = hand[-1]; ok = True
    w = (get_json(os.path.join(DATA, "wake", f"{h['id']}.wakes.json"), {}) or {}).get("wakes", [])
    d = next((x["delivered"] for x in w if x.get("delivered")), None)
    print(("PASS" if d else "FAIL") + f" 1. {h['id']} reached the {P} chat" + (f" (marker seen {d})" if d else ": no wake marker in its session log yet"))
    ok &= bool(d)
    ans = [m for m in M if m.get("from") == P and m.get("to") == ME and m.get("re") == h["id"]]
    print(("PASS" if ans else "FAIL") + f" 2. {P} answered it" + (f" ({ans[0]['id']})" if ans else ": no linked reply yet"))
    ok &= bool(ans)
    if ans:
        a = ans[0]["id"]; H = os.path.join(DATA, "hooks")
        mid = any(a in e.get("ids", []) for e in jl(os.path.join(H, "midturn_log.jsonl")))
        idle = any(e.get("kind") == "wake" and a in e.get("ids", []) for e in jl(os.path.join(H, "receipts.jsonl")))
        via = "mid-turn hook" if mid else "idle hook" if idle else None
        print(("PASS" if via else "FAIL") + f" 3. {a} reached Claude through a hook" + (f" ({via})" if via else
              ": no hook receipt. Restart Claude Code in this folder and leave it idle for a minute, then run this again"))
        ok &= bool(via)
    print("LINK VERIFIED" if ok else "NOT VERIFIED YET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
