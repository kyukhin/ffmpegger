#!/usr/bin/python3
"""Takes the release junk out of the names of videos that are there already, their subtitles and dubs
along, and sets the title of MP4 files to their name: what arrange.py and convert_eng.py do with every
new download, for the old ones and for any folder.

    cleanup-names.py --dry-run /Volumes/usb/films
    cleanup-names.py --library /mnt/lib --profiles ~/ffmpegger/profiles.json films/0series

The.Big.Bang.Theory.S03E06.1080p.BluRay.Rus.Eng.TeamHD.mkv -> The.Big.Bang.Theory.S03E06.mkv, its
.en.srt and .ru.mka along; lectures numbered without a season get leading zeros (Лекция #5 ->
Лекция #05) so that they sort. With --library and --profiles the results converted from a video in the
library follow it in every profile's folder: 0conv/bmw/<the same path>/The.Big.Bang.Theory.S03E06.mp4,
titled "The Big Bang Theory S03E06". The library's downloads and output folders are not walked.

A name taken already is left alone and reported. A file with another hard link (a torrent's file
in the library) is renamed but never written to: its title stays. Titles need mutagen
(python3-mutagen on Debian). See README.md."""

import argparse
import json
import os
import sys

from ffmpegger.convert_eng import (AUDIO_EXTS, SUBS_EXTS, VIDEO_EXTS, clean_name, number_width, tidy_name,
                                   title_of)

try:
    from mutagen.mp4 import MP4
except ImportError:
    MP4 = None

FIXUP = ("-f", "--fixup_names")


def plan(folder):
    """What to do in a folder: [(video's name, its name to be)] for every video, [(old name, new name)]
    renames with the subtitles and dubs, [(name, the name it cannot take)] clashes."""
    files = sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)))
    videos = [f for f in files if f.lower().endswith(VIDEO_EXTS)]
    stems = {f: os.path.splitext(f)[0] for f in videos}
    width = number_width(list(stems.values()))
    new = {f: tidy_name(stems[f], width) for f in videos}
    # A subtitle or dub belongs to the video with the longest name it starts with
    owner = {}
    for f in files:
        if f.lower().endswith(SUBS_EXTS + AUDIO_EXTS):
            mine = [v for v in videos if f.startswith(stems[v] + ".")]
            if mine:
                owner[f] = max(mine, key=len)
    targets = [(new[f] + os.path.splitext(f)[1]).lower() for f in videos]
    taken = {f.lower() for f in files}
    episodes, renames, clashes = [], [], []
    for f in videos:
        group = [(f, new[f] + os.path.splitext(f)[1])]
        group += [(c, new[f] + c[len(stems[f]):]) for c in files if owner.get(c) == f]
        if new[f] == stems[f]:
            episodes.append((f, f))
            continue
        bad = [n for o, n in group if n.lower() in taken] + \
            ([group[0][1]] if targets.count(group[0][1].lower()) > 1 else [])
        if bad:
            clashes.append((f, bad[0]))
            episodes.append((f, f))
            continue
        episodes.append(group[0])
        renames += group
    return episodes, renames, clashes


def set_title(path, title, dry_run):
    """True when the MP4's title is not title yet (and now is, unless dry_run)."""
    f = MP4(path)
    if f.tags is not None and f.tags.get("\xa9nam") == [title]:
        return False
    if not dry_run:
        if f.tags is None:
            f.add_tags()
        f.tags["\xa9nam"] = [title]
        f.save()
    return True


def cleanup(paths, library=None, profiles=None, dry_run=False, out=print):
    """Renames and titles under paths; returns the counts."""
    counts = {"renamed": 0, "titled": 0, "clashes": 0, "kept": 0}
    outputs, skip = [], set()
    if library:
        library = os.path.realpath(library)
        root = os.path.join(library, profiles.get("output", "0conv"))
        skip = {root, os.path.join(library, profiles.get("downloads", "0unsort"))}
        for name, p in sorted(profiles["profiles"].items()):
            fixup = any(a in FIXUP for a in profiles.get("common", []) + p.get("args", []))
            outputs.append((os.path.join(root, name), fixup))

    def rel(path):
        return os.path.relpath(path, library) if library else path

    def rename(folder, old, new):
        out("rename %s -> %s" % (rel(os.path.join(folder, old)), new))
        if not dry_run:
            os.rename(os.path.join(folder, old), os.path.join(folder, new))
        counts["renamed"] += 1

    def title(path, text):
        if os.stat(path).st_nlink > 1:
            # Hardlinked: maybe a torrent's file, which must stay as downloaded
            counts["kept"] += 1
        elif set_title(path, text, dry_run):
            out("title %s: %s" % (rel(path), text))
            counts["titled"] += 1

    for top in paths:
        top = os.path.realpath(top)
        if library and os.path.relpath(top, library).split(os.sep)[0] == "..":
            raise ValueError("%s is outside the library %s" % (top, library))
        for folder, dirs, _ in os.walk(top):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and os.path.join(folder, d) not in skip)
            if folder in skip:
                continue
            episodes, renames, clashes = plan(folder)
            for f, n in clashes:
                out("clash %s: %s is taken" % (rel(os.path.join(folder, f)), n))
                counts["clashes"] += 1
            # The results first: cut short, a run again still finds the sources under their old names
            for out_root, fixup in outputs:
                there = os.path.join(out_root, os.path.relpath(folder, library))
                if not os.path.isdir(there):
                    continue
                for old, new in episodes:
                    names = [os.path.splitext(n)[0] + ".mp4" for n in (old, new)]
                    old_out, new_out = [clean_name(n) for n in names] if fixup else names
                    now = os.path.join(there, new_out)
                    if old_out != new_out and os.path.exists(os.path.join(there, old_out)):
                        if os.path.exists(now):
                            out("clash %s: %s is taken" % (rel(os.path.join(there, old_out)), new_out))
                            counts["clashes"] += 1
                            continue
                        rename(there, old_out, new_out)
                        now = os.path.join(there, old_out if dry_run else new_out)
                    if os.path.exists(now):
                        title(now, title_of(os.path.splitext(new)[0]))
            for old, new in renames:
                rename(folder, old, new)
            for old, new in episodes:
                if new.lower().endswith(".mp4"):
                    title(os.path.join(folder, old if dry_run else new), title_of(os.path.splitext(new)[0]))
    return counts


def main(argv=None):
    p = argparse.ArgumentParser(description="Take the release junk out of video names, set MP4 titles.")
    p.add_argument("--library", help="the library root: the results of conversion follow their videos")
    p.add_argument("--profiles", help="profiles.json, for the output folder and the profiles in it")
    p.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    p.add_argument("paths", nargs="+", help="folders to clean up, inside --library when it is given")
    a = p.parse_args(argv)
    if bool(a.library) != bool(a.profiles):
        p.error("--library and --profiles go together")
    if MP4 is None:
        print("ERR: no mutagen for the MP4 titles: apt install python3-mutagen, or pip install mutagen",
              file=sys.stderr)
        return 2
    profiles = None
    if a.profiles:
        with open(a.profiles, encoding="utf-8") as f:
            profiles = json.load(f)
    paths = [os.path.join(a.library, x) if a.library and not os.path.isabs(x) else x for x in a.paths]
    missing = [x for x in paths if not os.path.isdir(x)]
    if missing:
        p.error("not a folder: " + ", ".join(missing))
    try:
        counts = cleanup(paths, a.library, profiles, a.dry_run)
    except (ValueError, OSError) as e:
        print("ERR:", e, file=sys.stderr)
        return 2
    print("%s: renamed %d, titled %d, clashes %d, hardlinked so title kept %d" % (
        "dry run, nothing changed" if a.dry_run else "done", counts["renamed"], counts["titled"],
        counts["clashes"], counts["kept"]))
    return 1 if counts["clashes"] else 0


if __name__ == "__main__":
    sys.exit(main())
