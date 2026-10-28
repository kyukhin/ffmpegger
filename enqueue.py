#!/usr/bin/python3
"""What happens to a finished torrent. Transmission runs it as script-torrent-done
through a small wrapper that passes --library; it can also be run by hand.

The torrent's labels (set by the bot) say what to do, wherever it was downloaded:
    show:<name>       arrange it into series/<name>/sNN/ (arrange.py)
    film[:<title>]    arrange it into films/<title>/
    convert:<profile> queue a conversion, of the arranged season folders when it was arranged;
                      one label per profile
Without labels, a torrent inside a profile's folder (profiles.json) is queued with that profile.

    enqueue.py --library /mnt/lib                       # Transmission: TR_TORRENT_DIR, _NAME, _LABELS
    enqueue.py --library /mnt/lib /mnt/lib/0unsort/0web/Show.S01
    enqueue.py --library /mnt/lib --profile bmw /mnt/lib/films/Rio.mkv
    enqueue.py --library /mnt/lib --labels "show:Better Call Saul,convert:bmw" /mnt/lib/0unsort/BCS.S03

It only links files and writes job files into the queue on the share; worker.py
does the converting. Results go to 0conv/<profile>/ plus the source's path (see
output_of). See README.md."""

import argparse
import os
import posixpath
import sys

import arrange
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


def parse_labels(text):
    """"show:Better Call Saul,convert:bmw,convert:web" -> {"show": ..., "film": ..., "convert": [...]}.
    Transmission gives a script the labels comma separated; a label of its own (say "kids") is ignored."""
    out = {"show": None, "film": None, "convert": []}
    for label in (text or "").split(","):
        key, _, value = label.strip().partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "show" and value:
            out["show"] = value
        elif key == "film":
            out["film"] = value or True
        elif key == "convert" and value:
            out["convert"] += [v.strip() for v in value.split("+") if v.strip()]
    return out


def queue(library, cfg, rel, name, is_dir):
    """Writes the job unless the same source is already waiting or running for this profile."""
    for state in ("queue", "running"):
        for job_id, job in jobs.listing(library, state):
            if job.get("path") == rel and job.get("profile") == name:
                log(library, "already %s as %s: %s" % ("queued" if state == "queue" else "running", job_id, rel))
                return
    job_id = jobs.new_id(rel + "|" + name)  # one torrent can be queued for several profiles at once
    jobs.write(jobs.file(library, "queue", job_id), {
        "id": job_id,
        "path": rel,
        "profile": name,
        "output": output_of(cfg, name, rel, is_dir),
        "args": cfg.get("common", []) + cfg["profiles"][name]["args"],
        "queued": jobs.now(),
        "server": os.uname().nodename if hasattr(os, "uname") else "",
    })
    log(library, "queued %s as %s: %s" % (name, job_id, rel))


def main(argv=None):
    p = argparse.ArgumentParser(description="Queue a finished torrent for conversion.")
    p.add_argument("--library", required=True, help="library root on this server, the folder the share serves")
    p.add_argument("--profiles", default=os.path.join(HERE, "profiles.json"))
    p.add_argument("--profile", help="use this profile whatever the folder")
    p.add_argument("--labels", help="as Transmission's labels, comma separated; default: $TR_TORRENT_LABELS")
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
    labels = parse_labels(a.labels if a.labels is not None else os.environ.get("TR_TORRENT_LABELS"))
    names = [a.profile] if a.profile else labels["convert"]
    unknown = [n for n in names if n not in profiles]
    jobs.ensure(library)
    if unknown:
        log(library, "no profile %s (there are %s): %s" % (", ".join(unknown), ", ".join(profiles), rel))
        names = [n for n in names if n in profiles]

    # The sources to convert: the season or film folders it was arranged into, or the torrent itself
    sources = [(rel, os.path.isdir(path))]
    if labels["show"] or labels["film"]:
        try:
            record = arrange.arrange(library, path, show=labels["show"], film=bool(labels["film"]),
                                     title=labels["film"] if isinstance(labels["film"], str) else None)
        except (ValueError, OSError) as e:
            log(library, "cannot arrange %s: %s" % (rel, e))
            return 1
        arrange.save(library, record)
        log(library, "arranged %s: %d placed, %d there, %d not placed, into %s"
            % (rel, len(record["placed"]), len(record["there"]), len(record["unplaced"]) + len(record["clash"]),
               ", ".join(record["folders"]) or "nothing"))
        sources = [(f, True) for f in record["folders"]]
    elif not names:
        # No labels for us: the folder it was downloaded into decides, as before labels
        names = [n for n in [match(profiles, rel)] if n]
        if not names:
            log(library, "no profile, not queued: " + rel)

    for name in names:
        for source, is_dir in sources:
            queue(library, cfg, source, name, is_dir)
    return 1 if unknown else 0


if __name__ == "__main__":
    sys.exit(main())
