#!/usr/bin/env python3
"""Claude Code Stop/SessionStart hook (asyncRewake): wakes an IDLE Claude when the partner sends mail.

It waits in the background for a message to Claude that is not acked and not yet announced, or a new team alert,
then prints a short notice and exits 2 - which starts a new Claude turn. Read-only on the mailbox.
  - acts only when the Claude session runs inside a folder listed in config.json work_dirs
  - kill switch: team-data/hooks/OFF
  - one waiter at a time (lock); every arm/wake/exit is logged to team-data/hooks/receipts.jsonl
  - each message is announced once, plus one reminder if still unacked after 20 min
  - exits 0 before the hook timeout; the next Stop arms a fresh waiter"""
import fcntl, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import DATA, cfg, get_json, put_json, msgs, acked, in_work_dirs, team_cmd

H = os.path.join(DATA, "hooks")
POLL_S = float(os.environ.get("TEAMKIT_POLL_S", "2"))
DEADLINE_S = float(os.environ.get("TEAMKIT_REWAKE_DEADLINE_S", "21000"))   # hook timeout is 21600 s
STALL_S = 1200
ACTIONABLE = {"START UNCONFIRMED", "PARTNER STALLED", "WAKE FAILED", "REPLY OVERDUE", "CODEX RESTARTED", "COMM DEGRADED", "WAKERD ERROR"}


def log(kind, **kw):
    try:
        with open(os.path.join(H, "receipts.jsonl"), "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "ts": time.strftime("%F %T"), "kind": kind, "pid": os.getpid(), **kw}) + "\n")
    except Exception:
        pass


def new_alerts(cur):
    """alerts.jsonl tracked by inode + byte offset; complete lines only; a replaced/truncated log restarts at 0"""
    p = os.path.join(DATA, "wake", "alerts.jsonl")
    try:
        f = open(p, "rb")
    except FileNotFoundError:
        return [], cur or {"ino": None, "off": 0}
    with f:
        st = os.fstat(f.fileno())
        if cur is None:
            return [], {"ino": st.st_ino, "off": st.st_size}   # first arm: no backlog
        off = cur["off"] if cur.get("ino") == st.st_ino and st.st_size >= cur["off"] else 0
        f.seek(off); data = f.read(st.st_size - off)
    end = data.rfind(b"\n") + 1; out = []
    for line in data[:end].splitlines():
        try:
            a = json.loads(line)
        except Exception:
            continue
        if a.get("kind") in ACTIONABLE:
            out.append(a)
    return out, {"ino": st.st_ino, "off": off + end}


def main():
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except Exception:
        hook = {}
    if os.path.exists(os.path.join(H, "OFF")) or not in_work_dirs(hook.get("cwd") or os.getcwd()):
        return 0
    lk = open(os.path.join(H, "waiter.lock"), "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0
    me = cfg()["claude_name"]; t0 = time.time()
    import subprocess
    start = subprocess.run(["ps", "-p", str(os.getpid()), "-o", "lstart="], capture_output=True, text=True).stdout.strip()
    put_json(os.path.join(H, "waiter.json"), {"pid": os.getpid(), "start": start, "since": t0, "session": hook.get("session_id")})
    log("armed", event=hook.get("hook_event_name"), session=hook.get("session_id"))
    cur_p = os.path.join(H, "alert_cursor.json")
    while True:
        if os.path.exists(os.path.join(H, "OFF")):
            log("off"); return 0
        dl = get_json(os.path.join(H, "delivered.json"), {}) or {}
        al, cur = new_alerts(get_json(cur_p))
        fresh, stall = [], []
        for m in msgs():
            if m.get("to") != me or acked(m["id"], me):
                continue
            r = dl.get(m["id"])
            if r is None:
                fresh.append(m)
            elif not r.get("stall") and time.time() - r["t"] > STALL_S:
                stall.append(m)
        if fresh or stall or al:
            lines = [f"[{m['id']}] {m['from']}->{me} {m['type']}{' REPLY WANTED' if m.get('reply') else ''}: {m['subject'][:120]}"
                     + (" (still unread after 20 min)" if m in stall else "") for m in fresh + stall]
            lines += [f"ALERT {a['kind']}: {a.get('text', '')[:140]}" for a in al]
            for m in fresh:
                dl[m["id"]] = {"t": time.time()}
            for m in stall:
                dl[m["id"]]["stall"] = time.time()
            put_json(os.path.join(H, "delivered.json"), dl)
            put_json(cur_p, cur)
            log("wake", ids=[m["id"] for m in fresh + stall], alerts=len(al), session=hook.get("session_id"))
            sys.stderr.write("TEAM MAIL: " + "; ".join(lines) + f" -- read with: {team_cmd()} inbox --as {me}\n")
            return 2
        put_json(cur_p, cur)
        if time.time() - t0 > DEADLINE_S:
            log("deadline"); return 0
        time.sleep(POLL_S)


if __name__ == "__main__":
    sys.exit(main())
