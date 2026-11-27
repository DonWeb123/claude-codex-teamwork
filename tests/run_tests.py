#!/usr/bin/env python3
"""Self-tests for the kit. Safe to run any time: everything happens in a temporary folder, Codex is never called
(dry mode + a fake Codex session log), your real mailbox and settings are not touched. Exit 0 = all passed."""
import json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="teamkit-test-")
DATA, CODEX_HOME = os.path.join(TMP, "data"), os.path.join(TMP, "codex")
CONFIG = os.path.join(TMP, "config.json")
WORK = os.path.join(TMP, "work"); os.makedirs(WORK)
THREAD = "00000000-test-4000-8000-000000000001"
json.dump({"claude_name": "claude", "partner_name": "codex", "codex_thread": THREAD, "work_dirs": [WORK], "installed_at": 0}, open(CONFIG, "w"))
ENV = dict(os.environ, TEAMKIT_DATA=DATA, TEAMKIT_CONFIG=CONFIG, TEAMKIT_CODEX_HOME=CODEX_HOME, TEAMKIT_DRY="1",
           TEAMKIT_POLL_S="0.1", TEAMKIT_REWAKE_DEADLINE_S="1")
os.environ.update(ENV)
sys.path.insert(0, os.path.join(ROOT, "team"))
import wakerd, commhealth  # noqa: E402  (imported after the env points at the temp folder)
from common import get_json  # noqa: E402

LOGDIR = os.path.join(CODEX_HOME, "sessions", "2026", "01", "01"); os.makedirs(LOGDIR)
LOG = os.path.join(LOGDIR, f"rollout-2026-01-01T00-00-00-{THREAD}.jsonl"); open(LOG, "w").close()
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok); print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"  -> {detail}"))


def team(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), *args], capture_output=True, text=True, env=ENV)
    return r.returncode, (r.stdout + r.stderr).strip()


def hook(name, payload, timeout=10):
    t = time.time()
    r = subprocess.run([sys.executable, os.path.join(ROOT, "team", "hooks", name)], input=json.dumps(payload), capture_output=True, text=True, env=ENV, timeout=timeout)
    return r.returncode, r.stdout, r.stderr, time.time() - t


def codex_log(record):
    with open(LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def wakes(mid):
    return (get_json(os.path.join(DATA, "wake", f"{mid}.wakes.json"), {}) or {}).get("wakes", [])


S = {"session_id": "s1", "cwd": WORK, "hook_event_name": "Stop"}
# ---------- mailbox rules
wakerd.tick(ps_out="")   # baseline: history before the daemon's first run never wakes anyone
rc, out = team("send", "--as", "claude", "--to", "codex", "--type", "FYI", "--subject", "status", "--body", "Build passed on first try.")
check("mailbox: send works", rc == 0 and "sent M0001" in out, out)
rc, out = team("send", "--as", "claude", "--to", "codex", "--type", "RESULT", "--subject", "done", "--body", "Can you check it?")
check("mailbox: a question sent as RESULT is refused (lesson: 13 min lost)", rc != 0 and "question" in out, out)
rc, out = team("send", "--as", "claude", "--to", "codex", "--type", "FYI", "--subject", "x", "--body", "word " * 221)
check("mailbox: over 220 words is refused", rc != 0 and "220" in out, out)
rc, out = team("send", "--as", "codex", "--to", "claude", "--type", "ASK", "--subject", "Which file?", "--body", "Which config file do you mean?")
rc, out = team("send", "--as", "claude", "--to", "codex", "--type", "FYI", "--subject", "x", "--body", "unrelated note")
check("mailbox: replying while the other waits needs --re (lesson: false overdue alarms)", rc != 0 and "--re" in out, out)
# ---------- leases (lesson: a plain claim also succeeds for the holder, so it is not a "free" test)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "run A", "--if-free", "--token", "auto")
tokA = out.split("token=")[-1] if "token=" in out else ""
check("lease: --if-free --token auto claims a free lease and prints the token", rc == 0 and len(tokA) == 12, out)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "run B", "--if-free", "--token", "auto")
check("lease: a second run of the SAME agent cannot take it with --if-free", rc != 0 and "not free" in out, out)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "run B", "--token", "bbbb")
check("lease: nor with its own token without --if-free", rc != 0 and "another token" in out, out)
rc, out = team("claim", "gpu", "--as", "codex", "--what", "partner")
check("lease: the partner cannot take it", rc != 0 and "held by claude" in out, out)
rc, out = team("release", "gpu", "--as", "claude")
check("lease: a release without the token is refused", rc != 0 and "token" in out, out)
rc, out = team("release", "gpu", "--as", "claude", "--token", "bbbb")
check("lease: a release with another run's token is refused", rc != 0 and "token" in out, out)
rc, out = team("claims")
check("lease: claims shows it token-bound", "gpu held by claude" in out and "token-bound" in out, out)
rc, out = team("release", "gpu", "--as", "claude", "--token", tokA)
check("lease: the owning run releases it with its token", rc == 0 and "released gpu" in out, out)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "plain")
rc2, out2 = team("release", "gpu", "--as", "claude")
check("lease: plain claim/release without tokens still works", rc == 0 and rc2 == 0, out + out2)
rc, out = team("release", "gpu", "--as", "claude")
check("lease: releasing a free lease is a harmless no-op", rc == 0 and "was not held" in out, out)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "x", "--token", "../etc")
check("lease: tokens are plain characters only", rc != 0 and "bad token" in out, out)
for bad in ("{not json", "[]", "{}", '{"what": "no owner field"}', '{"by": 7}'):
    open(os.path.join(DATA, "locks", "gpu.json"), "w").write(bad)
    rc, out = team("claim", "gpu", "--as", "claude", "--what", "x", "--if-free", "--token", "auto")
    rc2, out2 = team("release", "gpu", "--as", "claude", "--force")
    kept = open(os.path.join(DATA, "locks", "gpu.json")).read() == bad
    check(f"lease: a malformed lease file ({bad}) is never treated as free and keeps its bytes", rc != 0 and rc2 != 0 and kept and "malformed" in out and "malformed" in out2, out + out2)
os.remove(os.path.join(DATA, "locks", "gpu.json"))
import threading  # noqa: E402
res = []
th = [threading.Thread(target=lambda i=i: res.append(team("claim", "gpu", "--as", "claude", "--what", f"r{i}", "--if-free", "--token", "auto"))) for i in range(6)]
[t.start() for t in th]; [t.join() for t in th]
win = [o for r, o in res if r == 0]
check("lease: six simultaneous --if-free claims by one agent -> exactly one wins", len(win) == 1, res)
tok1 = win[0].split("token=")[-1] if win else ""
team("release", "gpu", "--as", "claude", "--token", tok1)
rc, out = team("claim", "gpu", "--as", "claude", "--what", "next run", "--if-free", "--token", "auto")
rc2, out2 = team("release", "gpu", "--as", "claude", "--token", tok1)
check("lease: an old run's token cannot release the next run's lease", rc == 0 and rc2 != 0, out + out2)
team("release", "gpu", "--as", "claude", "--token", out.split("token=")[-1])
rc, out = team("send", "--as", "claude", "--to", "codex", "--type", "ANSWER", "--re", "M0002", "--subject", "config.json", "--body", "The one in the project root.")
check("mailbox: linked answer accepted", rc == 0 and "M0003" in out, out)
rc, out = team("send", "--as", "claude", "--to", "owner", "--type", "ANSWER", "--re", "M0002", "--subject", "x", "--body", "y")
check("mailbox: --re must link the same two parties", rc != 0 and "not claude and owner" in out, out)
rc, out = team("inbox", "--as", "codex")
check("mailbox: inbox shows unread mail", "M0001" in out and "M0003" in out, out)
team("ack", "M0001", "--as", "codex")
rc, out = team("inbox", "--as", "codex")
check("mailbox: ack removes it from the inbox", "M0001" not in out and "M0003" in out, out)
# ---------- wake daemon
due = wakerd.tick(ps_out="")
check("wakerd: one coalesced wake for every unread message to the partner", [m["id"] for m in due] == ["M0003"] and len(wakes("M0003")) == 1, due)
check("wakerd: acked mail is not woken", wakes("M0001") == [])
check("wakerd: no second wake inside the backoff", wakerd.tick(ps_out="") == [] and len(wakes("M0003")) == 1)
marker = wakes("M0003")[0]["marker"]
codex_log({"timestamp": "2026-01-01T00:00:01.000Z", "type": "event_msg", "payload": {"type": "task_started"}})
with open(LOG, "a") as f:
    f.write(json.dumps({"timestamp": "2026-01-01T00:00:01.5Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"[{marker}] hi"}]}}))   # no newline yet
wakerd.tick(ps_out="")
check("wakerd: a half-written log line is not read", not wakes("M0003")[0].get("delivered"))
with open(LOG, "a") as f:
    f.write("\n")
wakerd.tick(ps_out="")
check("wakerd: delivery proven by marker (new Codex log format)", wakes("M0003")[0].get("delivered") == "2026-01-01T00:00:01.5Z", wakes("M0003"))
team("send", "--as", "claude", "--to", "codex", "--type", "REVIEW", "--re", "none", "--subject", "review", "--body", "Please review the diff.")
wakerd.tick(ps_out="")
m2 = wakes("M0004")[0]["marker"]
codex_log({"timestamp": "2026-01-01T00:00:02.000Z", "type": "event_msg", "payload": {"type": "user_message", "message": f"[{m2}] mail"}})
wakerd.tick(ps_out="")
check("wakerd: delivery proven by marker (old Codex log format)", bool(wakes("M0004")[0].get("delivered")))
team("send", "--as", "claude", "--to", "codex", "--type", "FYI", "--re", "none", "--subject", "late", "--body", "Nobody will see this one.")
wakerd.tick(ps_out="")
w = os.path.join(DATA, "wake", "M0005.wakes.json"); s = get_json(w); s["wakes"][0]["t"] -= 1000; json.dump(s, open(w, "w"))
due = wakerd.tick(ps_out="")
alerts = open(os.path.join(DATA, "wake", "alerts.jsonl")).read()
check("wakerd: undelivered wake -> START UNCONFIRMED alert + bounded re-wake", "START UNCONFIRMED" in alerts and [m["id"] for m in due] == ["M0005"], alerts)
ps1 = "  101 Mon Jan  1 00:00:00 2026 /x/codex app-server --listen\n"
ps2 = "  202 Mon Jan  1 00:05:00 2026 /x/codex app-server --listen\n"
wakerd.restart_check(ps1); wakerd.restart_check("")
check("wakerd: an empty process list is never called a restart", "CODEX RESTARTED" not in open(os.path.join(DATA, "wake", "alerts.jsonl")).read())
wakerd.restart_check(ps2)
check("wakerd: Codex restart detected (lesson: 8 min stalled after an app update)", "CODEX RESTARTED" in open(os.path.join(DATA, "wake", "alerts.jsonl")).read())
s0, t0 = wakerd.stamp(), time.monotonic()
import threading  # noqa: E402
threading.Timer(0.3, lambda: team("send", "--as", "codex", "--to", "claude", "--type", "FYI", "--re", "M0004", "--subject", "speed", "--body", "timing probe")).start()
t = time.time(); why = wakerd.wait_next(s0, t0); lat = time.time() - t - 0.3
check(f"wakerd: reacts to new mail fast (measured {lat:.2f} s after the send)", why == "change" and lat < 2.0, (why, lat))
# ---------- idle hook (Claude)
rc, _, err, dt = hook("idle_wake.py", dict(S, cwd=TMP))
check("idle hook: does nothing outside the work folders", rc == 0 and dt < 3, (rc, err))
rc, _, err, dt = hook("idle_wake.py", S)
check("idle hook: unread mail -> exit 2 with a notice (wakes Claude)", rc == 2 and "M0002" in err, (rc, err))
rc, _, err, dt = hook("idle_wake.py", S)
check("idle hook: the same mail is not announced twice", rc == 0, (rc, err))
open(os.path.join(DATA, "hooks", "OFF"), "w").close()
team("send", "--as", "codex", "--to", "claude", "--type", "ASK", "--re", "none", "--subject", "off test", "--body", "Are you there?")
rc, _, err, _ = hook("idle_wake.py", S)
check("idle hook: kill switch OFF", rc == 0 and not err, (rc, err))
os.remove(os.path.join(DATA, "hooks", "OFF"))
# ---------- mid-turn hook (Claude)
P = dict(S, hook_event_name="PostToolUse")
rc, out, _, _ = hook("midturn.py", P)
check("mid-turn hook: first run starts at the end (no backlog)", rc == 0 and out.strip() == "", out)
team("send", "--as", "codex", "--to", "claude", "--type", "RESULT", "--re", "none", "--subject", "tests green", "--body", "All 40 tests pass.")
rc, out, _, _ = hook("midturn.py", P)
ok = False
try:
    ok = "tests green" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
except Exception:
    pass
check("mid-turn hook: new mail shown to Claude mid-turn", rc == 0 and ok, out)
rc, out, _, _ = hook("midturn.py", P)
check("mid-turn hook: never shown twice", out.strip() == "", out)
team("send", "--as", "claude", "--to", "codex", "--type", "FYI", "--re", "none", "--subject", "mine", "--body", "own message")
rc, out, _, _ = hook("midturn.py", P)
check("mid-turn hook: Claude's own mail is not echoed", out.strip() == "", out)
rc, out, _, _ = hook("midturn.py", dict(P, cwd=TMP))
check("mid-turn hook: silent outside the work folders", out.strip() == "", out)
# ---------- health monitor
out = commhealth.tick(ps_out=ps2)
check("health: latency measured from real messages", any("claude->codex: seen" in line for line in out), out)
out = commhealth.tick(now=time.time() + 3600, ps_out=ps2)
check("health: mail unseen > 10 min and a stale daemon raise COMM DEGRADED", "COMM DEGRADED" in open(os.path.join(DATA, "wake", "alerts.jsonl")).read() and any("open incidents" in line and not line.endswith(": 0") for line in out), out)
# ---------- link check (receipts only)
VL = [sys.executable, os.path.join(ROOT, "team", "verify_link.py")]
os.makedirs(os.path.join(DATA, "hooks"), exist_ok=True)
json.dump({"codex_thread": THREAD, "partner_name": "codex", "claude_name": "claude"}, open(os.path.join(DATA, "binding.json"), "w"))
for e in ({"t": time.time() - 86400, "ids": ["M9999"]},):   # yesterday's receipts must never count
    open(os.path.join(DATA, "hooks", "midturn_log.jsonl"), "a").write(json.dumps(e) + "\n")
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
ch = get_json(os.path.join(DATA, "hooks", "link_challenge.json")) or {}
check("verify_link: first run sends a fresh challenge with a one-time code, not verified", r.returncode == 2 and ch.get("code") and ch.get("thread") == THREAD, r.stdout + r.stderr)
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: nothing proven yet -> NOT VERIFIED", r.returncode == 1 and "NOT VERIFIED" in r.stdout, r.stdout)
cid, code = ch["id"], ch["code"]
mk = "wake:linktest1"
json.dump({"wakes": [{"t": time.time(), "marker": mk, "delivered": "2026-01-01T00:00:01Z"}]}, open(os.path.join(DATA, "wake", f"{cid}.wakes.json"), "w"))
sub = subprocess.run([sys.executable, os.path.join(ROOT, "team", "team.py"), "send", "--as", "codex", "--to", "claude", "--type", "ANSWER",
                      "--re", cid, "--subject", "ok", "--body", f"code {code}"], capture_output=True, text=True, env=ENV).stdout.split()[1]
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: reply not in the bound chat's log (marker absent too) -> NOT VERIFIED", r.returncode == 1 and "FAIL 1." in r.stdout and "FAIL 2." in r.stdout, r.stdout)
def utc(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def send_event(mid, t):   # what Codex really logs when the chat runs team.py send (item_completed / CommandExecution)
    return {"timestamp": utc(t), "type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "CommandExecution", "command": ["/bin/zsh", "-lc", "python3 team/team.py send --as codex ..."],
        "aggregated_output": f"sent {mid}\n", "exit_code": 0}}}


with open(LOG, "a") as f:   # BEFORE the challenge: a send result and a body that merely quotes "sent <id>" must not count
    f.write(json.dumps({"timestamp": utc(ch["t"] - 600), "type": "event_msg", "payload": {"type": "user_message", "message": f"[{mk}] old"}}) + "\n")
    f.write(json.dumps(send_event(sub, ch["t"] - 600)) + "\n")
    f.write(json.dumps({"timestamp": utc(time.time()), "type": "response_item", "payload": {"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": f"I will reply: sent {sub}"}]}}) + "\n")
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: pre-challenge events and quoted text in the transcript do not count", r.returncode == 1 and "FAIL 1." in r.stdout and "FAIL 2." in r.stdout, r.stdout)
with open(LOG, "a") as f:
    f.write(json.dumps({"timestamp": utc(time.time()), "type": "event_msg", "payload": {"type": "user_message", "message": f"[{mk}] mail"}}) + "\n")
    f.write(json.dumps(send_event(sub, time.time())) + "\n")
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: marker + real send result from that chat but no hook receipt -> NOT VERIFIED", r.returncode == 1 and "PASS 1." in r.stdout and "PASS 2." in r.stdout and "FAIL 3." in r.stdout, r.stdout)
open(os.path.join(DATA, "hooks", "midturn_log.jsonl"), "a").write(json.dumps({"t": time.time(), "ids": [sub]}) + "\n")
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: a hook receipt without a Claude session does not count", r.returncode == 1 and "FAIL 3." in r.stdout, r.stdout)
open(os.path.join(DATA, "hooks", "midturn_log.jsonl"), "a").write(json.dumps({"t": time.time(), "ids": [sub], "session": "s-1"}) + "\n")
r = subprocess.run(VL, capture_output=True, text=True, env=ENV)
check("verify_link: all three proven after the challenge -> LINK VERIFIED", r.returncode == 0 and "LINK VERIFIED" in r.stdout and "s-1" in r.stdout, r.stdout)
# returning user: a verified pair is reinstalled (new install id) with the hooks offline -> must stay pending until a fresh round trip
c = json.load(open(CONFIG)); c["install_id"] = "reinstalled-1"; json.dump(c, open(CONFIG, "w"))
open(os.path.join(DATA, "hooks", "OFF"), "w").write("x")
r1 = subprocess.run(VL, capture_output=True, text=True, env=ENV)
r2 = subprocess.run(VL, capture_output=True, text=True, env=ENV)
ch2 = get_json(os.path.join(DATA, "hooks", "link_challenge.json")) or {}
check("verify_link: after a reinstall the old success never counts: new challenge, stays NOT VERIFIED with hooks offline",
      r1.returncode == 2 and ch2.get("id") != cid and ch2.get("install_id") == "reinstalled-1" and r2.returncode == 1 and "NOT VERIFIED" in r2.stdout,
      r1.stdout + r2.stdout)
os.remove(os.path.join(DATA, "hooks", "OFF"))
n = sum(RESULTS)
print(f"\n{n}/{len(RESULTS)} passed  (temp folder: {TMP})")
sys.exit(0 if n == len(RESULTS) else 1)
