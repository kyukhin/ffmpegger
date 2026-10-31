#!/usr/bin/python3
"""Takes conversion jobs from the queue on the library share and runs
convert_eng.py on them, one at a time. Made for a desktop that
is not always on: jobs wait in the queue while it is off, and a job cut by a
shutdown goes back to the queue on the next start and resumes with the episodes
not yet done. While a job runs, Windows is kept from sleeping. The worker
writes a heartbeat to .convert/workers/<name>.json and obeys power.py's commands
in .convert/control/<name>.json: off, or off when the queue is empty. While
.convert/control/<name>.pause.json is there it converts nothing: the running
converter is frozen where it is, no job is taken, and it goes on when the file
is gone. While .convert/control/<name>.low.json is there the converter runs at
idle priority on half of the CPUs, so a game comes first.

Started without --once it keeps itself current: before taking a job it does a
git pull of its checkout, and when the code changed it starts again on the new
code, so a job queued by a newer torrent server never meets an older converter.
It runs as a child of itself for that, see supervise.

    worker.py --library \\\\192.168.1.1\\lib          # Windows, started at boot by install-windows.ps1
    worker.py --library /Volumes/lib --once          # anywhere: empty the queue and exit

See README.md."""

import argparse
import contextlib
import logging
import logging.handlers
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import time

from ffmpegger import jobs

HERE = os.path.dirname(os.path.realpath(__file__))
CONVERT = os.path.join(HERE, "ffmpegger", "convert_eng.py")
CONTROL_EVERY = 20  # seconds between looks at the control files while converting
RESTART = 3  # the worker's exit code after a code update: the supervisor starts it again at once
STARTED = None  # set in main: when this worker started
CODE = None  # set in main: the commit it runs
CPU_LAST = None  # (idle, all) CPU time at the last look, for the load in between
# The worker decides where a job reads and writes, not the job file
FORBIDDEN = ("-d", "--directory", "-o", "--output", "-r", "--report")

log = logging.getLogger("worker")


def setup_logging(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handlers = [logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20, backupCount=3, encoding="utf-8")]
    if sys.stdout is not None and sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", handlers=handlers)


def default_log():
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ffmpegger", "worker.log")
    return os.path.join(os.path.expanduser("~"), ".local", "state", "ffmpegger", "worker.log")


@contextlib.contextmanager
def keep_awake():
    """Windows does not go to sleep while the block runs; elsewhere nothing."""
    if os.name != "nt":
        yield
        return
    import ctypes
    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)
    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)


def priority_args(priority):
    """Popen arguments. normal: full speed on every core. low: below normal priority, so games and
    the desktop come first, but then Windows 11 keeps the conversion on the efficiency cores.
    No console window on Windows. ffmpeg started by convert_eng.py inherits the priority."""
    if os.name == "nt":
        no_window, below_normal = 0x08000000, 0x00004000
        return {"creationflags": no_window | (below_normal if priority == "low" else 0)}
    return {"preexec_fn": lambda: os.nice(10)} if priority == "low" else {}


def python():
    """The console interpreter: under pythonw.exe the child would have no stdout to log."""
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":
        console = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.exists(console):
            return console
    return exe


def local_path(library, rel):
    return os.path.join(library, *rel.split("/"))


def output_of(library, job, src):
    """The results' folder and the extra converter arguments for it. A job names its folder, relative
    to the library (0conv/...), and gets the source's subfolders there; one queued before jobs named
    it gets 0out next to the source."""
    rel = job.get("output")
    if rel is None:
        return (os.path.join(src, "0out") if os.path.isdir(src) else os.path.join(os.path.dirname(src), "0out")), []
    parts = rel.split("/")
    if rel.startswith("/") or any(p in ("", ".", "..") for p in parts) or parts[0] == jobs.QUEUE:
        raise ValueError("bad output folder in the job: " + rel)
    return local_path(library, rel), ["--keep_tree"]


def logged_in():
    """Who is logged in to Windows (at the screen or over remote desktop): a sorted list, or None when
    that cannot be told (not Windows, or the API failed). power.py never switches off a machine with
    someone logged in, nor one it cannot tell about."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class SessionInfo(ctypes.Structure):
            _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR), ("State", ctypes.c_int)]

        wts = ctypes.WinDLL("wtsapi32")
        wts.WTSEnumerateSessionsW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                              ctypes.POINTER(ctypes.POINTER(SessionInfo)), ctypes.POINTER(wintypes.DWORD)]
        wts.WTSQuerySessionInformationW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_int,
                                                    ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
        wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        sessions, count = ctypes.POINTER(SessionInfo)(), wintypes.DWORD()
        if not wts.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(sessions), ctypes.byref(count)):
            return None
        users = set()
        try:
            for i in range(count.value):
                name, size = wintypes.LPWSTR(), wintypes.DWORD()
                # 5 = WTSUserName; session 0, where this worker runs before logon, has none
                if wts.WTSQuerySessionInformationW(None, sessions[i].SessionId, 5, ctypes.byref(name), ctypes.byref(size)):
                    if name.value:
                        users.add(name.value)
                    wts.WTSFreeMemory(name)
        finally:
            wts.WTSFreeMemory(sessions)
        return sorted(users)
    except Exception as e:  # never let this stop the worker
        log.info("cannot tell who is logged in: %s", e)
        return None


def load():
    """How busy the machine is, for the bot: {"cpu": % busy since the last call (from the second call on),
    "mem": % of memory in use, "mem_used", "mem_total": GB}. Windows only, None elsewhere."""
    global CPU_LAST
    if os.name != "nt":
        return None
    try:
        import ctypes

        class Memory(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32)] + \
                       [(n, ctypes.c_uint64) for n in ("TotalPhys", "AvailPhys", "TotalPageFile", "AvailPageFile",
                                                       "TotalVirtual", "AvailVirtual", "AvailExtendedVirtual")]

        k = ctypes.WinDLL("kernel32")
        m = Memory(dwLength=ctypes.sizeof(Memory))
        out = {}
        if k.GlobalMemoryStatusEx(ctypes.byref(m)):
            out.update(mem=m.dwMemoryLoad, mem_used=round((m.TotalPhys - m.AvailPhys) / 2 ** 30, 1),
                       mem_total=round(m.TotalPhys / 2 ** 30, 1))
        # FILETIMEs in 100 ns; kernel time includes idle time
        idle, kernel, user = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
        if k.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            now = (idle.value, kernel.value + user.value)
            if CPU_LAST and now[1] > CPU_LAST[1]:
                out["cpu"] = round(100 * (1 - (now[0] - CPU_LAST[0]) / (now[1] - CPU_LAST[1])))
            CPU_LAST = now
        return out or None
    except Exception as e:  # never let this stop the worker
        log.info("cannot tell the load: %s", e)
        return None


def address(library):
    """This machine's address towards the library's server: power.py pings it to tell a machine that was
    switched off from one whose heartbeat is merely a minute old."""
    host = library.replace("\\", "/").lstrip("/").split("/")[0] if library.startswith(("\\\\", "//")) else None
    if not host:
        return None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 9))  # UDP: nothing is sent, the route picks the address
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def git(*args):
    exe = shutil.which("git") or (r"C:\Program Files\Git\cmd\git.exe" if os.name == "nt" else "git")
    # Nobody to answer a password prompt, even less in session 0 before logon: fail instead
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
    return subprocess.run([exe] + list(args), cwd=HERE, capture_output=True, text=True, timeout=120, env=env,
                          stdin=subprocess.DEVNULL, **({"creationflags": 0x08000000} if os.name == "nt" else {}))


def version():
    """The commit this checkout is at, short; None when it is not a git checkout or git is missing."""
    try:
        r = git("rev-parse", "--short", "HEAD")
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def update():
    """git pull --ff-only of this checkout: True when the code changed. Without git or the network, or
    with local changes in the way, it says so in the log and the job runs on the code there is."""
    if not os.path.isdir(os.path.join(HERE, ".git")):
        return False
    try:
        before = git("rev-parse", "HEAD").stdout.strip()
        r = git("pull", "--ff-only", "--quiet")
        after = git("rev-parse", "HEAD").stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        log.info("cannot update the code: %s", e)
        return False
    if r.returncode:
        log.info("cannot update the code: %s", (r.stderr or r.stdout).strip()[-300:])
    if before != after:
        log.info("code updated from %s to %s", before[:7], after[:7])
    return before != after


def kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                            ctypes.c_void_p]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_size_t),
                                         ctypes.POINTER(ctypes.c_size_t)]
    return k


def kill_on_close(proc):
    """A Windows job object holding proc and everything it starts, all killed when the job's handle is
    closed: by release(), or by Windows when this process ends, however it ends. None elsewhere, or
    when Windows refuses (then nothing is killed, as before)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    try:
        k = kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = Extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        # 9: JobObjectExtendedLimitInformation
        if not (k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
                and k.AssignProcessToJobObject(job, int(proc._handle))):
            err = ctypes.get_last_error()
            k.CloseHandle(job)
            raise OSError(err, "job object")
        return job
    except Exception as e:  # never let this stop the worker
        log.info("the worker's processes are not tied to the supervisor: %s", e)
        return None


def release(job):
    if job:
        kernel32().CloseHandle(job)


def group(proc):
    """On Windows a job object holding proc and everything it starts, for members(); None elsewhere, or
    when Windows refuses (then a pause cannot freeze the converter, and says so)."""
    if os.name != "nt":
        return None
    try:
        k = kernel32()
        job = k.CreateJobObjectW(None, None)
        if job and not k.AssignProcessToJobObject(job, int(proc._handle)):
            k.CloseHandle(job)
            job = None
        return job or None
    except Exception as e:  # never let this stop the worker
        log.info("the converter cannot be paused: %s", e)
        return None


def members(proc, job):
    """Pids of proc and everything it started: its job object's list on Windows, its tree from ps elsewhere."""
    if os.name == "nt":
        if not job:
            return []
        import ctypes
        from ctypes import wintypes

        class Ids(ctypes.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("Listed", wintypes.DWORD), ("Ids", ctypes.c_size_t * 64)]

        ids = Ids()
        # 3: JobObjectBasicProcessIdList
        if not kernel32().QueryInformationJobObject(job, 3, ctypes.byref(ids), ctypes.sizeof(ids), None):
            return []
        return [ids.Ids[i] for i in range(ids.Listed)]
    children = {}
    for line in subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="], capture_output=True, text=True).stdout.splitlines():
        pid, ppid = map(int, line.split())
        children.setdefault(ppid, []).append(pid)
    found, todo = [], [proc.pid]
    while todo:
        found.append(todo.pop(0))
        todo += children.get(found[-1], [])
    return found


def freeze(pids, on):
    """Suspends (on) or resumes these processes: the pids it could."""
    done = []
    for pid in pids:
        try:
            if os.name == "nt":
                import ctypes
                k, nt = kernel32(), ctypes.WinDLL("ntdll")
                nt.NtSuspendProcess.argtypes = nt.NtResumeProcess.argtypes = [ctypes.c_void_p]
                handle = k.OpenProcess(0x0800, False, pid)  # PROCESS_SUSPEND_RESUME
                if not handle:
                    continue
                try:
                    if (nt.NtSuspendProcess if on else nt.NtResumeProcess)(handle) != 0:
                        continue
                finally:
                    k.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGSTOP if on else signal.SIGCONT)
            done.append(pid)
        except OSError:
            pass
    return done


def paused(library, name):
    return os.path.exists(jobs.pause_file(library, name))


def hold(library, name, proc, job, frozen):
    """Freezes the converter when the worker is paused and lets it go when not. Returns the pids frozen now:
    None when not paused, [] when paused but nothing could be frozen (it converts on, the heartbeat says so)."""
    if paused(library, name) and frozen is None:
        tried, frozen = [], []
        for _ in range(3):  # again for a process started while the others froze
            new = [p for p in members(proc, job) if p not in tried]
            if not new:
                break
            tried += new
            frozen += freeze(new, True)
        log.info("paused: %s", "%d processes frozen" % len(frozen) if frozen else "nothing could be frozen")
        return frozen
    if frozen is not None and not paused(library, name):
        freeze(reversed(frozen), False)
        log.info("resumed")
        return None
    return frozen


def cpus():
    """This machine's logical processors, by number."""
    if os.name == "nt":
        import ctypes
        k, mine, every = kernel32(), ctypes.c_size_t(), ctypes.c_size_t()
        if k.GetProcessAffinityMask(k.GetCurrentProcess(), ctypes.byref(mine), ctypes.byref(every)):
            return [i for i in range(64) if every.value >> i & 1]
    if hasattr(os, "sched_getaffinity"):
        return sorted(os.sched_getaffinity(0))
    return list(range(os.cpu_count() or 1))


def lowered(library, name):
    """The CPUs the converter may use while .convert/control/<name>.low.json is there, else None: the last
    share of them (cpus in the file, half by default). Intel's hybrid CPUs number the P-cores' threads first,
    so on the i9-14900KS (8 P-cores with two threads each, then 16 E-cores) half is the E-cores, and the
    P-cores stay with the game."""
    path = jobs.low_file(library, name)
    if not os.path.exists(path):
        return None
    try:
        share = float(jobs.read(path).get("cpus", 0.5))
    except (OSError, ValueError, TypeError, AttributeError):
        share = 0.5
    every = cpus()
    return every[-max(1, min(len(every), round(len(every) * share))):]


def lower(pids, use, priority):
    """Idle priority on the CPUs `use` for these processes; with use None back to the worker's priority on
    every CPU. The pids it could. Elsewhere than Windows only root may raise a priority again: there a
    lowered converter gets every CPU back but stays at idle priority until its job ends."""
    done = []
    for pid in pids:
        try:
            if os.name == "nt":
                k = kernel32()
                handle = k.OpenProcess(0x0600, False, pid)  # PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION
                if not handle:
                    continue
                try:
                    # IDLE, BELOW_NORMAL, NORMAL_PRIORITY_CLASS
                    level = 0x40 if use else 0x4000 if priority == "low" else 0x20
                    if not (k.SetPriorityClass(handle, level)
                            and k.SetProcessAffinityMask(handle, sum(1 << c for c in use or cpus()))):
                        continue
                finally:
                    k.CloseHandle(handle)
            else:
                if hasattr(os, "sched_setaffinity"):
                    os.sched_setaffinity(pid, use or cpus())
                os.setpriority(os.PRIO_PROCESS, pid, 19 if use else 10 if priority == "low" else 0)
            done.append(pid)
        except OSError:
            pass
    return done


def ease(library, name, proc, job, eased, priority):
    """Keeps the converter and everything it starts low while the worker is lowered (see lowered), each time
    also for processes started meanwhile, and puts it back once when not. Returns the CPUs it may use now:
    None when not lowered, [] when lowered but no process could be (it converts on, the heartbeat says so)."""
    use = lowered(library, name)
    if use:
        done = lower(members(proc, job), use, priority)
        got = use if done else []
        if got != eased:
            if done:
                log.info("lowered: idle priority on %d of %d CPUs, %d processes", len(use), len(cpus()), len(done))
            else:
                log.info("lowered, but no process could be")
        return got
    if eased:
        lower(members(proc, job), None, priority)
        log.info("back to %s priority on every CPU", priority)
    return None


def supervise(argv):
    """What the scheduled task runs: the worker itself as a child process, started again at once when it
    exits for new code and a minute later when it dies. On Windows each child is in a job object (see
    kill_on_close): a converter left by a worker that died is killed before the next one takes the job
    back, and stopping the task stops the conversion too. The supervisor itself is only replaced by a
    restart of the task or of the machine."""
    log.info("supervisor started, the worker runs as its child")
    while True:
        child = subprocess.Popen([sys.executable, os.path.realpath(__file__), "--child"] + argv,
                                 stdin=subprocess.DEVNULL)
        job = kill_on_close(child)
        code = child.wait()
        release(job)
        if code == RESTART:
            log.info("starting the worker again on the new code")
            continue
        log.info("the worker exited with code %s, starting it again in a minute", code)
        time.sleep(60)
        update()  # a fix pushed for what killed it


def heartbeat(library, name, state, job_id=None, low=None):
    """When this worker was last seen, what it does, who is logged in, its address and load, for power.py
    and the bot. state "off": it is shutting the machine down. low: the CPUs of a lowered worker."""
    path = jobs.worker_file(library, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    jobs.write(path, {"name": name, "seen": jobs.now(), "state": state, "job": job_id, "since": STARTED, "code": CODE,
                      "users": logged_in(), "ip": address(library), "load": load(), "pid": os.getpid(),
                      "low": {"cpus": len(low), "of": len(cpus())} if low else None, "os": platform.platform()})


def command(library, name):
    """The action power.py left for this worker (off, off-when-idle), or None."""
    try:
        return jobs.read(jobs.control_file(library, name)).get("action")
    except (OSError, ValueError):
        return None


def power_off(library, name, why):
    """Takes the command away first, so the machine does not switch itself off again when it boots."""
    try:
        os.remove(jobs.control_file(library, name))
    except OSError:
        pass
    log.info("switching off: %s", why)
    try:
        heartbeat(library, name, "off")
    except OSError:
        pass
    if os.name == "nt":
        # A minute to cancel with `shutdown /a`; a running conversion goes back to the queue on the next start
        subprocess.call(["shutdown", "/s", "/t", "60", "/c", "ffmpegger: " + why], creationflags=0x08000000)
    else:
        subprocess.call(["sudo", "-n", "shutdown", "-h", "+1"])


def requeue_own(library, name):
    """Jobs this machine was running when it went down go back to the queue."""
    for job_id, job in jobs.listing(library, "running"):
        if job.get("worker") == name and jobs.move(library, job_id, "running", "queue"):
            log.info("back to the queue after an interruption: %s %s", job_id, job["path"])


def claim(library, name):
    """The oldest waiting job, now ours; or None."""
    for job_id, job in jobs.listing(library, "queue"):
        if jobs.move(library, job_id, "queue", "running"):
            job.update(worker=name, taken=jobs.now(), attempts=job.get("attempts", 0) + 1)
            jobs.write(jobs.file(library, "running", job_id), job)
            return job_id, job
    return None


def summary(report_path):
    """What the bot and a human need from convert_eng.py's report."""
    try:
        r = jobs.read(report_path)
    except (OSError, ValueError):
        return {}
    return {"status": r.get("status"), "total": r.get("total"), "counts": r.get("counts"),
            "problems": [{"video": v["video"], "status": v["status"], "reason": v["reason"]}
                         for v in r.get("videos", []) if v["status"] in ("skipped", "failed")]}


def run(library, job_id, job, priority="normal", name=None):
    src = local_path(library, job["path"])
    report = jobs.file(library, "running", job_id, ".report.json")
    bad = [a for a in job.get("args", []) if a.split("=")[0] in FORBIDDEN]
    try:
        out, extra = output_of(library, job, src)
    except ValueError as e:
        out, extra, bad = None, [], [str(e)]
    if not os.path.exists(src):
        code, reason = None, "the source is gone: " + job["path"]
    elif bad:
        code, reason = None, "the job sets paths itself: " + " ".join(bad)
    else:
        # On Windows the library is a share: one local copy instead of four reads over the network
        extra += ["--local_copy"] if os.name == "nt" else []
        cmd = [python(), CONVERT, "-d", src, "-o", out, "-r", report] + extra + job.get("args", [])
        log.info("converting %s %s (%s)", job_id, job["path"], job.get("profile"))
        started = time.time()
        with open(jobs.file(library, "running", job_id, ".log"), "a", encoding="utf-8") as f:
            f.write("=== %s on %s: %s\n" % (jobs.now(), job.get("worker"), subprocess.list2cmdline(cmd)))
            f.flush()
            with keep_awake():
                proc = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        env=dict(os.environ, PYTHONIOENCODING="utf-8:replace"),
                                        **priority_args(priority))
                procs, frozen, eased = group(proc), None, None
                # Converting can take hours: keep the heartbeat going, a pause, a lowering and an "off" obeyed
                # meanwhile, from the start
                while True:
                    if name:
                        try:
                            frozen = hold(library, name, proc, procs, frozen)
                            eased = ease(library, name, proc, procs, eased, priority)
                            heartbeat(library, name, "paused" if frozen else "converting", job_id, eased)
                            if command(library, name) == "off":
                                power_off(library, name, "asked to switch off while converting " + job["path"])
                        except OSError:
                            pass
                    try:
                        code = proc.wait(CONTROL_EVERY)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                release(procs)
        reason = "" if code == 0 else "convert_eng.py exit code %d" % code
        job["seconds"] = round(time.time() - started)
    job.update(finished=jobs.now(), exit=code, reason=reason, result=summary(report))
    state = "done" if code == 0 else "failed"
    jobs.write(jobs.file(library, "running", job_id), job)
    jobs.move(library, job_id, "running", state)
    log.info("%s %s %s %s", state, job_id, job["path"], reason)


def main(argv=None):
    p = argparse.ArgumentParser(description="Convert jobs from the queue on the library share.")
    p.add_argument("--library", required=True, help="the library share as this machine sees it")
    p.add_argument("--poll", type=int, default=60, help="seconds between looks at an empty queue")
    p.add_argument("--priority", choices=("normal", "low"), default="normal",
                   help="low: games first, but on a hybrid CPU only the efficiency cores convert")
    p.add_argument("--name", default=socket.gethostname(), help="this worker's name in the jobs")
    p.add_argument("--log", default=default_log())
    p.add_argument("--once", action="store_true", help="exit when the queue is empty, without updating the code")
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)  # run by supervise
    a = p.parse_args(argv)
    global STARTED, CODE
    STARTED, CODE = jobs.now(), version()
    if not (a.once or a.child):
        # A log of its own: two processes rotating one file fail on Windows
        setup_logging(os.path.splitext(a.log)[0] + "-supervisor.log")
        return supervise(sys.argv[1:] if argv is None else argv)
    setup_logging(a.log)
    log.info("started as %s on %s, %s priority, python %s, code %s",
             a.name, a.library, a.priority, sys.version.split()[0], CODE)

    requeued = False
    while True:
        try:
            if not requeued:
                requeue_own(a.library, a.name)
                requeued = True
            stop = paused(a.library, a.name)
            heartbeat(a.library, a.name, "paused" if stop else "idle", low=lowered(a.library, a.name))
            action = command(a.library, a.name)
            if action == "off":
                power_off(a.library, a.name, "asked to switch off")
            # The job was queued with the torrent server's code: take it on the newest code there is
            if not stop and a.child and jobs.listing(a.library, "queue") and update():
                return RESTART
            got = None if stop else claim(a.library, a.name)
            if got:
                heartbeat(a.library, a.name, "converting", got[0], lowered(a.library, a.name))
                run(a.library, *got, priority=a.priority, name=a.name)
                continue
            if action == "off-when-idle" and not stop:
                power_off(a.library, a.name, "the queue is empty")
        except OSError as e:
            # The share is not there yet (just booted, network down): try again later,
            # and take back what was running here when it went away
            log.info("library unavailable: %s", e)
            requeued = False
        if a.once:
            return 0
        time.sleep(a.poll)


if __name__ == "__main__":
    sys.exit(main())
