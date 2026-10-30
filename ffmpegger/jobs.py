"""The conversion queue on the library share, used by enqueue.py on the torrent
server and by worker.py on the machine that converts. A job is a JSON file
that moves between folders by renaming, which the file server does atomically,
also over SMB:

    .convert/queue/    waiting
    .convert/running/  taken by a worker; its .log and .report.json grow here
    .convert/done/     everything converted or there already
    .convert/failed/   something skipped or failed, see the .log and .report.json
    .convert/workers/<name>.json  each worker's heartbeat: when it was last seen, what it does
    .convert/control/<name>.json  a command for a worker from power.py: off, off-when-idle
    .convert/control/<name>.pause.json  there while the worker is paused: it converts nothing until removed
    .convert/power/<name>.json    power.py auto's memory for a worker: whether it woke it, events

Paths inside a job are relative to the library root with / as the separator,
so the server (/mnt/lib) and Windows (\\\\server\\lib) read them the same way."""

import datetime
import hashlib
import json
import os

QUEUE = ".convert"
STATES = ("queue", "running", "done", "failed")
# Files of a job besides <id>.json, moved along with it
EXTRAS = (".log", ".report.json")


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def folder(library, state):
    return os.path.join(library, QUEUE, state)


def ensure(library):
    for state in STATES:
        os.makedirs(folder(library, state), exist_ok=True)


def new_id(path):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return stamp + "-" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:6]


def write(path, data):
    """Readers never see half a file: written aside, then renamed over."""
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def listing(library, state):
    """[(id, job)] in a state, oldest first (ids start with the time)."""
    out = []
    try:
        names = sorted(os.listdir(folder(library, state)))
    except FileNotFoundError:
        return out
    for name in names:
        if name.startswith(".") or not name.endswith(".json") or name.endswith(".report.json"):
            continue
        try:
            out.append((name[:-5], read(os.path.join(folder(library, state), name))))
        except (OSError, ValueError):
            pass  # renamed away meanwhile, or not ours
    return out


def worker_file(library, name):
    return os.path.join(library, QUEUE, "workers", name + ".json")


def control_file(library, name):
    return os.path.join(library, QUEUE, "control", name + ".json")


def pause_file(library, name):
    return os.path.join(library, QUEUE, "control", name + ".pause.json")


def power_file(library, name):
    return os.path.join(library, QUEUE, "power", name + ".json")


def file(library, state, job_id, ext=".json"):
    return os.path.join(folder(library, state), job_id + ext)


def move(library, job_id, src, dst):
    """Moves a job between states; False if someone else moved it first.
    The .json goes first: whoever renames it owns the job."""
    try:
        os.replace(file(library, src, job_id), file(library, dst, job_id))
    except FileNotFoundError:
        return False
    for ext in EXTRAS:
        try:
            os.replace(file(library, src, job_id, ext), file(library, dst, job_id, ext))
        except FileNotFoundError:
            pass
    return True
