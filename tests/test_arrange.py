"""arrange.py: hardlinks from a download into series/<show>/sNN/ and films/<title>/."""

import json
import os

import pytest

import arrange
from ffmpegger import convert_eng as ce
from tests.test_convert_eng import touch


def tree(root):
    return sorted(os.path.relpath(os.path.join(p, f), str(root)) for p, _, fs in os.walk(str(root)) for f in fs)


def test_film_title():
    assert arrange.film_title("Rio.2011.1080p.BluRay.x264.DTS-WiKi.mkv") == "Rio (2011)"
    assert arrange.film_title("Марсианин.2015.Extended.Cut.WEB-DL.2160p.mkv") == "Марсианин (2015)"
    assert arrange.film_title("How to Train Your Dragon 2.mkv") == "How to Train Your Dragon 2"
    assert arrange.film_title("Blade Runner 2049 (2017) [1080p]") == "Blade Runner 2049 (2017)"
    assert arrange.film_title("2001.A.Space.Odyssey.1968.1080p.mkv") == "2001 A Space Odyssey (1968)"
    assert arrange.film_title("2012.2009.BDRip.mkv") == "2012 (2009)"
    assert arrange.safe('Show: "Part 2"?') == "Show   Part 2"


def test_show_pack(tmp_path):
    lib = tmp_path / "lib"
    pack = lib / "0unsort" / "Show.S01-S02.1080p.Rus.Eng"
    e1 = touch(pack / "Season 1" / "Show.S01E01.mkv")
    touch(pack / "Season 1" / "Show.S01E02.mkv")
    touch(pack / "Season 1" / "Show.S01E02.eng.srt")
    touch(pack / "Season 2" / "Show.S02E01.mkv")
    touch(pack / "Subs" / "English" / "Show.S01E01.srt", 100)
    touch(pack / "Subs" / "English SDH" / "Show.S01E01.srt", 120)
    touch(pack / "Subs" / "Rus" / "Show.S01E01.srt")
    touch(pack / "Subs" / "Ukr" / "Show.S01E01.srt")
    touch(pack / "Sample" / "sample.mkv")
    touch(pack / "Extras" / "Making of.mkv")

    r = arrange.arrange(str(lib), str(pack), show="Show")
    assert tree(lib / "series") == [
        "Show/s01/Show.S01E01.en.sdh.srt", "Show/s01/Show.S01E01.en.srt", "Show/s01/Show.S01E01.mkv",
        "Show/s01/Show.S01E01.ru.srt", "Show/s01/Show.S01E02.en.srt", "Show/s01/Show.S01E02.mkv",
        "Show/s02/Show.S02E01.mkv"]
    assert r["folders"] == ["series/Show/s01", "series/Show/s02"]
    assert r["unplaced"] == ["Extras/Making of.mkv"]
    assert os.stat(e1).st_ino == os.stat(str(lib / "series" / "Show" / "s01" / "Show.S01E01.mkv")).st_ino

    # The converter reads the arranged season: full English subtitles, not SDH
    c = ce.parse_args(["-d", str(lib / "series" / "Show" / "s01")])
    v, s = ce.scan_videos(c)
    assert [os.path.basename(e[3]) for e in ce.analyze_episodes(c, v, s)] == \
        ["Show.S01E01.en.srt", "Show.S01E02.en.srt"]

    # Again: nothing new. Another release of S02E01: kept out, reported
    assert arrange.arrange(str(lib), str(pack), show="Show")["placed"] == []
    other = touch(lib / "0unsort" / "Show.S02E01.720p.mkv")
    r = arrange.arrange(str(lib), other, show="Show")
    assert r["placed"] == [] and "s02 has Show.S02E01.mkv" in r["clash"][0]
    # A new season from another torrent joins the same show
    r = arrange.arrange(str(lib), touch(lib / "0unsort" / "S3" / "Show.S03E01.mkv"), show="Show")
    assert r["folders"] == ["series/Show/s03"]


def test_film(tmp_path):
    lib = tmp_path / "lib"
    movie = touch(lib / "0unsort" / "Rio.2011.1080p.BluRay.x264.mkv")
    r = arrange.arrange(str(lib), movie, film=True)
    assert r["folders"] == ["films/Rio (2011)"]
    folder = lib / "0unsort" / "Big.Fish.2003.1080p"
    touch(folder / "Big.Fish.2003.1080p.mkv")
    touch(folder / "Subs" / "2_English.srt")
    arrange.arrange(str(lib), str(folder), film=True)
    assert tree(lib / "films") == ["Big Fish (2003)/Big.Fish.2003.1080p.en.srt", "Big Fish (2003)/Big.Fish.2003.1080p.mkv",
                                   "Rio (2011)/Rio.2011.1080p.BluRay.x264.mkv"]


def test_cli_writes_a_record(tmp_path):
    lib = tmp_path / "lib"
    ep = touch(lib / "0unsort" / "x" / "Show.S01E01.mkv")
    assert arrange.main(["--library", str(lib), "--show", "Show", ep]) == 0
    [name] = os.listdir(str(lib / ".convert" / "arranged"))
    r = json.load(open(str(lib / ".convert" / "arranged" / name)))
    assert r["show"] == "Show" and r["placed"] == ["series/Show/s01/Show.S01E01.mkv"]
    assert arrange.main(["--library", str(lib), "--show", "Show", str(tmp_path)]) == 2  # outside the library
    with pytest.raises(SystemExit):
        arrange.main(["--library", str(lib), ep])  # neither --show nor --film
