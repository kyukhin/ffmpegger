"""Unit tests run anywhere; the ones marked needs_ffmpeg make tiny videos with
ffmpeg and convert them for real (about a second each)."""

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from ffmpegger import convert_eng as ce

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                  reason="ffmpeg is not installed")

SRT = "1\n00:00:00,000 --> 00:00:01,000\nHello\n"


def cfg(**kw):
    c = ce.parse_args([])
    c.update(kw)
    return c


def touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x" * size)
    return str(path)


def make_video(path, audio=("rus", "eng"), subs=("eng",)):
    """A one-second mkv: test picture, one sine track per audio language, one
    text subtitle track per subtitle language."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    srt = str(path) + ".input.srt"
    with open(srt, "w") as f:
        f.write(SRT)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=5"]
    for _ in audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000"]
    for _ in subs:
        cmd += ["-i", srt]
    for i in range(1 + len(audio) + len(subs)):
        cmd += ["-map", str(i)]
    for i, lang in enumerate(audio):
        cmd += ["-metadata:s:a:%d" % i, "language=" + lang]
    for i, lang in enumerate(subs):
        cmd += ["-metadata:s:s:%d" % i, "language=" + lang]
    cmd += ["-t", "1", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt", str(path)]
    subprocess.run(cmd, check=True)
    os.remove(srt)
    return str(path)


def tree_hash(root, skip):
    """Every file's path and content under root, except the skip folder."""
    h = hashlib.sha256()
    for path, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if os.path.join(path, d) != skip)
        for fn in sorted(files):
            full = os.path.join(path, fn)
            h.update(os.path.relpath(full, root).encode())
            with open(full, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


# --- names -------------------------------------------------------------------

@pytest.mark.parametrize("name,ep", [
    ("Rick.and.Morty.S01E02.Lawnmower.Dog.720p.BDRip.Rus.2xEng.mkv", (1, 2)),
    ("show.s04x03.mkv", (4, 3)),
    ("Show S01.E10.mkv", (1, 10)),
    ("Family.Guy.6x01.Blue.Harvest.mkv", (6, 1)),
    ("/lib/Show.S02E01.1920x1080/Movie.mkv", None),
    ("Movie.1920x1080.mkv", None),
    ("Rio.2011.1080p.BluRay.x264.mkv", None),
])
def test_episode_of(name, ep):
    assert ce.episode_of(name) == ep


def test_clean_name():
    assert ce.clean_name("Some Show (2001) [x].mp4") == "Some.Show.2001.x.mp4"
    assert ce.clean_name("[Group] Show «1», it’s.mp4") == "Group.Show.1.it.s.mp4"


@pytest.mark.parametrize("rel,english", [
    ("Sub.Eng/01.srt", True),
    ("Subs/English/S01E01.srt", True),
    ("Movie.en.srt", True),
    ("Show.S01E01.Rus.2xEng.srt", True),
    ("Subs/Rus/S01E01.srt", False),
    ("Revenge.S01E01.srt", False),
    ("Engine.srt", False),
])
def test_english(rel, english):
    assert bool(ce.ENGLISH.search(rel)) == english


def test_filter_path_posix():
    assert ce.filter_path("/a/it's [x]: y, z; w.mkv") == "/a/it\\\\\\'s \\[x\\]\\\\: y\\, z\\; w.mkv"


def test_filter_path_windows(monkeypatch):
    monkeypatch.setattr(ce.os, "sep", "\\")
    assert ce.filter_path("C:\\Users\\k\\a.srt") == "C\\\\:/Users/k/a.srt"
    assert ce.filter_path("\\\\srv2\\lib\\0unsort\\it's.mkv") == "//srv2/lib/0unsort/it\\\\\\'s.mkv"


# --- picking tracks ----------------------------------------------------------

def stream(i, kind, codec="", lang="", title="", **disposition):
    return {"index": i, "codec_type": kind, "codec_name": codec, "width": 1920, "height": 1080,
            "tags": {"language": lang, "title": title}, "disposition": disposition}


def test_pick_video_skips_cover():
    streams = [stream(0, "video", "mjpeg", attached_pic=1), stream(1, "video", "h264")]
    assert ce.pick_video(streams)["index"] == 1


def test_pick_audio():
    tagged = [stream(0, "video"), stream(1, "audio", lang="rus"), stream(2, "audio", lang="eng")]
    assert ce.pick_audio(cfg(), tagged)[0] == "0:2"
    assert ce.pick_audio(cfg(audio_lang="rus"), tagged)[0] == "0:1"
    untagged = [stream(0, "video"), stream(1, "audio"), stream(2, "audio")]
    assert ce.pick_audio(cfg(), untagged)[0] == "0:2"
    assert ce.pick_audio(cfg(audio_lang="rus"), untagged)[0] == "0:1"
    assert ce.pick_audio(cfg(audio_enforce=1), untagged)[0] == "0:1"
    assert ce.pick_audio(cfg(), [stream(0, "video"), stream(1, "audio")])[0] is None


def test_pick_subtitles():
    streams = [stream(0, "video"), stream(1, "audio"),
               stream(2, "subtitle", "subrip", "rus"),
               stream(3, "subtitle", "subrip", "eng", "Forced", forced=1),
               stream(4, "subtitle", "subrip", "eng", "SDH"),
               stream(5, "subtitle", "subrip", "eng", "Full")]
    assert ce.pick_subtitles(streams)[0] == 3  # si counts subtitle streams only
    assert ce.pick_subtitles(streams[:5])[0] == 2


def test_pick_subtitles_pictures():
    si, why = ce.pick_subtitles([stream(0, "video"), stream(1, "subtitle", "hdmv_pgs_subtitle", "eng")])
    assert si is None and "pictures" in why
    assert ce.pick_subtitles([stream(0, "video")])[0] is None


# --- matching external subtitles ---------------------------------------------

def test_season_subtitles(tmp_path):
    root = tmp_path / "Show.S01.1080p.Rus.Eng"
    v1 = touch(root / "Show.S01E01.mkv")
    v2 = touch(root / "Show.S01E02.mkv")
    v3 = touch(root / "Show.S01E03.mkv")
    touch(root / "Sample" / "sample.mkv")
    e1 = touch(root / "Sub.Eng" / "Show.S01E01.English.srt", 100)
    touch(root / "Sub.Eng" / "Show.S01E01.English.SDH.srt", 200)
    touch(root / "Sub.Eng" / "Show.S01E02.forced.eng.srt", 50)
    e2 = touch(root / "Sub.Eng" / "Show.S01E02.eng.srt", 40)
    touch(root / "Sub.Eng" / "readme.eng.srt")
    touch(root / "Sub.Rus" / "Show.S01E03.srt")
    c = cfg(root_dir=str(root), out_dir=str(root / "0out"))
    v_list, s_list = ce.scan_videos(c)
    assert v_list == [v1, v2, v3]
    assert len(s_list) == 5  # more English .srt than videos used to be a crash
    assert ce.analyze_episodes(c, v_list, s_list) == [(1, 1, v1, e1), (1, 2, v2, e2), (1, 3, v3, None)]


def test_movie_with_external_subtitles(tmp_path):
    movie = touch(tmp_path / "Rio.2011.1080p.mkv")
    subs = touch(tmp_path / "Rio.2011.1080p.en.srt")
    touch(tmp_path / "Rio.2011.1080p.ru.srt")
    touch(tmp_path / "Other.Movie.en.srt")
    c = cfg(root_dir=movie, out_dir=str(tmp_path / "0out"))
    v_list, s_list = ce.scan_videos(c)
    assert (v_list, s_list) == ([movie], [subs])
    assert ce.analyze_episodes(c, v_list, s_list) == [(None, None, movie, subs)]


# --- converting for real -----------------------------------------------------

@needs_ffmpeg
def test_season_converts_resumes_and_keeps_sources(tmp_path):
    root = tmp_path / "Show (2001) S01"
    make_video(root / "Show.S01E01.mkv")
    make_video(root / "Show.S01E02.mkv")
    make_video(root / "Sample" / "sample.mkv")
    out = str(root / "0out")
    report = str(tmp_path / "report.json")
    before = tree_hash(str(root), out)
    args = ["-d", str(root), "-o", out, "-f", "-s", "50", "-r", report]

    assert ce.main(args) == 0
    assert sorted(os.listdir(out)) == ["Show.S01E01.mp4", "Show.S01E02.mp4"]
    r = json.load(open(report))
    assert r["status"] == "done" and r["counts"]["converted"] == 2 and r["total"] == 2
    assert tree_hash(str(root), out) == before

    # Run again: nothing to do, and the results inside the source are not taken for episodes
    assert ce.main(args) == 0
    r = json.load(open(report))
    assert r["total"] == 2 and r["counts"]["exists"] == 2


@needs_ffmpeg
def test_missing_subtitles_skips_and_fails(tmp_path):
    root = tmp_path / "Show.S01"
    make_video(root / "Show.S01E01.mkv")
    make_video(root / "Show.S01E02.mkv", subs=())
    report = str(tmp_path / "report.json")
    args = ["-d", str(root), "-o", str(root / "0out"), "-s", "50", "-r", report]

    assert ce.main(args) == 1
    r = json.load(open(report))
    assert r["status"] == "incomplete"
    assert [(v["output"], v["status"]) for v in r["videos"]] == [("Show.S01E01.mp4", "converted"),
                                                                 ("Show.S01E02.mp4", "skipped")]
    assert "no English subtitles" in r["videos"][1]["reason"]

    assert ce.main(args + ["-ins"]) == 0


@needs_ffmpeg
def test_awkward_names(tmp_path):
    """Quotes, brackets, colons, commas, semicolons and Cyrillic in the paths the
    subtitles filter reads, embedded and external."""
    folder = tmp_path / "it's [a]; b, c=d: Тест"
    movie = make_video(folder / "Фильм it's [1], x; y.mkv", subs=())
    with open(str(folder / "Фильм it's [1], x; y.en.srt"), "w") as f:
        f.write(SRT)
    assert ce.main(["-d", movie, "-o", str(folder / "0out"), "-s", "50"]) == 0
    episode = make_video(folder / "season" / "Show it's [x]: S01E01.mkv")
    make_video(folder / "season" / "Show it's [x]: S01E02.mkv")
    assert ce.main(["-d", str(folder / "season"), "-o", str(folder / "0out2"), "-s", "50"]) == 0
    assert len(os.listdir(str(folder / "0out2"))) == 2 and os.path.exists(episode)


@needs_ffmpeg
def test_single_pass_and_video_fit(tmp_path):
    movie = make_video(tmp_path / "m.mkv")
    out = str(tmp_path / "0out")
    assert ce.main(["-d", movie, "-o", out, "-q", "30", "-vf", "160x60"]) == 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", os.path.join(out, "m.mp4")],
                           capture_output=True, text=True, check=True)
    assert probe.stdout.strip() == "160,60"


@needs_ffmpeg
def test_cyrillic_names_with_a_windows_console_encoding(tmp_path):
    """Under the worker on Windows the output goes to a file in cp1252: names in Cyrillic must not kill it."""
    folder = tmp_path / "Тест (2001)"
    make_video(folder / "Серия.S01E01.mkv")
    make_video(folder / "Серия.S01E02.mkv")
    log = tmp_path / "log.txt"
    with open(str(log), "w") as f:
        code = subprocess.call([sys.executable, ce.__file__, "-v", "-d", str(folder), "-o", str(folder / "0out"),
                                "-s", "50"], stdout=f, stderr=subprocess.STDOUT,
                               env=dict(os.environ, PYTHONIOENCODING="cp1252"))
    assert code == 0, open(str(log), encoding="utf-8").read()[-2000:]
    assert "Тест" in open(str(log), encoding="utf-8").read()


@needs_ffmpeg
def test_keep_tree_converts_a_show_season_by_season(tmp_path):
    show = tmp_path / "series" / "Better Call Saul"
    make_video(show / "s01" / "BCS.S01E01.mkv")
    make_video(show / "s02" / "BCS.S02E01.mkv")
    out = tmp_path / "0conv" / "web" / "series" / "Better Call Saul"
    report = str(tmp_path / "report.json")
    args = ["-d", str(show), "-o", str(out), "-k", "-f", "-s", "50", "-r", report]
    assert ce.main(args) == 0
    assert sorted(os.path.relpath(os.path.join(p, f), str(out)) for p, _, fs in os.walk(str(out)) for f in fs) == \
        [os.path.join("s01", "BCS.S01E01.mp4"), os.path.join("s02", "BCS.S02E01.mp4")]

    # A new season arrives: only it is converted
    make_video(show / "s03" / "BCS.S03E01.mkv")
    assert ce.main(args) == 0
    r = json.load(open(report))
    assert r["counts"] == {"converted": 1, "exists": 2, "skipped": 0, "failed": 0}
    assert r["videos"][2]["output"] == os.path.join("s03", "BCS.S03E01.mp4")
