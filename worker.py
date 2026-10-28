#!/usr/bin/python3
"""Takes conversion jobs from the queue on the library share and runs
convert_eng.py on them, one at a time. Made for a desktop that
is not always on: jobs wait in the queue while it is off, and a job cut by a
shutdown goes back to the queue on the next start and resumes with the episodes
not yet done. While a job runs, Windows is kept from sleeping.

    worker.py --library \\\\192.168.1.1\\lib          # Windows, started at logon by install-windows.ps1
    worker.py --library /Volumes/lib --once          # anywhere: empty the queue and exit

See README.md."""

import argparse
import contextlib
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import time

from ffmpegger import jobs

HERE = os.path.dirname(os.path.realpath(__file__))
CONVERT = os.path.join(HERE, "ffmpegger", "convert_eng.py")
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


def run(library, job_id, job, priority="normal"):
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
                code = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       env=dict(os.environ, PYTHONIOENCODING="utf-8:replace"), **priority_args(priority))
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
    p.add_argument("--once", action="store_true", help="exit when the queue is empty")
    a = p.parse_args(argv)
    setup_logging(a.log)
    log.info("started as %s on %s, %s priority, python %s", a.name, a.library, a.priority, sys.version.split()[0])

    requeued = False
    while True:
        try:
            if not requeued:
                requeue_own(a.library, a.name)
                requeued = True
            got = claim(a.library, a.name)
            if got:
                run(a.library, *got, priority=a.priority)
                continue
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
