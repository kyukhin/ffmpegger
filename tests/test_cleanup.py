"""cleanup-names.py: the release junk out of names already there, the results and their titles along."""

import hashlib
import importlib.util
import json
import os

import pytest

from tests.test_arrange import tree
from tests.test_convert_eng import make_video, needs_ffmpeg, title, touch

pytest.importorskip("mutagen")
spec = importlib.util.spec_from_file_location(
    "cleanup_names", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cleanup-names.py"))
cn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cn)

PROFILES = {"common": ["-v"], "output": "0conv", "downloads": "0unsort",
            "profiles": {"bmw": {"folder": "0unsort/0bmw", "args": ["-f"]},
                         "bmw-rus": {"folder": "0unsort/0bmw-rus", "results": "bmw", "args": ["-f"]},
                         "raw": {"folder": "0unsort/0raw", "args": []}}}


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@needs_ffmpeg
def test_cleanup_library(tmp_path, capsys):
    lib = tmp_path / "lib"
    season = lib / "films" / "0series" / "Show" / "s01"
    dl = lib / "0unsort" / "Show.S01"
    torrent = touch(dl / "Show.S01E01.1080p.WEB-DL.mkv")
    os.makedirs(str(season))
    os.link(torrent, str(season / "Show.S01E01.1080p.WEB-DL.mkv"))
    touch(season / "Show.S01E01.1080p.WEB-DL.en.srt")
    touch(season / "Show.S01E01.1080p.WEB-DL.ru.mka")
    touch(season / "Show.S01E02.mkv")
    touch(season / "Show.S01E03.1080p.mkv")
    touch(season / "Show.S01E03.mkv")
    # An MP4 that is also a torrent's file: renamed in the library, never written to
    seeding = make_video(dl / "Show.S01E04.WEB-DL.mp4", subs=())
    before = digest(seeding)
    os.link(seeding, str(season / "Show.S01E04.WEB-DL.mp4"))
    bmw = lib / "0conv" / "bmw" / "films" / "0series" / "Show" / "s01"
    make_video(bmw / "Show.S01E01.1080p.WEB-DL.mp4", subs=())
    make_video(bmw / "Show.S01E02.mp4", subs=())
    make_video(lib / "0conv" / "raw" / "films" / "0series" / "Show" / "s01" / "Show.S01E01.1080p.WEB-DL.mp4", subs=())
    profiles = str(tmp_path / "profiles.json")
    with open(profiles, "w") as f:
        json.dump(PROFILES, f)
    args = ["--library", str(lib), "--profiles", profiles]

    everything = tree(lib)
    assert cn.main(args + ["--dry-run", "films/0series"]) == 1
    assert tree(lib) == everything
    assert "rename films/0series/Show/s01/Show.S01E01.1080p.WEB-DL.ru.mka -> Show.S01E01.ru.mka" in \
        capsys.readouterr().out

    # The library's root: its downloads and results are not taken for videos to clean up
    assert cn.main(args + ["."]) == 1
    assert tree(lib) == sorted([
        "0conv/bmw/films/0series/Show/s01/Show.S01E01.mp4", "0conv/bmw/films/0series/Show/s01/Show.S01E02.mp4",
        "0conv/raw/films/0series/Show/s01/Show.S01E01.mp4",
        "0unsort/Show.S01/Show.S01E01.1080p.WEB-DL.mkv", "0unsort/Show.S01/Show.S01E04.WEB-DL.mp4",
        "films/0series/Show/s01/Show.S01E01.en.srt", "films/0series/Show/s01/Show.S01E01.mkv",
        "films/0series/Show/s01/Show.S01E01.ru.mka", "films/0series/Show/s01/Show.S01E02.mkv",
        "films/0series/Show/s01/Show.S01E03.1080p.mkv", "films/0series/Show/s01/Show.S01E03.mkv",
        "films/0series/Show/s01/Show.S01E04.mp4"])
    out = capsys.readouterr().out
    assert "clash films/0series/Show/s01/Show.S01E03.1080p.mkv: Show.S01E03.mkv is taken" in out
    assert "renamed 6, titled 3, clashes 1, hardlinked so title kept 1" in out
    assert title(str(bmw / "Show.S01E01.mp4")) == "Show S01E01" and title(str(bmw / "Show.S01E02.mp4")) == "Show S01E02"
    assert os.stat(torrent).st_ino == os.stat(str(season / "Show.S01E01.mkv")).st_ino
    assert digest(seeding) == before

    # Again: nothing to do
    assert cn.main(args + ["films/0series"]) == 1
    assert "renamed 0, titled 0, clashes 1" in capsys.readouterr().out


@needs_ffmpeg
def test_cleanup_any_folder(tmp_path, capsys):
    """A folder of MP4s outside the library, lectures to sort: names and titles."""
    for n in (1, 2, 10):
        make_video(tmp_path / "usb" / ("%d-Lecture.1080p.mp4" % n), subs=())
    assert cn.main([str(tmp_path / "usb")]) == 0
    assert sorted(os.listdir(str(tmp_path / "usb"))) == ["01-Lecture.mp4", "02-Lecture.mp4", "10-Lecture.mp4"]
    assert title(str(tmp_path / "usb" / "01-Lecture.mp4")) == "01-Lecture"


def test_cleanup_refuses(tmp_path):
    with pytest.raises(SystemExit):
        cn.main(["--library", str(tmp_path), str(tmp_path)])  # no --profiles
    with pytest.raises(SystemExit):
        cn.main([str(tmp_path / "missing")])
