#!/usr/bin/python3
"""Converts a movie or a whole season to MP4 (H.264 + AAC stereo) with English
subtitles burnt in. Picks the audio and subtitle tracks itself and never
touches the source files. Runs on Linux, macOS and Windows; needs only
Python 3.8+ and ffmpeg/ffprobe in PATH. See README.md."""

import argparse
import datetime
import json
import os
from os.path import basename, dirname, isdir, join, realpath, relpath, splitext
import re
import shutil
import subprocess
import sys
import tempfile
import time

VIDEO_EXTS = (".avi", ".m4v", ".mkv", ".mp4")
SUBS_EXTS = (".srt",)
# Subtitle codecs libass can draw. Picture subtitles (PGS, VobSub) it cannot
TEXT_SUBS = ("subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text")
# S01E02, s04x03, S01.E02, then 6x01. Only the file name is searched: a folder
# called 1920x1080 is not an episode
EPISODE = [re.compile(r"s(\d{1,2})[ ._-]?[ex](\d{1,3})", re.IGNORECASE),
           re.compile(r"(?<!\d)(\d{1,2})x(\d{2,3})(?!\d)", re.IGNORECASE)]
# The season of a folder: s03 (as arranged), Show.S04.1080p, Season 4, (3 season), 3 сезон
SEASON = [re.compile(r"(?:^|[^a-z])s(\d{1,2})(?![e\d])", re.IGNORECASE),
          re.compile(r"(?:season|сезон)[ ._-]*(\d{1,2})(?!\d)", re.IGNORECASE),
          re.compile(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th|-?й)?[ ._-]*(?:season|сезон)", re.IGNORECASE)]
# en, eng, english as a word, also 2xEng from release names
ENGLISH = re.compile(r"(?:^|[^a-z])(?:\d+x)?(?:en|eng|english)(?:[^a-z]|$)", re.IGNORECASE)
# Worse kinds of subtitles: forced has only the foreign bits, SDH adds [door creaks]
FORCED = re.compile(r"forced", re.IGNORECASE)
SDH = re.compile(r"(?:^|[^a-z])(?:sdh|hi|cc)(?:[^a-z]|$)", re.IGNORECASE)
SAMPLE = re.compile(r"(?:^|[^a-z])sample(?:[^a-z]|$)", re.IGNORECASE)
BAD_CHARS = [' ', '(', ')', '[', ']', '\'', '«', '»', ',', '’']

def parse_resolution(resolution):
    try:
        width, height = map(int, resolution.split('x'))
        return width, height
    except ValueError:
        raise argparse.ArgumentTypeError("Resolution must be in the format "\
                                         "WIDTHxHEIGHT (e.g., 1280x1024)")

def parse_args(argv=None):
    cfg = {
        "audio_enforce": -1,
        "audio_lang": "eng",
        "fixup_names": False,
        "keep_tree": False,
        "local_copy": False,
        "max_height": None,
        "root_dir": os.getcwd(),
        "out_dir": os.getcwd(),
        "overwrite": False,
        "report": None,
        "verbose": False,
        "settings_video": None,
        "video_fit": None,
        "settings_subtitles": None,
        "subtitles_disabled": None,
        "ignore_no_subtitles": False,
        "test_mode": False,
        "a_stream": None,
        "quality": 16,
        "size_target": None
    }

    parser = argparse.ArgumentParser()

    parser.add_argument("-as", "--astream",
                        help="Do not guess audio track, set it explicitly.",
                        type=str)

    parser.add_argument("-al", "--audio_lang",
                        help="Language of the audio track to pick. Default: eng.",
                        type=str)

    parser.add_argument("-d", "--directory",
                        help="Scan given directory or take one video file, instead of current directory.",
                        type=str)

    parser.add_argument("-f", "--fixup_names",
                        help="Remove special characters from output file names. Source files are never renamed.",
                        action="store_true")

    parser.add_argument("-ae", "--audio_enforce",
                        help="If failed to guess - use specified track number.",
                        type=int)

    parser.add_argument("-k", "--keep_tree",
                        help="Keep the source's subfolders in the output: s01/x.mkv -> OUTPUT/s01/x.mp4.",
                        action="store_true")

    parser.add_argument("-lc", "--local_copy",
                        help="Copy each video to the local temp folder first: for sources on a network share.",
                        action="store_true")

    parser.add_argument("-mh", "--max_height",
                        help="Scale a taller video down to this height, keeping its shape: 1080 for a 4K source.",
                        type=int)

    parser.add_argument("-o", "--output",
                        help="Set output directory.",
                        type=str)

    parser.add_argument("-q", "--quality",
                        help="Set video quality. Google H264's crf for details. Default: 16.",
                        type=int)

    parser.add_argument("-r", "--report",
                        help="Write a JSON report here, updated after every video.",
                        type=str)

    parser.add_argument("-s", "--size_target",
                        help="Set target video bitrate in kbs/s.",
                        type=int)

    parser.add_argument("-t", "--test",
                        help="Do a single conversion.",
                        action="store_true")

    parser.add_argument("-sv", "--settings_video",
                        help="Video settings to pass to video filters, for example:",
                        type=str)

    parser.add_argument("-ss", "--settings_subtitles",
                        help="Subtitles settings to pass to libass, for example",
                        type=str)

    parser.add_argument("-sd", "--subtitles_disabled",
                        help="Do not burn in subtitles.",
                        action="store_true")

    parser.add_argument("-ins", "--ignore_no_subtitles",
                        help="Proceed without subtitles if English track not found.",
                        action="store_true")

    parser.add_argument("-v", "--verbose",
                        help="Be verbose (TODO: make it integer).",
                        action="store_true")

    parser.add_argument("-vf", "--video_fit",
                        help="Fit video stream to given resolution by using" \
                        "scale and crop filters",
                        type=parse_resolution)

    parser.add_argument("-y", "--yes",
                        help="Convert again even if the output file exists.",
                        action="store_true")

    args = parser.parse_args(argv)

    cfg["a_stream"] = args.astream
    if args.audio_lang: cfg["audio_lang"] = args.audio_lang.lower()
    if args.directory: cfg["root_dir"] = args.directory
    cfg["fixup_names"] = args.fixup_names
    cfg["keep_tree"] = args.keep_tree
    cfg["local_copy"] = args.local_copy
    if args.max_height: cfg["max_height"] = args.max_height
    if args.audio_enforce is not None: cfg["audio_enforce"] = args.audio_enforce
    if args.output: cfg["out_dir"] = args.output
    cfg["report"] = args.report
    cfg["verbose"] = args.verbose
    if args.settings_video: cfg["settings_video"] = args.settings_video
    if args.settings_subtitles: cfg["settings_subtitles"] = args.settings_subtitles
    cfg["test_mode"] = args.test
    cfg["overwrite"] = args.yes
    cfg["subtitles_disabled"] = args.subtitles_disabled
    cfg["ignore_no_subtitles"] = args.ignore_no_subtitles
    if args.quality is not None: cfg["quality"] = args.quality
    if args.size_target is not None: cfg["size_target"] = args.size_target

    if args.video_fit is not None:
        cfg["video_fit"] = args.video_fit # w, h
        if args.settings_video is not None:
            print("WRN: --settings_video switch should not contain crop or " \
                  "scale filters. This is for subtitles mostly until it is " \
                  "extracted into separate option.")

    return cfg

def episode_of(path):
    """(season, episode) from a file name, or None. Without S01E02 in the name, a name like
    "Show - 301 - Title.mkv" in a folder of season 3 is s03e01: the season from the nearest folder that
    names one, then exactly one number of that season and two digits in the name (not x264, 1080p)."""
    name = basename(path)
    for regex in EPISODE:
        m = regex.search(name)
        if m:
            return int(m.group(1)), int(m.group(2))
    folder = dirname(path)
    while basename(folder):
        season = next((int(m.group(1)) for m in (r.search(basename(folder)) for r in SEASON) if m), None)
        if season is not None:
            eps = set(re.findall(r"(?<![\dxh])%d(\d{2})(?![\dpi])" % season, name, re.IGNORECASE))
            return (season, int(eps.pop())) if len(eps) == 1 and season else None
        folder = dirname(folder)
    return None

def clean_name(name):
    """Special characters to dots, runs of dots to one:
    "Some Show (2001) [x].mkv" -> "Some.Show.2001.x.mkv"."""
    for char in BAD_CHARS:
        name = name.replace(char, '.')
    return re.sub(r'\.\.+', '.', name).lstrip('.')

def filter_path(path):
    """A path as a value inside -vf. ffmpeg unescapes it twice, first as a
    filtergraph, then as an option value, so it is escaped for both."""
    if os.sep == "\\":
        # C:/x and //host/share/x work on Windows and need no backslashes
        path = path.replace("\\", "/")
    for char in "\\':":
        path = path.replace(char, "\\" + char)
    for char in "\\'[],;":
        path = path.replace(char, "\\" + char)
    return path

def scan_videos(cfg):
    """Videos and English .srt files under root_dir. root_dir can be a single
    video: then its subtitles are the .srt files next to it named after it."""
    root = cfg["root_dir"]
    print("INFO: Searching for video files")
    if not isdir(root):
        folder, stem = dirname(root) or ".", splitext(basename(root))[0]
        s_list = [join(folder, f) for f in sorted(os.listdir(folder))
                  if f.lower().endswith(SUBS_EXTS) and f.startswith(stem) and ENGLISH.search(f[len(stem):])]
        return [root], s_list

    out = realpath(cfg["out_dir"])
    v_list = []
    s_list = []
    for path, dirs, files in os.walk(root):
        if cfg["verbose"]: print("DBG: Entering", path)
        # Results may live inside the source (0out next to the episodes): never convert them again
        dirs[:] = sorted(d for d in dirs if realpath(join(path, d)) != out)
        for fn in sorted(files):
            full = join(path, fn)
            rel = relpath(full, root)
            if fn.lower().endswith(VIDEO_EXTS):
                if SAMPLE.search(rel):
                    print("INFO: sample, skipped:", rel)
                    continue
                v_list.append(full)
            elif fn.lower().endswith(SUBS_EXTS):
                # The path inside the torrent counts (Sub.Eng/01.srt), not the torrent's own name
                if ENGLISH.search(rel):
                    s_list.append(full)
                elif cfg["verbose"]:
                    print("DBG: not English subtitles:", rel)

    return v_list, s_list

def subtitles_rank(path):
    """Lower is better: full subtitles over SDH over forced, then the bigger file."""
    name = join(basename(dirname(path)), basename(path))
    kind = 2 if FORCED.search(name) else 1 if SDH.search(name) else 0
    return kind, -os.path.getsize(path)

def best_subtitles(paths):
    return min(paths, key=subtitles_rank) if paths else None

def analyze_episodes(cfg, v_list, s_list):
    """(season, episode, video, external subtitles or None) for every video.
    A movie takes the best English .srt there is. An episode takes the best one
    with its season and episode, otherwise subtitles inside the video are used."""
    if len(v_list) == 1:
        print("INFO: Detected single video. Treat it as a movie")
        return [(None, None, v_list[0], best_subtitles(s_list))]

    print("INFO: Looks like directory contains series.")
    by_episode = {}
    for s in s_list:
        ep = episode_of(s)
        if ep is None:
            print("WRN: no season and episode in the name, ignored:", s)
            continue
        by_episode.setdefault(ep, []).append(s)

    e_list = []
    for v in v_list:
        ep = episode_of(v)
        if ep is None:
            print("WRN: looks like filenames are not in sXXeYY or sXXxYY format. External subtitles match won't work:", v)
            e_list.append((None, None, v, None))
            continue
        subs = best_subtitles(by_episode.get(ep, []))
        if subs:
            print("MATCHED:", v, subs)
        e_list.append((ep[0], ep[1], v, subs))
    return e_list

def probe(cfg, video):
    """The video's streams as ffprobe sees them, or None."""
    cmd = ["ffprobe", "-v", "error", "-of", "json", "-show_entries",
           "stream=index,codec_type,codec_name,width,height:stream_tags=language,title:"
           "stream_disposition=attached_pic,forced,hearing_impaired",
           "-i", video]
    if cfg["verbose"]: print("DBG:", cmd)
    res = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print("ERR: ffprobe failed:", res.stderr.strip()[-500:])
        return None
    return json.loads(res.stdout).get("streams", [])

def tag(stream, name):
    return (stream.get("tags") or {}).get(name, "")

def flag(stream, name):
    return (stream.get("disposition") or {}).get(name, 0) == 1

def is_lang(stream, lang):
    """eng matches eng and en."""
    return tag(stream, "language").lower() in (lang, lang[:2])

def pick_video(streams):
    """The first video stream that is not a cover picture."""
    for s in streams:
        if s.get("codec_type") == "video" and not flag(s, "attached_pic"):
            return s
    return None

def pick_audio(cfg, streams):
    """("0:N", why) or (None, why)."""
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    lang = cfg["audio_lang"]
    for s in audio:
        if is_lang(s, lang):
            return "0:%d" % s["index"], "%s by language tag" % lang
    print("WRN: cannot find %s audio track by language tag." % lang)
    if cfg["audio_enforce"] != -1:
        print("INF: enforcing track", cfg["audio_enforce"])
        return "0:%d" % cfg["audio_enforce"], "enforced"
    # Without tags (.avi) English is usually the second track, Russian the first
    nth = 1 if lang == "eng" else 0
    if len(audio) <= nth:
        return None, "no %s audio track: %d audio tracks, none tagged %s" % (lang, len(audio), lang)
    print("WRN: falling back to audio track %d of %d" % (nth + 1, len(audio)))
    return "0:%d" % audio[nth]["index"], "track %d of %d, untagged" % (nth + 1, len(audio))

def pick_subtitles(streams):
    """(si, why) for English text subtitles inside the video, or (None, why).
    si counts subtitle streams only, as the subtitles filter wants it."""
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    english = [(i, s) for i, s in enumerate(subs)
               if re.search("eng", tag(s, "language"), re.IGNORECASE) or re.search("eng", tag(s, "title"), re.IGNORECASE)]
    if not english:
        return None, "no English subtitles in the video or next to it"
    text = [(i, s) for i, s in english if s.get("codec_name") in TEXT_SUBS]
    if not text:
        return None, "English subtitles in the video are pictures (%s), they cannot be burnt in" % english[0][1].get("codec_name")

    def rank(pair):
        s = pair[1]
        kind = (2 if flag(s, "forced") or FORCED.search(tag(s, "title"))
                else 1 if flag(s, "hearing_impaired") or SDH.search(tag(s, "title")) else 0)
        return kind, pair[0]

    i, s = min(text, key=rank)
    return i, "inside, subtitle track %d %s" % (i + 1, tag(s, "title") or tag(s, "language"))

def video_filters(cfg, width, height):
    """Crop and scale for --video_fit, or --max_height and --settings_video."""
    if cfg["video_fit"] is None:
        out = []
        if cfg["max_height"] and height > cfg["max_height"]:
            out.append("scale=w=-2:h=%d" % cfg["max_height"])  # -2: the width that keeps the shape, even
        return out + ([cfg["settings_video"]] if cfg["settings_video"] else [])
    fit_w, fit_h = cfg["video_fit"]
    if width <= height or fit_w <= fit_h:
        raise ValueError("video_fit works only from landscape to landscape: %dx%d to %dx%d"
                         % (width, height, fit_w, fit_h))
    d = fit_w / fit_h
    # Crop
    crop_h = width / d
    crop_h = int(round(crop_h) + (round(crop_h) % 2)) # Even int for codec
    shift_y = (height - crop_h) / 2
    crop = "crop=w="+str(width)+":h="+str(crop_h)+":x=0"+":y="+str(shift_y)
    # Scale
    scale = "scale=w="+str(fit_w)+":h="+str(fit_h)
    print("INFO: video track conversion command is", crop + "," + scale)
    return [crop, scale]

def full_speed(proc):
    """Windows 11 throttles a process that has no window (EcoQoS) and keeps it on the efficiency
    cores of a hybrid CPU: an i9 then converts on its E-cores with the P-cores idle. Opt out."""
    if os.name != "nt":
        return
    import ctypes

    class PowerThrottlingState(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

    process_power_throttling, execution_speed = 4, 1
    state = PowerThrottlingState(1, execution_speed, 0)  # control execution speed, throttling off
    # Popen keeps the process handle in _handle on Windows
    ctypes.windll.kernel32.SetProcessInformation(ctypes.c_void_p(int(proc._handle)), process_power_throttling,
                                                 ctypes.byref(state), ctypes.sizeof(state))

def local_copy(path, tmp):
    """A copy of path in tmp, or path itself when the disk is short of space. Over a network share
    one copy at full speed beats ffmpeg's reads: the subtitles filter reads the whole file before
    each pass starts, then the pass reads it again, four times in all for two passes."""
    size = os.path.getsize(path)
    free = shutil.disk_usage(tmp).free
    if free < size + (2 << 30):
        print("WRN: %.1f GB free in %s, %.1f GB needed: reading over the network" % (free / 2**30, tmp, size / 2**30))
        return path
    dst = join(tmp, "source" + splitext(path)[1])
    while os.path.exists(dst):
        dst = join(tmp, "source-%d%s" % (len(os.listdir(tmp)), splitext(path)[1]))
    started = time.time()
    with open(path, "rb") as fi, open(dst, "wb") as fo:
        shutil.copyfileobj(fi, fo, 8 << 20)
    took = max(time.time() - started, 0.001)
    print("INFO: copied %s to the local disk, %.1f GB in %d s, %.0f MB/s"
          % (basename(path), size / 2**30, took, size / 2**20 / took))
    return dst

def run_ffmpeg(cmd):
    """None on success, otherwise the end of ffmpeg's error output.
    In a terminal ffmpeg shows its progress; under a worker its output is kept
    and printed only if it fails."""
    print(cmd)
    if sys.stdout.isatty():
        proc = subprocess.Popen(cmd)
        full_speed(proc)
        return None if proc.wait() == 0 else "see the output above"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
    full_speed(proc)
    _, err = proc.communicate()
    if proc.returncode == 0:
        return None
    tail = err.strip().splitlines()[-20:]
    print("\n".join(tail))
    return tail[-1] if tail else "exit code %d" % proc.returncode

def convert_one(cfg, e):
    """Converts one video. Returns its report entry: status converted,
    exists (done before), skipped (no subtitles or audio) or failed."""
    video, ext_subs = e[2], e[3]
    name = splitext(basename(video))[0] + ".mp4"
    if cfg["fixup_names"]:
        name = clean_name(name)
    root = cfg["root_dir"] if isdir(cfg["root_dir"]) else dirname(cfg["root_dir"]) or "."
    if cfg["keep_tree"]:
        # The season folders of a show come along: series/Show/s01/x.mkv -> OUTPUT/s01/x.mp4
        name = join(relpath(dirname(video), root), name)
    name = os.path.normpath(name)
    out = join(cfg["out_dir"], name)
    item = {"video": relpath(video, root), "output": name, "status": "", "reason": "",
            "audio": "", "subtitles": "", "seconds": 0}

    if os.path.exists(out) and not cfg["overwrite"]:
        print("INFO: already converted, skipped:", out)
        return dict(item, status="exists")

    streams = probe(cfg, video)
    if streams is None:
        return dict(item, status="failed", reason="ffprobe cannot read it")
    v = pick_video(streams)
    if v is None:
        print("ERR: cannot find video stream.")
        return dict(item, status="failed", reason="no video stream")
    print("INFO: OK: found video track 0:%d Resolution %sx%s" % (v["index"], v.get("width"), v.get("height")))

    burn, si = False, None
    if not cfg["subtitles_disabled"]:
        if ext_subs:
            burn = True
            item["subtitles"] = "external " + relpath(ext_subs, root)
        else:
            si, why = pick_subtitles(streams)
            if si is not None:
                burn = True
                item["subtitles"] = why
            elif cfg["ignore_no_subtitles"]:
                print("WRN: %s, proceeding without subtitles." % why)
                item["subtitles"] = "none: " + why
            else:
                print("ERR:", why)
                return dict(item, status="skipped", reason=why)
        if burn:
            print("INFO: OK: subtitles", item["subtitles"])

    if cfg["a_stream"]:
        astream, item["audio"] = cfg["a_stream"], "set explicitly"
    else:
        astream, item["audio"] = pick_audio(cfg, streams)
    if not astream:
        print("ERR: no audio stream defined or guessed:", item["audio"])
        return dict(item, status="skipped", reason=item["audio"])
    print("INFO: OK: found audio track inside video file", astream)

    try:
        vf = video_filters(cfg, v.get("width") or 0, v.get("height") or 0)
    except ValueError as err:
        print("ERR:", err)
        return dict(item, status="failed", reason=str(err))

    # Written under another name and renamed when complete: an interrupted run
    # leaves no half file that a later run would take for done
    part = out + ".part"
    os.makedirs(dirname(out), exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix="ffmpegger-") as tmp:
        src, subs_src = video, ext_subs
        if cfg["local_copy"]:
            src = local_copy(video, tmp)
            if ext_subs and src != video:
                subs_src = local_copy(ext_subs, tmp)
        if burn:
            subs = ("subtitles=filename=" + filter_path(subs_src) if ext_subs
                    else "subtitles=filename=%s:si=%d" % (filter_path(src), si))
            vf.append(subs + (":" + cfg["settings_subtitles"] if cfg["settings_subtitles"] else ""))
        if cfg["verbose"]: print("DBG: video filter setting:", vf)

        common = ["ffmpeg", "-hide_banner", "-nostdin", "-y",
                  "-i", src,
                  # Video setting
                  "-map", "0:%d" % v["index"], "-c:v", "libx264",
                  # 8 bits: from a 10-bit source x264 makes High 10, which phones,
                  # browsers and cars do not play
                  "-pix_fmt", "yuv420p",
                  # Audio setting
                  "-map", astream, "-c:a", "aac",
                  # TODO: extract into param. These key turns sound into stereo
                  # from, say 5.1. Nothing but mono/stereo can be played on iPhone
                  "-ac", "2"]
        if vf:
            common += ["-vf", ",".join(vf)]

        if cfg["size_target"]:
            print("Will do two pass converion as bitrate was specified")
            rate = ["-b:v", str(cfg["size_target"]) + "k"]
            passlog = join(tmp, "pass")
            steps = [("pass 1 ", common + rate + ["-pass", "1", "-passlogfile", passlog, "-an",
                                                   "-f", "null", os.devnull]),
                     ("pass 2 ", common + rate + ["-pass", "2", "-passlogfile", passlog, "-f", "mp4", part])]
        else:
            steps = [("", common + ["-preset", "slower", "-crf", str(cfg["quality"]), "-f", "mp4", part])]
        for label, cmd in steps:
            error = run_ffmpeg(cmd)
            if error:
                print("ERR: FFmpeg %sfailed" % label)
                if os.path.exists(part):
                    os.remove(part)
                return dict(item, status="failed", reason="ffmpeg %sfailed: %s" % (label, error),
                            seconds=round(time.time() - started))
            print("OK: FFmpeg %sfinished" % label)
    os.replace(part, out)
    return dict(item, status="converted", seconds=round(time.time() - started))

def write_report(cfg, report):
    if not cfg["report"]:
        return
    tmp = cfg["report"] + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.replace(tmp, cfg["report"])

def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")

def main(argv=None):
    """0: everything converted or there already. 1: something was skipped or
    failed, see the report. 2: no videos found."""
    try:
        # Into a file Windows Python writes cp1252 and dies on the first Cyrillic name
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except AttributeError:
        pass
    cfg = parse_args(argv)
    report = {"root": cfg["root_dir"], "out_dir": cfg["out_dir"], "started": now(), "finished": None,
              "status": "running", "total": 0, "counts": {}, "videos": []}

    v_list, s_list = scan_videos(cfg)

    if cfg["verbose"]:
        for i in v_list: print(i)
        for i in s_list: print(i)

    if len(v_list) == 0:
        print("No videos found. Exiting")
        write_report(cfg, dict(report, finished=now(), status="no videos"))
        return 2

    if len(s_list) == 0:
        print("INFO: No external English subtitles detected.")
        print("INFO: Will still try to extract it from main video file.")
    else:
        print("INFO: subtitles list length: ", len(s_list))
        print("INFO: videos list length: ", len(v_list))

    e_list = analyze_episodes(cfg, v_list, s_list)
    if cfg["test_mode"]:
        print("INFO: Test mode, converting single video")
        e_list = e_list[:1]

    print("INFO: List of found videos:")
    for e in e_list: print(e)

    os.makedirs(cfg["out_dir"], exist_ok=True)
    report["total"] = len(e_list)
    for n, e in enumerate(e_list, 1):
        print("INFO: video %d of %d: %s" % (n, len(e_list), e[2]))
        report["videos"].append(convert_one(cfg, e))
        write_report(cfg, report)

    counts = {k: sum(1 for v in report["videos"] if v["status"] == k)
              for k in ("converted", "exists", "skipped", "failed")}
    ok = counts["skipped"] == 0 and counts["failed"] == 0
    write_report(cfg, dict(report, finished=now(), status="done" if ok else "incomplete", counts=counts))
    print("INFO: converted %(converted)d, there already %(exists)d, skipped %(skipped)d, failed %(failed)d" % counts)
    return 0 if ok else 1

if __name__ == '__main__':
    sys.exit(main())
