"""Small cross-platform layer (macOS, Linux, Windows): file locks, process inspection, notifications, command quoting.
Standard library only."""
import json, os, shutil, subprocess, sys, time

WINDOWS = sys.platform == "win32"
if WINDOWS:
    import msvcrt
else:
    import fcntl


class Busy(Exception):
    """a non-blocking lock is held by someone else"""


def lock(path, blocking=True, timeout=30.0):
    """exclusive lock on `path`; keep the returned file open for as long as you need the lock"""
    f = open(path, "a+")
    if not WINDOWS:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError:
            f.close(); raise Busy(path)
        return f
    t0 = time.time()
    while True:
        try:
            f.seek(0); msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        except OSError:
            if not blocking or time.time() - t0 > timeout:
                f.close(); raise Busy(path)
            time.sleep(0.05)


def _ps(script, **env):
    """run a PowerShell script. Paths go in through environment variables (arguments after -Command are NOT $args).
    PSModulePath is dropped: one inherited from PowerShell 7 stops Windows PowerShell 5.1 from loading its own modules
    (Get-Acl then fails with 'module Microsoft.PowerShell.Security could not be loaded')."""
    exe = shutil.which("powershell") or shutil.which("pwsh") or "powershell"
    e = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}; e.update(env)
    return subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=60, env=e)


def _powershell(script):
    r = _ps(script)
    if r.returncode != 0:
        return None
    try:
        d = json.loads(r.stdout or "null")
    except Exception:
        return None
    return [] if d is None else (d if isinstance(d, list) else [d])


def proc_info(pid):
    """{'args': command line, 'start': start-time text} for a RUNNING process, None if it is gone (zombies count as gone),
    raises OSError if the process table cannot be inspected"""
    if os.environ.get("TEAMKIT_SIMULATE_PS_FAILURE") == "1":   # tests: behave as if the process table cannot be read
        raise OSError("simulated inspection failure")
    if WINDOWS:
        rows = _powershell(f"Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' | Select-Object CommandLine,CreationDate | ConvertTo-Json -Compress")
        if rows is None:
            raise OSError("process inspection failed")
        if not rows:
            return None
        return {"args": rows[0].get("CommandLine") or "", "start": str(rows[0].get("CreationDate"))}
    ps = lambda field: subprocess.run(["ps", "-ww", "-p", str(int(pid)), "-o", field + "="], capture_output=True, text=True, timeout=10)
    st = ps("stat")
    if st.returncode not in (0, 1):
        raise OSError("ps failed")
    if not st.stdout.strip() or st.stdout.strip().startswith("Z"):
        return None
    return {"args": ps("args").stdout.strip(), "start": ps("lstart").stdout.strip()}


def gone(pid):
    try:
        return proc_info(pid) is None
    except OSError:
        return False   # cannot tell: never claim it stopped


def my_start():
    try:
        return (proc_info(os.getpid()) or {}).get("start", "")
    except OSError:
        return ""


def kill(pid):
    import signal
    try:
        os.kill(int(pid), signal.SIGTERM)   # on Windows this terminates the process
    except (ProcessLookupError, OSError):
        pass


def parse_ps_appservers(ps_out):
    """POSIX `ps -axo pid=,lstart=,command=` text -> sorted 'pid start' strings of Codex app-servers"""
    r = []
    for line in ps_out.splitlines():
        f = line.split()
        if len(f) < 7:
            continue
        exe, args = f[6], f[7:]
        if os.path.basename(exe) in ("codex", "codex.exe") and "app-server" in args:
            r.append(" ".join(f[:6]))
    return sorted(r)


def codex_appservers():
    """sorted identities of running Codex app-server processes; None if the process table cannot be inspected"""
    if WINDOWS:
        rows = _powershell("Get-CimInstance Win32_Process -Filter \"Name like 'codex%'\" | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress")
        if rows is None:
            return None
        return sorted(f"{x.get('ProcessId')} {x.get('CreationDate')}" for x in rows if "app-server" in (x.get("CommandLine") or ""))
    try:
        r = subprocess.run(["ps", "-axo", "pid=,lstart=,command="], capture_output=True, text=True, timeout=10)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return parse_ps_appservers(r.stdout)


def args_has(args, path):
    """does a process command line mention this file? (slash- and, on Windows, case-insensitive)"""
    norm = lambda x: str(x).replace("\\", "/").lower() if WINDOWS else str(x).replace("\\", "/")
    return norm(os.path.abspath(path)) in norm(args or "")


def acl_sddl(path):
    """Windows: the file's security descriptor as SDDL text (None if it cannot be read). Other systems: None."""
    if not WINDOWS:
        return None
    r = _ps("(Get-Acl -LiteralPath $env:TK_P).Sddl", TK_P=path)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


_RULES = r"""((Get-Acl -LiteralPath $env:TK_P).Access | ForEach-Object { "$($_.IdentityReference)|$([int]$_.FileSystemRights)|$($_.AccessControlType)" } | Sort-Object -Unique) -join ';'"""


def acl_rules(path):
    """Windows: who may do what to the file (effective access rules, sorted text). None if unreadable / not Windows."""
    if not WINDOWS:
        return None
    r = _ps(_RULES, TK_P=path)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


_COPY_ACL = r"""
$ErrorActionPreference = 'Stop'
function Rules($p) { ((Get-Acl -LiteralPath $p).Access | ForEach-Object { "$($_.IdentityReference)|$([int]$_.FileSystemRights)|$($_.AccessControlType)" } | Sort-Object -Unique) -join ';' }
$src = Get-Acl -LiteralPath $env:TK_SRC
$want = Rules $env:TK_SRC
$dst = Get-Acl -LiteralPath $env:TK_DST
$dst.SetSecurityDescriptorSddlForm($src.GetSecurityDescriptorSddlForm('Access'), 'Access')
Set-Acl -LiteralPath $env:TK_DST -AclObject $dst
if ((Rules $env:TK_DST) -ne $want) {
  # different folder, different inherited entries: freeze the source's effective rules onto the file instead
  $src.SetAccessRuleProtection($true, $true)
  $dst = Get-Acl -LiteralPath $env:TK_DST
  $dst.SetSecurityDescriptorSddlForm($src.GetSecurityDescriptorSddlForm('Access'), 'Access')
  Set-Acl -LiteralPath $env:TK_DST -AclObject $dst
}
if ((Rules $env:TK_DST) -ne $want) { Write-Error "permissions differ"; exit 3 }
"""


LAST_ERROR = ""   # why the last copy_protection failed (Windows)


def copy_protection(src, dst):
    """give dst the same protection as src: POSIX mode bits; on Windows the same effective access rules (verified).
    False = could not."""
    if not WINDOWS:
        try:
            shutil.copymode(src, dst); return True
        except OSError:
            return False
    global LAST_ERROR
    try:
        r = _ps(_COPY_ACL, TK_SRC=os.path.abspath(src), TK_DST=os.path.abspath(dst))
    except (OSError, subprocess.SubprocessError) as e:
        LAST_ERROR = str(e); return False
    LAST_ERROR = (r.stderr or r.stdout).strip()[-400:]
    return r.returncode == 0


def notify(title, text):
    if os.environ.get("TEAMKIT_DRY") == "1":
        return
    if sys.platform == "darwin" and shutil.which("osascript"):
        subprocess.run(["osascript", "-e", f"display notification {json.dumps(text[:180])} with title {json.dumps(title)}"], capture_output=True)


def q(path):
    """double-quoted path with forward slashes: works in Git Bash, cmd and POSIX shells alike"""
    return '"' + str(path).replace("\\", "/").replace('"', '\\"') + '"'


def python_exe(windowless=False):
    exe = sys.executable
    if windowless and WINDOWS:
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(w):
            return w
    return exe
