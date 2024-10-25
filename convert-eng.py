#!/usr/bin/python3

# TODO: check if jq exists?

import argparse
import os
from os.path import basename, getsize, join, splitext
import re
import subprocess

def parse_resolution(resolution):
    try:
        width, height = map(int, resolution.split('x'))
        return width, height
    except ValueError:
        raise argparse.ArgumentTypeError("Resolution must be in the format "\
                                         "WIDTHxHEIGHT (e.g., 1280x1024)")

def parse_args():
    cfg = {
        "audio_enforce": 0,
        "fixup_names": False,
        "root_dir": os.getcwd(),
        "out_dir": os.getcwd(),
        "overwrite": False,
        "verbose": False,
        "settings_video": None,
        "video_fit": None,
        "settings_subtitles": None,
        "test_mode": False,
        "a_stream": None,
        "quality": 16,
        "size_target": None
    }

    parser = argparse.ArgumentParser()

    parser.add_argument("-as", "--astream",
                        help="Do not guess audio track, set it explicitly.",
                        type=str)

    parser.add_argument("-d", "--directory",
                        help="Scan given directory, instead of current.",
                        type=str)

    parser.add_argument("-f", "--fixup_names",
                        help="Remove special characters from filenames.",
                        action="store_true")

    parser.add_argument("-ae", "--audio_enforce",
                        help="If failed to guess - use specified track number.",
                        type=int)

    parser.add_argument("-o", "--output",
                        help="Set output directory.",
                        type=str)

    parser.add_argument("-q", "--quality",
                        help="Set video quality. Google H264's crf for details. Default: 16.",
                        type=int)

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

    parser.add_argument("-v", "--verbose",
                        help="Be verbose (TODO: make it integer).",
                        action="store_true")

    parser.add_argument("-vf", "--video_fit",
                        help="Fit video stream to given resolution by using" \
                        "scale and crop filters",
                        type=parse_resolution)

    parser.add_argument("-y", "--yes",
                        help="Overwrite output file if exists.",
                        action="store_true")

    args = parser.parse_args()

    cfg["a_stream"] = args.astream
    if args.directory: cfg["root_dir"] = args.directory
    cfg["fixup_names"] = args.fixup_names
    cfg["audio_enforce"] = args.audio_enforce
    if args.output: cfg["out_dir"] = args.output
    cfg["verbose"] = args.verbose
    if args.settings_video: cfg["settings_video"] = args.settings_video
    if args.settings_subtitles: cfg["settings_subtitles"] = args.settings_subtitles
    cfg["test_mode"] = args.test
    cfg["overwrite"] = args.yes
    if args.quality is not None: cfg["quality"] = args.quality
    if args.size_target is not None: cfg["size_target"] = args.size_target

    if args.video_fit is not None:
        cfg["video_fit"] = args.video_fit # w, h
        if args.settings_video is not None:
            print("WRN: --settings_video switch should not contain crop or " \
                  "scale filters. This is for subtitles mostly until it is " \
                  "extracted into separate option.")

    return cfg

def scan_videos(cfg):
    video_exts = (".avi", ".m4v", ".mkv", ".mp4")
    subs_exts = (".srt")

    print("INFO: Searching for video files")
    v_list = []
    s_list = []
    for root, dirs, files in os.walk(cfg["root_dir"]):
        if cfg["verbose"]: print("DBG: Entering", root)

        for fn in files:
            if cfg["verbose"]: print("DBG:  Checking:", fn, end=" ")
            if fn.endswith(video_exts):
                if cfg["verbose"]: print("VIDEO!")
                v_list.append(join(root, fn))

            if fn.endswith(subs_exts):
                if cfg["verbose"]: print("DBG: SUBS!", end=" ")
                if "eng".casefold() in join(root, fn).casefold():
                    if cfg["verbose"]: print("ENG!!!")
                    s_list.append(join(root, fn))
                else:
                    if cfg["verbose"]: print("not eng :(")

    return v_list, s_list

def analyze_episodes(cfg, v_list, s_list):
    e_list = []
    regex = re.compile(r"s(\d+)e(\d+)", flags=re.IGNORECASE)
    for v in v_list:
        if cfg["verbose"] : print("Analyzing", v)
        v_pair = regex.findall(v)
        season = None
        episode = None
        if not v_pair:
            print("WRN: looks like filenames are not in sXXeYY format. External subtitles match won't work")
        else:
            if len(v_pair[0]) != 2:
                print("ERR: failed to extract Season/Episode from name. Exiting", v)
                return
            else:
                season = v_pair[0][0]
                episode = v_pair[0][1]

        # Now let's try to match corresponding external subtitles file
        subs_file = None
        if s_list and v_pair:
            matched = False
            for s in s_list:
                s_pair = regex.findall(s)
                if len(s_pair[0]) != 2:
                    print("ERR: failed to extract Season/Episode from name. Exiting", s)
                    return
                if v_pair == s_pair:
                    print("MATCHED:", v, s)
                    matched = True
                    subs_file = s
                    break

            if not matched:
                print("ERR: external subtitles exist, but failed to match any with video", v)
                return

        e_list.append((season, episode, v, subs_file))
        print(regex.findall(v))

    return e_list

# Arg cmd is a _list_ of options to pass to ffprobe
def exec_ffprobe_json(cfg, video, cmd):
    ffprobe_cmd=["ffprobe",
                 "-i", video,
                 "-of", "json",
                 "-v", "panic",
                 "-show_entries"]
    ffprobe_cmd += cmd
    if cfg["verbose"]: print(ffprobe_cmd)
    res = subprocess.run(ffprobe_cmd, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during ffprobe execution. Unable find video track")
        return

    return res

# Arg cmd is a string with filter.
def jq_extract_value(cfg, input, cmd):
    jq_filter_cmd=["jq", cmd, "-"]
    if cfg["verbose"]: print("DBG:", jq_filter_cmd)
    res = subprocess.run(jq_filter_cmd, input=input, capture_output=True)
    if cfg["verbose"]: print("DBG:", res)
    if res.returncode != 0:
        print("ERR: error during execution of jq. Command was",
              jq_filter_cmd)
        return
    res = res.stdout.decode("utf-8").rstrip()
    return res

# Search a video file for English subtitles.
# Return result as a correct argument for ffmpeg convertor
def scan_eng_subtitles(cfg, video):
    cmd = ["stream=index,codec_type:stream_tags=language",
           "-select_streams", "s"]
    res = exec_ffprobe_json(cfg, video, cmd)

    res = jq_extract_value(cfg, res.stdout,
                           "[.streams[].tags.language==\"eng\"]|index(true)")

    # Strip newline from result
    res = video + ":si=" + res
    print("INF: OK: found subtitles track inside video file", res)
    return res

# Search a video file for English audio track.
# Usually, normal English audio track appears first.
# All other "commentary" and alike are later.
# Return result as a correct argument for ffmpeg convertor
def scan_eng_astream(cfg, video):
    cmd = ["stream=index,codec_type:stream_tags=language",
           "-select_streams", "a"]
    res = exec_ffprobe_json(cfg, video, cmd)

    res = jq_extract_value(cfg, res.stdout,
                           "[.streams[] | select(.tags.language==\"eng\").index][0]")

    if res == "null":
        print("ERR: cannot find English audio track.")
        if cfg["audio_enforce"] == 0:
            return
        res = str(cfg["audio_enforce"])
        print("INF: enforcing track", res)


    res = "0:" + res
    print("INFO: OK: found audio track inside video file", res)
    return res

def scan_vstream(cfg, video):
    # 1. Search for video stream
    cmd = ["stream=index", "-select_streams", "v"]
    res = exec_ffprobe_json(cfg, video, cmd)

    track = jq_extract_value(cfg, res.stdout,
                             "[.streams[].index][0]")
    if track == "null":
        print("ERR: cannot find video stream.")
        return

    # 2. Detect original resolution
    # NB: do not specify stream type as it will make indexing local.
    # E.g. if video stream indices were 0 and 7 passing prefix "v:7"
    # will led to error, as for "v:" only 0 and 1 will be valid.
    cmd = ["stream=width,height", "-select_streams", track]
    res = exec_ffprobe_json(cfg, video, cmd)

    w = jq_extract_value(cfg, res.stdout,
                         "[.streams[].width][0]")
    h = jq_extract_value(cfg, res.stdout,
                         "[.streams[].height][0]")

    res = "0:" + track
    print("INFO: OK: found video track", res, "Resolution", w+"x"+h)
    return res, int(w), int(h)

# Fixup filenames in a root dir. The function uses config's root dir
# and walks (top down) through directories and file recursively.
# During the walk, it renames paths replacing strange characters
# with dots. It then joins series of dots into single dot make
# name loo more nicely.
#
# Strange symbols are stored in `bad_chars` array.
def fixup_names(cfg):
    root = cfg["root_dir"]

    bad_chars = [' ', '(', ')', '[', ']', '\'', '«', '»', ',', '’']
    os.chdir(root)
    for path, subdir, files in os.walk(root, topdown=False):
        for f in subdir:
            copy_f = f
            for char in copy_f:
                if (char in bad_chars): copy_f = copy_f.replace(char, '.')

            # Replace runs of two and more dots with single dot.
            copy_f = re.sub(r'\.\.+', '.', copy_f)

            os.rename(os.path.join(path, f),
                      os.path.join(path, copy_f))
            print("INFO: Renamed", f, "into", copy_f)
        for f in files:
            copy_f = f
            for char in copy_f:
                if (char in bad_chars): copy_f = copy_f.replace(char, '.')

            # Replace runs of two and more dots with single dot.
            copy_f = re.sub(r'\.\.+', '.', copy_f)

            os.rename(os.path.join(path, f),
                      os.path.join(path, copy_f))
            print("INFO: Renamed ", f, "into ", copy_f)
    return

def convert_one(cfg, e):
    video = e[2]
    astream = cfg["a_stream"]
    out = splitext(join(cfg["out_dir"], basename(video)))[0] + ".mp4"

    if not e[3]:
        subs = scan_eng_subtitles(cfg, video)
    else:
        subs = e[3]

    if not astream:
        astream = scan_eng_astream(cfg, video)

    if not astream:
        print("ERR: no audio stream defined or guessed.")
        return False

    v_stream, orig_w, orig_h = scan_vstream(cfg, video)

    vs = ""
    if cfg["video_fit"] is not None:
        assert(cfg["settings_video"] is None)
        assert(orig_w > orig_h)
        fit_w = cfg["video_fit"][0]
        fit_h = cfg["video_fit"][1]
        assert(fit_w > fit_h)

        d = fit_w / fit_h
        # Crop
        crop_h = orig_w / d
        crop_h = int(round(crop_h) + (round(crop_h) % 2)) # Even int for codec
        shift_y = (orig_h - crop_h) / 2
        crop = "crop=w="+str(orig_w)+":h="+str(crop_h)+":x=0"+":y="+str(shift_y)
        # Scale
        scale = "scale=w="+str(fit_w)+":h="+str(fit_h)
        vs = crop+","+scale+","
        print("INFO: video track conversion command is", vs)
    else:
        if cfg["settings_video"]:
            vs = cfg["settings_video"]+","

    vs += "subtitles=" + subs
    if cfg["settings_subtitles"]:
        vs += ":" + cfg["settings_subtitles"]

    if cfg["verbose"]: print("DBG: video filter setting:", vs)

    ffmpeg_cmd_common_opts = [
        "ffmpeg",
        "-i", video,
        # Video setting
        "-c:v", "libx264",
        "-map", v_stream,

        # Audio setting
        "-acodec", "aac",
        "-map", astream,
        # TODO: extract into param. These key turns sound into stereo
        # from, say 5.1. Nothing but mono/stereo can be played on iPhone
        "-ac", "2",

        # Video and subtitles
        "-vf", vs,

        "-f", "mp4", # Force format, as /dev/null has no ext.
        "-y",        # Allow "re-write" /dev/null w/o questions.
    ]

    if cfg["overwrite"]:
        ffmpeg_cmd_common_opts.append("-y")

    if cfg["size_target"]:
        print("Will do two pass converion as bitrate was specified")
        ffmpeg_cmd_1 = ffmpeg_cmd_common_opts + [
            # Video bitrate
            "-b:v", str(cfg["size_target"])+"k",
            "-pass", "1",
            "/dev/null"]

        ffmpeg_cmd_2 = ffmpeg_cmd_common_opts + [
            # Video bitrate
            "-b:v", str(cfg["size_target"])+"k",
            "-pass", "2",
            out
        ]
        print(ffmpeg_cmd_1)
        res = subprocess.run(ffmpeg_cmd_1, capture_output=False)
        print(res)
        if res.returncode == 0:
            print ("OK: FFmpeg pass 1 finished")
        else:
            print ("ERR: FFmpeg pass 1 failed")
            return False

        print(ffmpeg_cmd_2)
        res = subprocess.run(ffmpeg_cmd_2, capture_output=False)
        print(res)
        if res.returncode == 0:
            print ("OK: FFmpeg pass 2 finished")
            return True
        else:
            print ("ERR: FFmpeg pass 2 failed")
            return False
    else:
        ffmpeg_cmd = ffmpeg_cmd_common_opts + [
            "-preset", "slower",
            "-crf", str(cfg["quality"]),
            out
        ]
        print(ffmpeg_cmd)
        if subprocess.run(ffmpeg_cmd).returncode == 0:
            print ("OK: FFmpeg finished")
            return True
        else:
            print ("ERR: FFmpeg failed")
            return False

def main():
    cfg = parse_args()

    if cfg["fixup_names"]:
        print("INFO: will try to fixup filenames if needed")
        fixup_names(cfg)

    v_list, s_list = scan_videos(cfg)

    if cfg["verbose"]:
        for i in v_list: print(i)
        for i in s_list: print(i)

    if len(v_list) == 0:
        print("No videos found. Exiting")
        return

    if len(s_list) == 0:
        print("INFO: No external English subtitles detected.")
        print("INFO: Will still try to extract it from main video file.")
    else:
        assert len(s_list) == len(v_list)

    if len(v_list) == 1:
        print("Detected single video. Treat it as a movie")
        # TODO
        if len(s_list) == 0:
            convert_one(cfg, [0, 0, v_list[0], 0])
        else:
            assert len(s_list) == 1
        return

    # This is series
    print("INFO: Looks like directory contains series.")
    print("INFO: Trying to detect episodes")

    # TODO: I do not know, how to create record in Python,
    # so let it be an array per serie. Format as following:
    # (<season>, <episode>, <video file path>, [<subtitles path>])
    # this will be an array which function returns.
    e_list = analyze_episodes(cfg, v_list, s_list)

    print("INFO: List of found videos:")
    for e in e_list: print(e)

    if cfg["test_mode"]:
        print("INFO: Test mode, converting single video")
        convert_one(cfg, e_list[0])
    else:
        print("INFO: Generic mode, converting whole list")
        for e in e_list:
            convert_one(cfg, e)

if __name__ == '__main__':
    main()
