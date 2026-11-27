#!/usr/bin/env python3
"""Wake daemon: delivers Claude's mail to the Codex partner and PROVES it arrived.

For every unread message to the partner it queues ONE wake into the partner's Codex chat (`codex queue --thread ...`)
carrying a unique marker [wake:xxxx]. Delivery is confirmed only when that marker shows up as a user message in the
partner's Codex session log (both the old `event_msg/user_message` and the newer `response_item role=user` formats).
  - several messages at once -> one coalesced wake; never two wakes for the same message inside the backoff
  - not delivered after 5 min -> alert START UNCONFIRMED; re-wake after 15 min, at most 3 times, then STALLED
  - Codex app restarted -> alert CODEX RESTARTED (queued wakes are accepted but no turn starts until the chat is reopened)
  - a reply-wanted message unanswered for 45 min -> REPLY OVERDUE (either direction)
Reacts to new mail within ~0.2-0.5 s (watches the mailbox folder), with a 10 s backstop tick. One instance (lock).
  python3 wakerd.py run | once | status        TEAMKIT_DRY=1 = never call codex, never notify"""
import calendar, fcntl, glob, json, os, re, subprocess, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, cfg, get_json, put_json, msgs, acked, answered, alert, find_codex, codex_home, team_cmd, binding, write_pid

W = os.path.join(DATA, "wake")
REWAKE_S, MAX_WAKES, NOSTART_S, REPLY_DEADLINE_S = 900, 3, 300, 2700
DRY = os.environ.get("TEAMKIT_DRY") == "1"


def iso(x):
    try:
        b, _, frac = str(x).rstrip("Z").partition(".")
        return calendar.timegm(time.strptime(b, "%Y-%m-%dT%H:%M:%S")) + (float("0." + frac) if frac else 0.0)
    except Exception:
        return None


def session_log(thread):
    f = [x for x in glob.glob(os.path.join(codex_home(), "sessions", "*", "*", "*", "*.jsonl")) if thread and thread in os.path.basename(x)]
    return max(f, key=os.path.getmtime) if f else None


def partner_events(thread, state):
    """Incremental read of COMPLETE lines only (a half-written last line is left for the next tick).
    Returns user-message texts (with timestamps) and the new read position; turn start/end tracked for 'busy'."""
    f = session_log(thread)
    if not f:
        return [], state
    same = state.get("file") == f
    off = state.get("offset", 0) if same else 0
    busy = state.get("busy") if same else None
    turn = state.get("turn") if same else None
    ev = []
    with open(f, "rb") as h:
        h.seek(off)
        while True:
            pos = h.tell(); raw = h.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                h.seek(pos); break
            try:
                d = json.loads(raw)
            except Exception:
                continue
            p = d.get("payload") or {}; t = p.get("type"); ts = d.get("timestamp")
            if t == "task_started":
                busy, turn = True, ts
            elif t in ("task_complete", "turn_aborted"):
                busy = False
            if t == "user_message":
                ev.append((ts, str(p.get("message") or "")))
            elif d.get("type") == "response_item" and t == "message" and p.get("role") == "user":
                ev.append((ts, " ".join(c.get("text", "") for c in (p.get("content") or []) if isinstance(c, dict))))
        off = h.tell()
    return ev, {"file": f, "offset": off, "busy": busy, "turn": turn}


def codex_appservers(ps_out):
    r = []
    for line in ps_out.splitlines():
        f = line.split()
        if len(f) < 7:
            continue
        exe, args = f[6], f[7:]
        if os.path.basename(exe) == "codex" and "app-server" in args:
            r.append(" ".join(f[:6]))
    return sorted(r)


def restart_check(ps_out=None):
    if ps_out is None:
        try:
            r = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return
            ps_out = r.stdout
        except Exception:
            return
    if not ps_out.strip():
        return   # an empty process table is an inspection failure, never a restart
    cur = codex_appservers(ps_out); p = os.path.join(W, "codex_procs.json"); prev = get_json(p)
    if prev is not None and cur != prev.get("procs") and cur:
        alert("codexrestart|" + ",".join(c.split()[0] for c in cur), "CODEX RESTARTED",
              f"Codex restarted: open the {cfg()['partner_name']} chat in the Codex panel once so wakes start turns again")
    put_json(p, {"procs": cur, "t": time.time()})


def queue(text):
    C = cfg()
    if DRY:
        return "DRY", "dry run"
    b = binding()   # delivery goes ONLY to the chat this mailbox is bound to, whatever config.json says
    if not b or b["codex_thread"] != C.get("codex_thread"):
        return None, "routing refused: config.json chat does not match this mailbox's binding (team-data/binding.json)"
    codex = find_codex()
    if not codex:
        return None, "codex CLI not found"
    try:
        r = subprocess.run([codex, "queue", "--thread", C["codex_thread"], "--message", text], capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr)[-300:]
        if r.returncode != 0:
            return None, out
        return next((x for x in out.split() if len(x) == 36 and x.count("-") == 4), "accepted"), out
    except subprocess.TimeoutExpired:
        return "UNKNOWN", "timeout: delivery unknown, settled by the marker"


def tick(now=None, ps_out=None):
    C = cfg(); P, ME = C["partner_name"], C["claude_name"]; now = now or time.time()
    M = msgs()
    b = get_json(os.path.join(W, "baseline.json"))
    if b is None:   # first run: never wake for old history
        b = {"id": max([int(m["id"][1:]) for m in M] or [0]), "t": now}; put_json(os.path.join(W, "baseline.json"), b)
    M2 = [m for m in M if int(m["id"][1:]) > b["id"]]
    st = get_json(os.path.join(W, "partner_log.json"), {}) or {}
    ev, st2 = partner_events(C["codex_thread"], st)
    restart_check(ps_out)
    # 1. confirm deliveries by marker
    for m in M2:
        p = os.path.join(W, f"{m['id']}.wakes.json")
        if m["to"] != P or not os.path.exists(p):
            continue
        s = get_json(p, {"wakes": []}); ch = False
        for w in s["wakes"]:
            if w.get("delivered"):
                continue
            hit = next((ts for ts, txt in ev if w["marker"] in txt), None)
            if hit:
                w["delivered"] = hit; ch = True
        if ch:
            put_json(p, s)
    # 2. decide who needs a wake
    due = []
    for m in M2:
        if m["to"] != P or acked(m["id"], P) or (m.get("reply") and answered(m, M)):
            continue
        p = os.path.join(W, f"{m['id']}.wakes.json"); s = get_json(p, {"wakes": []}); last = s["wakes"][-1] if s["wakes"] else None
        if any(w.get("delivered") for w in s["wakes"]):
            if m.get("reply") and now - m.get("t", m["_created"]) > REPLY_DEADLINE_S:
                alert(f"overdue|{m['id']}", "REPLY OVERDUE", f"{P} has not answered {m['id']} for {int((now - m.get('t', m['_created'])) // 60)} min")
            continue
        if last is None:
            due.append(m); continue
        age = now - last["t"]
        if age > NOSTART_S:
            alert(f"nostart|{m['id']}|{len(s['wakes'])}", "START UNCONFIRMED", f"wake for {m['id']} not seen in {P}'s chat after {int(age)} s")
        if age > REWAKE_S:
            if len(s["wakes"]) < MAX_WAKES:
                due.append(m)
            elif not s.get("stalled"):
                s["stalled"] = now; put_json(p, s)
                alert(f"stalled|{m['id']}", "PARTNER STALLED", f"{m['id']} not delivered after {MAX_WAKES} wakes: is the {P} chat open and not out of usage?")
    # 3. one coalesced wake
    if due:
        marker = "wake:" + uuid.uuid4().hex[:12]
        text = (f"[{marker}] Team mailbox: {len(due)} new message(s) for you ({sum(1 for m in due if m.get('reply'))} need a reply): "
                + "; ".join(f"{m['id']} {m['subject'][:60]}" for m in due)
                + f". Read with: {team_cmd()} inbox --as {P} ; answer with {team_cmd()} send --as {P} --to {ME} --re <id> ...")
        for m in due:   # record the intent BEFORE submitting, so a crash cannot lose it
            p = os.path.join(W, f"{m['id']}.wakes.json"); s = get_json(p, {"wakes": []})
            s["wakes"].append({"t": now, "marker": marker, "busy_at_queue": st2.get("busy")}); put_json(p, s)
        q, out = queue(text)
        for m in due:
            p = os.path.join(W, f"{m['id']}.wakes.json"); s = get_json(p, {"wakes": []})
            s["wakes"][-1].update(queued=q, out=out[-160:]); put_json(p, s)
        if not q:
            alert(f"wakefail|{marker}", "WAKE FAILED", f"could not queue a wake for {P}: {out[-120:]}")
    # 4. Claude side: overdue answers
    for m in M2:
        if m["to"] == ME and m.get("reply") and not answered(m, M) and now - m.get("t", m["_created"]) > REPLY_DEADLINE_S:
            alert(f"overdue|{m['id']}", "REPLY OVERDUE", f"{ME} has not answered {m['id']} for {int((now - m.get('t', m['_created'])) // 60)} min")
    put_json(os.path.join(W, "partner_log.json"), st2)   # read position committed only after the records are durable
    put_json(os.path.join(W, "heartbeat.json"), {"t": now, "ts": time.strftime("%F %T", time.localtime(now)), "pid": os.getpid(),
                                                  "partner_busy": st2.get("busy"), "woke": [m["id"] for m in due]})
    return due


def stamp():
    try:
        return (os.stat(os.path.join(DATA, "msgs")).st_mtime_ns, os.stat(os.path.join(DATA, "acks")).st_mtime_ns)
    except OSError:
        return None


def wait_next(s0, t0, step=0.2, backstop=10.0):
    """return as soon as the mailbox folder changes (baseline taken BEFORE the last scan), else after the backstop"""
    while True:
        time.sleep(step)
        if stamp() != s0:
            return "change"
        if time.monotonic() - t0 >= backstop:
            return "backstop"


def status():
    hb = get_json(os.path.join(W, "heartbeat.json"))
    if not hb:
        return "wakerd never ran"
    return f"wakerd heartbeat {round(time.time() - hb['t'])} s ago (pid {hb['pid']}); partner busy: {hb.get('partner_busy')}"


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "status"
    if c in ("run", "once"):
        if not cfg().get("codex_thread") and not DRY:
            sys.exit("no codex_thread in config.json: run install.py first")
        lk = open(os.path.join(W, "daemon.lock"), "w")
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit("another wakerd is already running")
        if c == "run":
            write_pid("wakerd")
        if c == "once":
            print("woke:", [m["id"] for m in tick()]); sys.exit(0)
        while True:
            s0 = stamp(); t0 = time.monotonic()
            try:
                tick()
            except Exception as e:
                alert("err|" + type(e).__name__ + "|" + time.strftime("%F %H"), "WAKERD ERROR", repr(e)[:200])
            wait_next(s0, t0)
    print(status())
