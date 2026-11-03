#!/usr/bin/python3
"""Arranges a finished download into the library with hardlinks, so the torrent
keeps seeding from where it is and the library takes no extra space:

    arrange.py --library /mnt/lib --show "Better Call Saul" /mnt/lib/0unsort/BCS.S01-S02.1080p
        -> series/Better Call Saul/s01/..., series/Better Call Saul/s02/...
    arrange.py --library /mnt/lib --film /mnt/lib/0unsort/Rio.2011.1080p.BluRay.mkv
        -> films/Rio (2011)/Rio.2011.mkv

--series films/0series puts the shows there instead of series/.
Episodes go to their season by SxxEyy in the file name, whatever the torrent's
layout, or by a name like "Show - 301 - Title" in a folder that names season 3.
Lectures and the like, numbered without a season (Лекция #10, Lecture 10,
1-Title, 01. Title), go to the season their folder names, else each folder of
them is a season of its own: the shallowest first, then by name. English and
Russian .srt files follow their video as <video>.en.srt, <video>.ru.srt (.sdh,
.forced kept), so the language survives leaving its Subs/English/ folder; audio
files (a dub in .mka, .ac3 ...) as <video>.ru.mka. Files already there are skipped; files without an episode
number are reported, not guessed. Names lose their release junk (convert_eng.tidy_name), lectures
get leading zeros when a season has ten and more. A record of what went where is written to
.convert/arranged/<id>.json for the bot. See README.md."""

import argparse
import os
import posixpath
import re
import sys

from ffmpegger import jobs
from ffmpegger.convert_eng import (AUDIO_EXTS, ENGLISH, FORCED, SAMPLE, SDH, SUBS_EXTS, VIDEO_EXTS, episode_of,
                                   folder_season, number_of, number_width, season_episode, tidy_file)

RUSSIAN = re.compile(r"(?:^|[^a-z])(?:ru|rus|russian)(?:[^a-z]|$)", re.IGNORECASE)
YEAR = re.compile(r"[ ._(\[-]((?:19|20)\d{2})(?=[ ._)\]-]|$)")
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not in a name on Windows, which reads the share


def safe(name):
    """A folder name that works on the share from Windows too."""
    return ILLEGAL.sub(" ", name).strip(" .")


def film_title(name):
    """Rio.2011.1080p.BluRay.x264.mkv -> Rio (2011); without a year, the name without its extension.
    The last year counts: Blade Runner 2049 (2017), 2001.A.Space.Odyssey.1968."""
    base = os.path.splitext(name)[0] if name.lower().endswith(VIDEO_EXTS) else name
    years = [m for m in YEAR.finditer(base) if m.start() > 0]
    if years:
        title = re.sub(r"[._]+", " ", base[:years[-1].start()]).strip(" -([")
        return "%s (%s)" % (title, years[-1].group(1))
    return re.sub(r"[._]+", " ", base).strip()


def files_of(source):
    """(videos, subtitles, audio files) under source as (path, path inside the torrent); a file is its
    own torrent."""
    if not os.path.isdir(source):
        return [(source, os.path.basename(source))], [], []
    videos, subs, audio = [], [], []
    for path, dirs, files in os.walk(source):
        dirs.sort()
        for fn in sorted(files):
            full = os.path.join(path, fn)
            rel = os.path.relpath(full, source)
            if fn.lower().endswith(VIDEO_EXTS) and not SAMPLE.search(rel):
                videos.append((full, rel))
            elif fn.lower().endswith(SUBS_EXTS):
                subs.append((full, rel))
            elif fn.lower().endswith(AUDIO_EXTS):
                audio.append((full, rel))
    return videos, subs, audio


def language_of(rel):
    """en or ru from the path inside the torrent ([Нейронка RUS]/…, Sub.Eng/…), or None."""
    return "en" if ENGLISH.search(rel) else "ru" if RUSSIAN.search(rel) else None


def subtitle_suffix(rel):
    """.en, .en.sdh, .ru.forced ... from the path inside the torrent, or None for other languages."""
    lang = language_of(rel)
    if not lang:
        return None
    name = os.path.join(os.path.basename(os.path.dirname(rel)), os.path.basename(rel))
    return "." + lang + (".forced" if FORCED.search(name) else ".sdh" if SDH.search(name) else "")


def link(src, dst, record):
    """Hardlinks src as dst; notes what happened."""
    rel = record["_rel"](dst)
    if os.path.lexists(dst):
        if os.path.samefile(src, dst):
            record["there"].append(rel)
        else:
            record["clash"].append(rel)
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.link(src, dst)
    record["placed"].append(rel)
    return True


def arrange(library, source, show=None, film=False, title=None, series="series"):
    """Links the download into <series>/<show>/sNN/ or films/<title>/; returns the record."""
    library = os.path.realpath(library)
    source = os.path.realpath(source)
    src_rel = os.path.relpath(source, library).replace(os.sep, "/")
    if src_rel in (".", "..") or src_rel.startswith("../"):
        raise ValueError("%s is outside the library %s" % (source, library))
    if bool(show) == bool(film):
        raise ValueError("arrange either a show or a film")
    videos, subs, audio = files_of(source)
    record = {"id": jobs.new_id(src_rel + "|" + (show or "film")), "time": jobs.now(), "source": src_rel,
              "placed": [], "there": [], "clash": [], "unplaced": [], "folders": [],
              "_rel": lambda p: os.path.relpath(p, library).replace(os.sep, "/")}

    def add_subs(video_dst, matching):
        """The subtitles and audio files of a video next to it: <video>.en.srt, <video>.ru.mka."""
        stem = os.path.splitext(video_dst)[0]
        used = set()
        for full, rel in matching:
            ext = os.path.splitext(rel)[1].lower()
            suffix = subtitle_suffix(rel) if ext in SUBS_EXTS else "." + (language_of(rel) or "")
            if suffix in (None, "."):
                continue
            n, name = 1, stem + suffix + ext
            while name in used or (os.path.lexists(name) and not os.path.samefile(full, name)):
                n += 1
                name = "%s%s.%d%s" % (stem, suffix, n, ext)
            used.add(name)
            link(full, name, record)

    episodes_in = {}  # season folder -> {(season, episode): file name} of the videos already there

    def holder(folder, ep):
        if folder not in episodes_in:
            names = os.listdir(folder) if os.path.isdir(folder) else []
            episodes_in[folder] = {episode_of(os.path.join(folder, n)): n for n in names
                                   if n.lower().endswith(VIDEO_EXTS)}
        return episodes_in[folder].get(ep)

    if show:
        record["show"] = show = safe(show)
        record["where"] = posixpath.join(series.strip("/"), show)
        # The torrent's own folder may be what names the season: Show (3 season)/Show - 301 - Title.mkv
        top = os.path.basename(source) if os.path.isdir(source) else ""
        # Numbered without a season (Лекция #10, 1-Title): the season its folder names, else each folder
        # of them a season of its own, after the seasons named in the torrent
        named = {season_episode(os.path.join(top, rel)) for _, rel in videos} - {None}
        folders = sorted({os.path.dirname(rel) for _, rel in videos
                          if not season_episode(os.path.join(top, rel)) and number_of(rel) is not None},
                         key=lambda d: (d.count(os.sep) if d else -1, d))
        seasons, used, n = {}, {ep[0] for ep in named}, 0
        for d in folders:
            season = folder_season(os.path.join(top, d, "x"))
            if season is None:
                n += 1
                while n in used:
                    n += 1
                season = n
            seasons[d] = season
            used.add(season)

        def episode(rel):
            """The episode of a video, subtitle or audio file: named, else its number in the season of
            the nearest folder of numbered videos it is in."""
            ep = season_episode(os.path.join(top, rel))
            if ep or number_of(rel) is None:
                return ep
            d = os.path.dirname(rel)
            while d not in seasons and d:
                d = os.path.dirname(d)
            return (seasons[d], number_of(rel)) if d in seasons else None

        # Names without release junk, the episodes of a season numbered without one padded alike
        stems = {}
        for full, rel in videos:
            if episode(rel):
                stems.setdefault(episode(rel)[0], []).append(os.path.splitext(os.path.basename(full))[0])
        widths = {season: number_width(names) for season, names in stems.items()}

        by_episode = {}
        for full, rel in subs + audio:
            # A subtitle's folder may name its episode: Subs/S01E02/English.srt
            ep = episode(rel) or season_episode(os.path.join(top, os.path.dirname(rel)))
            if ep:
                by_episode.setdefault(ep, []).append((full, rel))
        for full, rel in videos:
            ep = episode(rel)
            if not ep:
                record["unplaced"].append(rel)
                continue
            folder = os.path.join(library, series, show, "s%02d" % ep[0])
            dst = os.path.join(folder, tidy_file(os.path.basename(full), widths.get(ep[0])))
            other = holder(folder, ep)
            if other and not os.path.samefile(full, os.path.join(folder, other)):
                # Another release of the same episode is there already: keep it, say so
                record["clash"].append("%s (s%02d has %s)" % (rel, ep[0], other))
                continue
            if other:
                # There already, maybe under the name it had before names were cleaned
                dst = os.path.join(folder, other)
            link(full, dst, record)
            episodes_in[folder][ep] = os.path.basename(dst)
            add_subs(dst, by_episode.get(ep, []))
            if record["_rel"](folder) not in record["folders"]:
                record["folders"].append(record["_rel"](folder))
    else:
        record["film"] = title = safe(title or film_title(os.path.basename(source)))
        record["where"] = "films/" + title
        folder = os.path.join(library, "films", title)
        for full, rel in videos:
            dst = os.path.join(folder, tidy_file(os.path.basename(full)))
            link(full, dst, record)
            if len(videos) == 1:
                add_subs(dst, subs + audio)
        if videos:
            record["folders"].append(record["_rel"](folder))
    record["folders"].sort()
    del record["_rel"]
    return record


def save(library, record):
    os.makedirs(jobs.folder(library, "arranged"), exist_ok=True)
    jobs.write(jobs.file(library, "arranged", record["id"]), record)


def main(argv=None):
    p = argparse.ArgumentParser(description="Arrange a download into the library with hardlinks.")
    p.add_argument("--library", required=True, help="library root on this server")
    kind = p.add_mutually_exclusive_group(required=True)
    kind.add_argument("--show", help="the show's name: its folder in --series")
    kind.add_argument("--film", action="store_true", help="a film: films/<title>/")
    p.add_argument("--title", help="the film's folder, default from the download's name: Rio (2011)")
    p.add_argument("--series", default="series", help="the shows' folder in the library, default series")
    p.add_argument("source", help="the finished torrent, a folder or a file")
    a = p.parse_args(argv)
    try:
        record = arrange(a.library, a.source, show=a.show, film=a.film, title=a.title, series=a.series)
    except (ValueError, OSError) as e:
        print("ERR:", e, file=sys.stderr)
        return 2
    save(a.library, record)
    for key in ("placed", "there", "clash", "unplaced"):
        for f in record[key]:
            print("%-8s %s" % (key, f))
    print("folders:", ", ".join(record["folders"]) or "none")
    return 0 if record["folders"] else 1


if __name__ == "__main__":
    sys.exit(main())
