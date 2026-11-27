"""Shared helpers for the Claude + Codex teamwork kit. Plain files only: nothing is lost when an app restarts."""
import glob, json, os, shutil, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("TEAMKIT_DATA") or os.path.join(ROOT, "team-data")
CONFIG = os.environ.get("TEAMKIT_CONFIG") or os.path.join(ROOT, "config.json")
DEFAULTS = {"claude_name": "claude", "partner_name": "codex", "codex_thread": None, "work_dirs": [ROOT], "installed_at": None}
for d in ("msgs", "acks", "evidence", "locks", "wake", "hooks", "logs"):
    os.makedirs(os.path.join(DATA, d), exist_ok=True)


def cfg():
    c = dict(DEFAULTS)
    c.update(get_json(CONFIG, {}) or {})
    return c


def get_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def put_json(path, obj):
    """atomic write: a reader never sees half a file"""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp.")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, path)


def msgs():
    out = []
    for p in sorted(glob.glob(os.path.join(DATA, "msgs", "M*.json"))):
        m = get_json(p)
        if isinstance(m, dict):
            m["_created"] = os.path.getmtime(p)
            out.append(m)
    return out


def acked(mid, who):
    return os.path.exists(os.path.join(DATA, "acks", f"{mid}.{who}"))


def answered(m, M):
    """a reply only counts if it goes BACK to the sender, linked with --re"""
    return any(x.get("from") == m.get("to") and x.get("to") == m.get("from") and x.get("re") == m.get("id") for x in M)


def alert(key, kind, text, notify=True):
    """append one alert per stable key (never repeated); hooks surface it to Claude"""
    seen_p = os.path.join(DATA, "wake", "alert_keys.json")
    seen = get_json(seen_p, {}) or {}
    if key in seen:
        return False
    seen[key] = time.time()
    put_json(seen_p, seen)
    with open(os.path.join(DATA, "wake", "alerts.jsonl"), "a") as f:
        f.write(json.dumps({"ts": time.strftime("%F %T"), "t": time.time(), "key": key, "kind": kind, "text": text}) + "\n")
    if notify and os.environ.get("TEAMKIT_DRY") != "1" and shutil.which("osascript"):
        import subprocess
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text[:180])} with title {json.dumps('Team kit: ' + kind)}"], capture_output=True)
    return True


def in_work_dirs(cwd):
    if not cwd:
        return False
    cwd = os.path.realpath(cwd)
    for d in cfg()["work_dirs"]:
        d = os.path.realpath(os.path.expanduser(d))
        if cwd == d or cwd.startswith(d + os.sep):
            return True
    return False


def find_codex():
    """the Codex CLI: on PATH, or bundled inside the Codex VS Code / Cursor extension"""
    if os.environ.get("TEAMKIT_CODEX"):
        return os.environ["TEAMKIT_CODEX"]
    p = shutil.which("codex")
    if p:
        return p
    pats = ["~/.vscode/extensions/openai.chatgpt-*/bin/*/codex", "~/.cursor/extensions/openai.chatgpt-*/bin/*/codex",
            "/Applications/Codex.app/Contents/Resources/codex"]
    hits = [h for pat in pats for h in glob.glob(os.path.expanduser(pat)) if os.access(h, os.X_OK)]
    return sorted(hits, key=os.path.getmtime)[-1] if hits else None


def codex_home():
    return os.environ.get("TEAMKIT_CODEX_HOME") or os.path.expanduser("~/.codex")


BINDING = os.path.join(DATA, "binding.json")


def binding():
    """the chat + names this mailbox belongs to, or None if absent/invalid (every field required)"""
    b = get_json(BINDING)
    ok = isinstance(b, dict) and all(isinstance(b.get(k), str) and b.get(k).strip() for k in ("codex_thread", "partner_name", "claude_name"))
    return b if ok else None


def write_pid(name):
    """record a running daemon (pid + start time) so a manual stop can verify it is ours"""
    import subprocess
    st = subprocess.run(["ps", "-p", str(os.getpid()), "-o", "lstart="], capture_output=True, text=True).stdout.strip()
    put_json(os.path.join(DATA, "wake", f"{name}.pid.json"), {"pid": os.getpid(), "start": st})


def team_cmd():
    import shlex
    return "python3 " + shlex.quote(os.path.join(ROOT, "team", "team.py"))   # quoted: folders with spaces work
