#!/usr/bin/env python3
"""Live link check with a FRESH challenge, judged from receipts only (never from someone reading the inbox by hand).

First run: sends the partner a new challenge (an ASK carrying a one-time code) and stops. Run it again a few minutes later.
It prints LINK VERIFIED only when, all AFTER that challenge was sent:
  1. the challenge's wake marker appears in the session log of the Codex chat this copy is bound to (and the binding has not changed)
  2. the partner answered with --re <challenge> and the one-time code, and that chat's own session log shows it sent that reply
  3. the reply reached Claude THROUGH A HOOK (a mid-turn or idle-hook receipt, with the Claude session it was shown in)
Old receipts (e.g. from yesterday's setup) never count. `--new` throws away an open challenge and sends a fresh one."""
import json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, ROOT, binding, cfg, get_json, msgs, put_json
from wakerd import session_log

CH = os.path.join(DATA, "hooks", "link_challenge.json")


def jl(p):
    out = []
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def send_challenge(C, B):
    code = os.urandom(4).hex()
    body = (f"Link check {code}. Please answer this message now with --re set to this message's id and put the code {code} in "
            "the body. Nothing else is needed.")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", C["claude_name"], "--to", C["partner_name"],
                        "--type", "ASK", "--re", "none", "--subject", f"Link check {code}", "--body", body], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.startswith("sent "):
        print("FAIL could not send the challenge:", (r.stdout + r.stderr).strip()[-300:]); return 1
    mid = r.stdout.split()[1]
    put_json(CH, {"id": mid, "code": code, "t": time.time(), "thread": B["codex_thread"], "partner": C["partner_name"]})
    print(f"challenge {mid} sent to {C['partner_name']} (code {code}). Leave Claude Code open and idle; run this again in a few minutes.")
    return 2


def main():
    B = binding()
    if not B:
        print("FAIL this copy is not set up yet: run install.py"); return 1
    C = cfg(); ME, P = C["claude_name"], C["partner_name"]
    ch = get_json(CH)
    if "--new" in sys.argv or not ch or ch.get("thread") != B["codex_thread"] or ch.get("partner") != P:
        return send_challenge(C, B)
    cid, code, t0 = ch["id"], ch["code"], ch["t"]
    log = session_log(B["codex_thread"])
    text = open(log, errors="replace").read() if log else ""
    ok = True
    w = (get_json(os.path.join(DATA, "wake", f"{cid}.wakes.json"), {}) or {}).get("wakes", [])
    mk = next((x["marker"] for x in w if x.get("delivered") and x.get("t", 0) >= t0 - 1 and x.get("marker") and x["marker"] in text), None)
    print(("PASS" if mk else "FAIL") + f" 1. challenge {cid} reached the bound {P} chat"
          + (f" ([{mk}] is in its session log)" if mk else ": its wake marker is not in that chat's session log yet"))
    ok &= bool(mk)
    ans = [m for m in msgs() if m.get("from") == P and m.get("to") == ME and m.get("re") == cid
           and code in str(m.get("body", "")) and m.get("t", 0) >= t0]
    a = ans[0]["id"] if ans else None
    sent_there = bool(a) and re.search(rf"sent {a}(?!\d)", text) is not None
    print(("PASS" if sent_there else "FAIL") + f" 2. {P} answered from that chat"
          + (f" ({a}, with the code; the chat's log shows it sent it)" if sent_there
             else f": {a} exists but that chat's log does not show it sending it" if a else ": no reply with the code yet"))
    ok &= sent_there
    if a:
        H = os.path.join(DATA, "hooks")
        via = next((("mid-turn hook", e.get("session")) for e in jl(os.path.join(H, "midturn_log.jsonl"))
                    if a in e.get("ids", []) and e.get("t", 0) >= t0), None) or \
              next((("idle hook", e.get("session")) for e in jl(os.path.join(H, "receipts.jsonl"))
                    if e.get("kind") == "wake" and a in e.get("ids", []) and e.get("t", 0) >= t0), None)
        print(("PASS" if via else "FAIL") + f" 3. {a} reached Claude through a hook"
              + (f" ({via[0]}, Claude session {via[1] or 'unknown'})" if via else
                 ": no hook receipt yet. Restart Claude Code in this folder, leave it idle for a minute, then run this again"))
        ok &= bool(via)
    else:
        ok = False
    print("LINK VERIFIED" if ok else "NOT VERIFIED YET")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
