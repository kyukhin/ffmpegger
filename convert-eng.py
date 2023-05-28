#!/usr/bin/python3

# TODO: check if jq exists?

import argparse
import os
from os.path import basename, getsize, join, splitext
import re
import subprocess

def parse_args():
    cfg = {
        "fixup_names": False,
        "root_dir": os.getcwd(),
        "out_dir": os.getcwd(),
        "overwrite": False,
        "verbose": False,
        "test_mode": False,
        "a_stream": None,
        "quality": 16
    }

    parser = argparse.ArgumentParser()

    parser.add_argument("-as", "--astream",
                        help="Do not guess audio track, set it explicitly",
                        type=str)

    parser.add_argument("-d", "--directory",
                        help="Scan given directory, instead of current",
                        type=str)

    parser.add_argument("-f", "--fixup_names",
                        help="Remove special characters from filenames",
                        action="store_true")

    parser.add_argument("-o", "--output",
                        help="Set output directory",
                        type=str)

    parser.add_argument("-q", "--quality",
                        help="Set video quality. Google H264's crf for details. Default: 16",
                        type=int)

    parser.add_argument("-t", "--test",
                        help="Do a single conversion",
                        action="store_true")

    parser.add_argument("-v", "--verbose",
                        help="Be verbose (TODO: make it integer)",
                        action="store_true")

    parser.add_argument("-y", "--yes",
                        help="Overwrite output file if exists.",
                        action="store_true")

    args = parser.parse_args()

    cfg["a_stream"] = args.astream
    if args.directory: cfg["root_dir"] = args.directory
    cfg["fixup_names"] = args.fixup_names
    if args.output: cfg["out_dir"] = args.output
    cfg["verbose"] = args.verbose
    cfg["test_mode"] = args.test
    cfg["overwrite"] = args.yes
    if args.quality is not None: cfg["quality"] = args.quality

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
    regex = re.compile("s(\d+)e(\d+)", flags=re.IGNORECASE)
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

# Search a video file for English subtitles.
# Return result as a correct argument for ffmpeg convertor
def scan_eng_subtitles(cfg, video):
    ff_probe_command=["ffprobe",
                       "-i", video,
                       "-show_entries", "stream=index,codec_type:stream_tags=language",
                       "-of", "json",
                       "-v", "panic",
                       "-select_streams", "s"]
    if cfg["verbose"]: print(ff_probe_command)
    res = subprocess.run(ff_probe_command, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during ffprobe execution. Unable to fetch subtitles")
        return

    jq_filter_command=["jq",
                       "[.streams[].tags.language==\"eng\"]|index(true)",
                       "-"]
    if cfg["verbose"]: print("DBG:", jq_filter_command)
    res = subprocess.run(jq_filter_command, input=res.stdout, capture_output=True)
    if cfg["verbose"]: print("DBG:", res)
    if res.returncode != 0:
        print("ERR: error during execution of jq. Unable to fetch subtitles")
        return

    # Strip newline from result
    res = video + ":si=" + res.stdout.decode("utf-8").rstrip()
    print("INF: OK: found subtitles track inside video file", res)
    return res

# Search a video file for English audio track.
# Usually, normal English audio track appears first.
# All other "commentary" and alike are later.
# Return result as a correct argument for ffmpeg convertor
def scan_eng_astream(cfg, video):
    ff_probe_command=["ffprobe",
                       "-i", video,
                       "-show_entries", "stream=index,codec_type:stream_tags=language",
                       "-of", "json",
                       "-v", "panic",
                       "-select_streams", "a"]
    if cfg["verbose"]: print(ff_probe_command)
    res = subprocess.run(ff_probe_command, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during ffprobe execution. Unable to fetch English audio track")
        return

    jq_filter_command=["jq",
                       "[.streams[] | select(.tags.language==\"eng\").index][0]",
                       "-"]
    if cfg["verbose"]: print(jq_filter_command)
    res = subprocess.run(jq_filter_command, input=res.stdout, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during execution of jq. Unable to fetch English audio track")
        return

    # Strip newline from result
    res = res.stdout.decode("utf-8").rstrip()
    if res == "null":
        print("ERR: cannot find English audio track.")
        return

    res = "0:" + res
    print("INFO: OK: found audio track inside video file", res)
    return res

def scan_vstream(cfg, video):
    ff_probe_command=["ffprobe",
                       "-i", video,
                       "-show_entries", "stream=index",
                       "-of", "json",
                       "-v", "panic",
                       "-select_streams", "v"]
    if cfg["verbose"]: print(ff_probe_command)
    res = subprocess.run(ff_probe_command, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during ffprobe execution. Unable find video track")
        return

    jq_filter_command=["jq",
                       "[.streams[].index][0]",
                       "-"]
    if cfg["verbose"]: print(jq_filter_command)
    res = subprocess.run(jq_filter_command, input=res.stdout, capture_output=True)
    if cfg["verbose"]: print(res)
    if res.returncode != 0:
        print("ERR: error during execution of jq. Unable to find video track")
        return

    # Strip newline from result
    res = res.stdout.decode("utf-8").rstrip()
    if res == "null":
        print("ERR: cannot find video stream.")
        return

    res = "0:" + res
    print("INFO: OK: found video track inside video file", res)
    return res

def fixup_names(cfg):
    root = cfg["root_dir"]

    bad_chars = [' ', '(', ')', '[', ']', '\'', '«', '»', ',', '’']
    os.chdir(root)
    for path, subdir, files in os.walk(root, topdown=False):
        for f in subdir:
            copy_f = f
            for char in copy_f:
                if (char in bad_chars): copy_f = copy_f.replace(char, '.')
            os.rename(os.path.join(path, f),
                      os.path.join(path, copy_f))
        for f in files:
            copy_f = f
            for char in copy_f:
                if (char in bad_chars): copy_f = copy_f.replace(char, '.')
            os.rename(os.path.join(path, f),
                      os.path.join(path, copy_f))
    return

def convert_one(cfg, e):
    video = e[2]
    out = splitext(join(cfg["out_dir"], basename(video)))[0] + ".mp4"

    if not e[3]:
        subs = scan_eng_subtitles(cfg, video)
    else:
        subs = e[3]

    if not cfg["a_stream"]:
        cfg["a_stream"] = scan_eng_astream(cfg, video)

    if not cfg["a_stream"]:
        print("ERR: no audio stream defined or guessed.")
        return False

    v_stream = scan_vstream(cfg, video)

    ffmpeg_command = [
        "ffmpeg",
        "-i", video,

        # Video setting
        "-c:v", "libx264",
        "-preset", "slower",
        "-crf", str(cfg["quality"]),
        "-map", v_stream,

        # Audio setting
        "-acodec", "copy",
        "-map", cfg["a_stream"],

        # Subs setting
        "-vf", "subtitles="+subs,

        out
        ]
    if cfg["overwrite"]:
        ffmpeg_command.append("-y")
    print(ffmpeg_command)
    if subprocess.run(ffmpeg_command).returncode == 0:
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
        return

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
