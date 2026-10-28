#!/usr/bin/python3
"""Queues a finished torrent for conversion if it lies in a profile's folder
(profiles.json). Transmission runs it as script-torrent-done through a small
wrapper that passes --library; it can also be run by hand:

    enqueue.py --library /mnt/lib                       # Transmission: TR_TORRENT_DIR/TR_TORRENT_NAME
    enqueue.py --library /mnt/lib /mnt/lib/0unsort/0web/Show.S01
    enqueue.py --library /mnt/lib --profile bmw /mnt/lib/films/Rio.mkv

It only writes a job file into the queue on the share; worker.py does the rest.
Results go to 0conv/<profile>/ plus the source's path (see output_of).
See README.md."""

import argparse
import os
import posixpath
import sys

from ffmpegger import jobs

HERE = os.path.dirname(os.path.realpath(__file__))


def match(profiles, rel):
    """The profile whose folder holds rel, the deepest one; or None."""
    best = None
    for name, p in profiles.items():
        f = p["folder"].strip("/")
        if (rel == f or rel.startswith(f + "/")) and (best is None or len(f) > len(profiles[best]["folder"])):
            best = name
    return best


def output_of(cfg, profile, rel, is_dir):
    """Where the results go, relative to the library: 0conv/<profile>/ plus the source's folder,
    minus the profile folder or the downloads folder it was downloaded into:
        0unsort/0web/Show.S01      -> 0conv/web/Show.S01
        0unsort/0bmw/Rio.mkv       -> 0conv/bmw
        series/Better Call Saul    -> 0conv/bmw/series/Better Call Saul"""
    folder = rel if is_dir else posixpath.dirname(rel)
    prefixes = [p["folder"].strip("/") for p in cfg["profiles"].values()] + [cfg.get("downloads", "").strip("/")]
    inside = [p for p in prefixes if p and (folder == p or folder.startswith(p + "/"))]
    if inside:
        folder = folder[len(max(inside, key=len)):].strip("/")
    return posixpath.join(cfg.get("output", "0conv"), profile, folder).rstrip("/")


def log(library, line):
    """One line per decision into .convert/enqueue.log: Transmission keeps no output of the script."""
    line = "%s %s" % (jobs.now(), line)
    print(line)
    try:
        with open(os.path.join(library, jobs.QUEUE, "enqueue.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def main(argv=None):
    p = argparse.ArgumentParser(description="Queue a finished torrent for conversion.")
    p.add_argument("--library", required=True, help="library root on this server, the folder the share serves")
    p.add_argument("--profiles", default=os.path.join(HERE, "profiles.json"))
    p.add_argument("--profile", help="use this profile whatever the folder")
    p.add_argument("path", nargs="?", help="default: $TR_TORRENT_DIR/$TR_TORRENT_NAME")
    a = p.parse_args(argv)

    path = a.path
    if not path:
        if not os.environ.get("TR_TORRENT_DIR"):
            p.error("no path and no TR_TORRENT_DIR")
        path = os.path.join(os.environ["TR_TORRENT_DIR"], os.environ.get("TR_TORRENT_NAME", ""))
    library = os.path.realpath(a.library)
    rel = os.path.relpath(os.path.realpath(path), library).replace(os.sep, "/")
    if rel in (".", "..") or rel.startswith("../"):
        print("ERR: %s is outside the library %s" % (path, library), file=sys.stderr)
        return 1

    cfg = jobs.read(a.profiles)
    profiles = cfg["profiles"]
    name = a.profile or match(profiles, rel)
    jobs.ensure(library)
    if not name:
        log(library, "no profile, not queued: " + rel)
        return 0
    if name not in profiles:
        print("ERR: no profile %s, there are: %s" % (name, ", ".join(profiles)), file=sys.stderr)
        return 1
    for state in ("queue", "running"):
        for job_id, job in jobs.listing(library, state):
            if job.get("path") == rel and job.get("profile") == name:
                log(library, "already %s as %s: %s" % ("queued" if state == "queue" else "running", job_id, rel))
                return 0

    job_id = jobs.new_id(rel + "|" + name)  # one torrent can be queued for several profiles at once
    jobs.write(jobs.file(library, "queue", job_id), {
        "id": job_id,
        "path": rel,
        "profile": name,
        "output": output_of(cfg, name, rel, os.path.isdir(path)),
        "args": cfg.get("common", []) + profiles[name]["args"],
        "queued": jobs.now(),
        "server": os.uname().nodename if hasattr(os, "uname") else "",
    })
    log(library, "queued %s as %s: %s" % (name, job_id, rel))
    return 0


if __name__ == "__main__":
    sys.exit(main())
