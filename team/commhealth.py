#!/usr/bin/env python3
"""Passive health monitor: measures the link from REAL traffic (no test pings, no usage spent) every 60 s.

Per direction: time from a message being written to the first proof the receiver saw it
  Claude -> partner: wake marker found in the partner's Codex log, the partner's ack, or a linked reply
  partner -> Claude: mid-turn hook showed it, idle hook woke Claude for it, Claude's ack, or a linked reply
Reports p50/p90 and how many messages have no proof yet. Pages COMM DEGRADED (once per incident) when mail sits unseen
> 10 min, the wake daemon's heartbeat is stale or missing, or no Codex app is running.
Writes team-data/wake/COMM_HEALTH.md.        python3 commhealth.py once | run"""
import math, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, cfg, get_json, put_json, msgs, alert, write_pid
from wakerd import iso, codex_appservers

W = os.path.join(DATA, "wake"); H = os.path.join(DATA, "hooks")
PAGE_AGE_S, TARGET_P90 = 600, 60


def pct(v, q):
    if not v:
        return None
    v = sorted(v)
    return round(v[max(0, math.ceil(q * len(v)) - 1)], 1)   # nearest rank


def jl(p):
    import json
    out = []
    if os.path.exists(p):
        for line in open(p, errors="replace"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass   # one bad line never breaks the report
    return out


def tick(now=None, ps_out=None):
    C = cfg(); ME, P = C["claude_name"], C["partner_name"]; now = now or time.time()
    M = msgs(); since = C.get("installed_at") or 0
    seen_c = {}
    for e in jl(os.path.join(H, "midturn_log.jsonl")):
        for i in e.get("ids", []):
            seen_c.setdefault(i, e["t"])
    for e in jl(os.path.join(H, "receipts.jsonl")):
        if e.get("kind") == "wake":
            for i in e.get("ids", []):
                seen_c[i] = min(seen_c.get(i, 1e18), e["t"])
    rows = {f"{ME}->{P}": [], f"{P}->{ME}": []}; pending = []
    for m in M:
        frm, to, i, c = m.get("from"), m.get("to"), m["id"], m.get("t", m["_created"])
        if f"{frm}->{to}" not in rows or c < since:
            continue
        ack = os.path.join(DATA, "acks", f"{i}.{to}"); ack_t = os.path.getmtime(ack) if os.path.exists(ack) else None
        if to == P:
            w = (get_json(os.path.join(W, f"{i}.wakes.json"), {}) or {}).get("wakes", [])
            proofs = [iso(x["delivered"]) for x in w if x.get("delivered")] + [ack_t]
        else:
            proofs = [seen_c.get(i), ack_t]
        rep = min([x.get("t", x["_created"]) for x in M if x.get("from") == to and x.get("to") == frm and x.get("re") == i], default=None)
        first = min([t for t in proofs + [rep] if t], default=None)
        rows[f"{frm}->{to}"].append({"seen": first - c if first else None, "reply": rep - c if rep and m.get("reply") else None})
        if first is None:
            pending.append((to, i, now - c))
    out = [f"# Comm health {time.strftime('%F %T', time.localtime(now))} (target p90 < {TARGET_P90} s each way; measured from real messages)"]
    for d, L in rows.items():
        s = [r["seen"] for r in L if r["seen"] is not None]; rp = [r["reply"] for r in L if r["reply"] is not None]
        p9 = pct(s, .9)
        out.append(f"- {d}: seen {len(s)}/{len(L)}, p50 {pct(s, .5)} s, p90 {p9} s{' (OVER TARGET)' if p9 is not None and p9 >= TARGET_P90 else ''}; reply p50 {pct(rp, .5)} s (n={len(rp)})")
    hb = get_json(os.path.join(W, "heartbeat.json"))
    if ps_out is None:
        try:
            ps_out = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            ps_out = ""
    procs = codex_appservers(ps_out) if ps_out.strip() else None
    out.append(f"- wake daemon heartbeat: {round(now - hb['t']) if hb else 'missing'} s ago; Codex apps running: {len(procs) if procs is not None else 'unknown'}")
    out.append(f"- unseen messages: {len(pending)}" + (f"; oldest {max(pending, key=lambda x: x[2])[1]} ({round(max(x[2] for x in pending))} s)" if pending else ""))
    causes = {}
    for to, i, age in pending:
        if age > PAGE_AGE_S:
            causes[f"unseen|{i}"] = f"{i} to {to} unseen for {round(age)} s"
    if not hb:
        causes["wakerd|missing"] = "wake daemon heartbeat missing: is wakerd running?"
    elif now - hb["t"] > 120:
        causes["wakerd|stale"] = f"wake daemon heartbeat {round(now - hb['t'])} s old"
    if procs == []:
        causes["codex|none"] = f"no Codex app running: {P} cannot be woken"
    inc_p = os.path.join(W, "health_incidents.json"); inc = get_json(inc_p, {}) or {}
    inc = {c: v for c, v in inc.items() if c in causes}   # cleared causes close their incident
    for c, text in causes.items():
        if c not in inc:
            inc[c] = {"id": f"{c}#{int(now)}", "text": text}
            alert(inc[c]["id"], "COMM DEGRADED", text)
    put_json(inc_p, inc)
    out.append(f"- open incidents: {len(inc)}" + "".join(f"\n  - {v['text']}" for v in inc.values()))
    tmp = os.path.join(W, "COMM_HEALTH.md.tmp")
    open(tmp, "w").write("\n".join(out) + "\n"); os.replace(tmp, os.path.join(W, "COMM_HEALTH.md"))
    return out


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "once"
    if c == "once":
        print("\n".join(tick()))
    elif c == "run":
        write_pid("commhealth")
        while True:
            try:
                tick()
            except Exception as e:
                open(os.path.join(W, "commhealth.err"), "a").write(f"{time.strftime('%F %T')} {e!r}\n")
            time.sleep(60)
