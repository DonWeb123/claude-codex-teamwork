#!/usr/bin/env python3
"""The shared mailbox. One file per message, never edited after sending; acks kept separately; atomic writes + a lock.

  send --as ME --to WHO --type TYPE --subject S (--body TEXT | --body-file FILE) [--re ID|none] [--refs path:line,...] [--img FILE[::caption] ...] [--reply yes|no]
  inbox --as ME [--all]     unread messages for me (full text)
  open --as ME              messages that asked me for a reply I have not sent yet
  read ID                   show one message
  ack ID --as ME [--note T] mark as read and understood (silence is not agreement)
  claim NAME --as ME --what TEXT | release NAME --as ME | claims     who is editing what
  wait --as ME [--secs N]   return when a message for me is unread

Types that ask for a reply: ASK PROPOSE REVIEW BLOCK HANDOFF.  Others: ANSWER AGREE OBJECT RESULT FYI CLAIM.
Rules built in: max 220 words (put detail in files and point to them with --refs); a '?' needs a reply-wanted type;
if the other agent is waiting on you, say which message you answer (--re ID) or that this is a new topic (--re none)."""
import argparse, fcntl, hashlib, os, re, shutil, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import DATA, cfg, get_json, put_json, msgs, acked

C = cfg()
WHO = (C["claude_name"], C["partner_name"], "owner")
TYPES = "ASK ANSWER CLAIM PROPOSE AGREE OBJECT REVIEW RESULT BLOCK FYI HANDOFF".split()
WANT_REPLY = ("ASK", "PROPOSE", "REVIEW", "BLOCK", "HANDOFF")
MAX_WORDS = 220


def lock():
    f = open(os.path.join(DATA, "locks", ".lock"), "w")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def keep(path):
    """keep a hashed copy of a screenshot/evidence file so both agents open the same bytes"""
    path = os.path.expanduser(path)
    with open(path, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()[:12]
    rel = f"evidence/{h}{os.path.splitext(path)[1]}"
    if not os.path.exists(os.path.join(DATA, rel)):
        shutil.copyfile(path, os.path.join(DATA, rel))
    return rel


def show(m, full=True):
    print(f"[{m['id']}] {m['from']}->{m['to']} {m['type']} {m['ts']}" + (f" re {m['re']}" if m.get("re") else "")
          + (" REPLY WANTED" if m.get("reply") else "") + f"\n  {m['subject']}")
    if not full:
        return
    print("  " + m["body"].replace("\n", "\n  "))
    for r in m.get("refs", []):
        print("  ref", r)
    for x in m.get("img", []):
        print(f"  image {os.path.join(DATA, x['file'])}  ({x['caption']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    me = lambda p: p.add_argument("--as", dest="me", choices=WHO, required=True)
    p = sp.add_parser("send"); me(p)
    p.add_argument("--to", choices=WHO, required=True); p.add_argument("--type", choices=TYPES, required=True)
    p.add_argument("--subject", required=True)
    for a in ("--body", "--body-file", "--re", "--refs"):
        p.add_argument(a)
    p.add_argument("--img", nargs="*", default=[]); p.add_argument("--reply", choices=("auto", "yes", "no"), default="auto")
    p = sp.add_parser("inbox"); me(p); p.add_argument("--all", action="store_true")
    p = sp.add_parser("open"); me(p)
    p = sp.add_parser("wait"); me(p); p.add_argument("--secs", type=int, default=0)
    p = sp.add_parser("read"); p.add_argument("id")
    p = sp.add_parser("ack"); p.add_argument("id"); me(p); p.add_argument("--note", default="")
    p = sp.add_parser("claim"); p.add_argument("name"); me(p); p.add_argument("--what", required=True)
    p = sp.add_parser("release"); p.add_argument("name"); me(p)
    sp.add_parser("claims")
    a = ap.parse_args()
    # paths are built from these values: only plain ids/names, never '../' or '/'
    for k in ("id", "re"):
        v = getattr(a, k, None)
        if v and not (k == "re" and v == "none") and not re.fullmatch(r"M\d{4,6}", v):
            sys.exit(f"bad message id {v!r} (expected like M0042)")
    if getattr(a, "name", None) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", a.name):
        sys.exit(f"bad claim name {a.name!r}: letters, digits, . _ - only")

    if a.cmd == "send":
        body = a.body or ""
        if a.body_file:
            with open(os.path.expanduser(a.body_file)) as f:
                body = f.read()
        if a.me == a.to:
            sys.exit("cannot send to yourself")
        # Lesson: a question sent as RESULT/FYI did not ask for a reply, so nobody answered for 13 minutes.
        if a.reply == "auto" and a.type not in WANT_REPLY and ("?" in body or "?" in a.subject):
            sys.exit(f"this looks like a question ('?') but type {a.type} does not ask for a reply: use ASK/REVIEW, or pass --reply yes|no")
        M = msgs()
        # Lesson: replies sent without --re looked unanswered and raised false 'overdue' alarms.
        if a.re and a.re != "none":
            par = next((m for m in M if m.get("id") == a.re), None)
            if par is None:
                sys.exit(f"--re {a.re}: no such message")
            if {par.get("from"), par.get("to")} != {a.me, a.to}:
                sys.exit(f"--re {a.re} is between {par.get('from')} and {par.get('to')}, not {a.me} and {a.to}")
        elif not a.re and a.to != "owner":
            waiting = [m["id"] for m in M if m.get("from") == a.to and m.get("to") == a.me and m.get("reply")
                       and not any(x.get("from") == a.me and x.get("to") == a.to and x.get("re") == m["id"] for x in M)]
            if waiting:
                sys.exit(f"{a.to} is waiting for your reply to {', '.join(waiting[-5:])}: pass --re <id> if this answers one, or --re none for a new topic")
        if a.re == "none":
            a.re = None
        n = len(body.split())
        if not n:
            sys.exit("empty body")
        if n > MAX_WORDS:
            sys.exit(f"body is {n} words (limit {MAX_WORDS}): put the detail in a file and pass --refs path:line")
        with lock():
            ids = [int(m["id"][1:]) for m in msgs() if str(m.get("id", "")).startswith("M")]
            i = "M%04d" % (1 + max(ids or [0]))
            m = {"id": i, "ts": time.strftime("%m-%d %H:%M"), "t": time.time(), "from": a.me, "to": a.to, "type": a.type,
                 "subject": a.subject, "body": body.strip(), "re": a.re,
                 "refs": [r.strip() for r in (a.refs or "").split(",") if r.strip()],
                 "img": [{"file": keep(x.split("::")[0]), "caption": (x.split("::") + [""])[1]} for x in a.img],
                 "reply": a.type in WANT_REPLY if a.reply == "auto" else a.reply == "yes"}
            put_json(os.path.join(DATA, "msgs", f"{i}.json"), m)
        print("sent", i)
    elif a.cmd in ("inbox", "open"):
        M = msgs()
        if a.cmd == "inbox":
            L = [m for m in M if m["to"] == a.me and (a.all or not acked(m["id"], a.me))]
        else:
            L = [m for m in M if m["to"] == a.me and m.get("reply")
                 and not any(x["from"] == a.me and x.get("to") == m["from"] and x.get("re") == m["id"] for x in M)]
        for m in L:
            show(m, a.cmd == "inbox")
        print(f"{len(L)} message(s)")
    elif a.cmd == "read":
        m = get_json(os.path.join(DATA, "msgs", f"{a.id}.json"))
        if not m:
            sys.exit(f"no message {a.id}")
        show(m)
    elif a.cmd == "ack":
        if not os.path.exists(os.path.join(DATA, "msgs", f"{a.id}.json")):
            sys.exit(f"no message {a.id}")
        with open(os.path.join(DATA, "acks", f"{a.id}.{a.me}"), "w") as f:
            f.write(time.strftime("%m-%d %H:%M ") + a.note)
        print("acked", a.id)
    elif a.cmd in ("claim", "release"):
        with lock():
            path = os.path.join(DATA, "locks", f"{a.name}.json")
            cur = get_json(path)
            if cur and cur.get("by") not in (a.me, None):
                sys.exit(f"held by {cur['by']}: {cur.get('what')}")
            put_json(path, {"by": a.me if a.cmd == "claim" else None, "what": getattr(a, "what", ""), "ts": time.strftime("%m-%d %H:%M")})
        print(("claimed" if a.cmd == "claim" else "released"), a.name)
    elif a.cmd == "claims":
        for n in sorted(os.listdir(os.path.join(DATA, "locks"))):
            if n.endswith(".json"):
                c = get_json(os.path.join(DATA, "locks", n)) or {}
                if c.get("by"):
                    print(n[:-5], "held by", c["by"], "-", c.get("what"), c.get("ts"))
    elif a.cmd == "wait":
        t = time.time()
        while True:
            L = [m["id"] for m in msgs() if m["to"] == a.me and not acked(m["id"], a.me)]
            if L:
                print("NEW", " ".join(L)); return
            if a.secs and time.time() - t > a.secs:
                print("nothing new"); return
            time.sleep(2)


if __name__ == "__main__":
    main()
