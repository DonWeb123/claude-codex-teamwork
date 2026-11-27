#!/usr/bin/env python3
"""Claude Code PostToolUse hook: shows Claude new team mail WHILE it is working (after every tool call).

Before this hook, a busy Claude only saw mail when its turn ended: replies took 5-8 minutes at the slow end.
Guarantees (each one came from a review round):
  - the checkpoint moves only over items actually printed, after stdout was flushed; extras wait for the next call
  - a message file that cannot be read yet stops the scan at that id (retried); after 5 tries it is reported, never skipped
  - one updater at a time (lock); the alert log is tracked by inode + offset, complete lines only
  - never blocks or fails a tool: any error = silent exit 0.  Kill switch: team-data/hooks/OFF or MIDTURN_OFF"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import compat
from common import DATA, cfg, get_json, put_json, in_work_dirs, team_cmd

H = os.path.join(DATA, "hooks"); ST = os.path.join(H, "midturn_state.json")
ACTIONABLE = {"START UNCONFIRMED", "PARTNER STALLED", "WAKE FAILED", "REPLY OVERDUE", "CODEX RESTARTED", "COMM DEGRADED", "WAKERD ERROR"}
MAX_EMIT, MAX_TRIES = 8, 5


def main():
    if os.path.exists(os.path.join(H, "OFF")) or os.path.exists(os.path.join(H, "MIDTURN_OFF")):
        return
    try:
        hook = json.loads(sys.stdin.read() or "{}")
    except Exception:
        hook = {}
    if not in_work_dirs(hook.get("cwd") or os.getcwd()):
        return
    try:
        lk = compat.lock(os.path.join(H, "midturn.lock"), blocking=False)  # noqa: F841 (held until exit)
    except compat.Busy:
        return
    me = cfg()["claude_name"]
    names = [n for n in os.listdir(os.path.join(DATA, "msgs")) if n.startswith("M") and n.endswith(".json") and n[1:-5].isdigit()]
    top = max((int(n[1:-5]) for n in names), default=0)
    alog = os.path.join(DATA, "wake", "alerts.jsonl")
    if not os.path.exists(ST):   # first run: start at the current end, no backlog
        ino, size = (os.stat(alog).st_ino, os.path.getsize(alog)) if os.path.exists(alog) else (None, 0)
        put_json(ST, {"max_id": top, "alert_ino": ino, "alert_off": size, "tries": {}}); return
    st = get_json(ST)
    if not isinstance(st, dict):
        return   # unreadable checkpoint is NOT a first run: keep it, retry next call
    items, tries, i = [], dict(st.get("tries", {})), st.get("max_id", top) + 1
    while i <= top and sum(1 for x in items if x[0] == "m") < MAX_EMIT:
        m = get_json(os.path.join(DATA, "msgs", f"M{i:04d}.json"))
        if not isinstance(m, dict):
            n = tries.get(str(i), 0) + 1; tries[str(i)] = n
            if n < MAX_TRIES:
                break
            items.append(("m", i, f"[M{i:04d}] could not be read after {n} tries: open it with {team_cmd()} read M{i:04d}"))
        elif m.get("to") == me:
            items.append(("m", i, f"[{m['id']}] {m.get('from')}->{me} {m.get('type')}{' REPLY WANTED' if m.get('reply') else ''}: {str(m.get('subject', ''))[:140]}"))
        else:
            items.append(("skip", i, None))
        i += 1
    a_ino, a_off, a_new = st.get("alert_ino"), st.get("alert_off", 0), []
    alert_end = a_off
    if os.path.exists(alog):
        with open(alog, "rb") as f:
            s_ = os.fstat(f.fileno())
            if s_.st_ino != a_ino or s_.st_size < a_off:
                a_ino, a_off = s_.st_ino, 0
            f.seek(a_off); data = f.read(s_.st_size - a_off)
        pos = 0
        for raw in data.split(b"\n")[:-1]:
            try:
                a = json.loads(raw)
            except Exception:
                a = {}
            if a.get("kind") in ACTIONABLE:
                if sum(1 for x in items if x[0] == "m") + len(a_new) >= MAX_EMIT:
                    break
                a_new.append(f"ALERT {a.get('kind')}: {str(a.get('text', ''))[:140]}")
            pos += len(raw) + 1
        alert_end = a_off + pos
    lines = [x[2] for x in items if x[0] == "m"] + a_new
    if lines:
        txt = f"TEAM MAIL arrived while you were working (read with: {team_cmd()} inbox --as {me}):\n" + "\n".join(lines)
        if i <= top:
            txt += "\n(more pending: shown after the next tool call)"
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": txt}}) + "\n")
        sys.stdout.flush()   # print FIRST, then move the checkpoint
        try:
            with open(os.path.join(H, "midturn_log.jsonl"), "a") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "ids": [f"M{x[1]:04d}" for x in items if x[0] == "m"], "alerts": len(a_new), "session": hook.get("session_id")}) + "\n")
        except Exception:
            pass
    last = max([x[1] for x in items], default=st.get("max_id", top))
    put_json(ST, {"max_id": last, "alert_ino": a_ino, "alert_off": alert_end, "tries": {k: v for k, v in tries.items() if int(k) > last}})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
