"""The queue: enqueue.py on the server side, worker.py on the converting side,
with a temp folder standing in for the share."""

import json
import os

import enqueue
import worker
from ffmpegger import jobs
from tests.test_convert_eng import make_video, needs_ffmpeg, touch

PROFILES = {"web": {"folder": "0unsort/0web", "args": ["-s", "50"]},
            "web-rus": {"folder": "0unsort/0web-rus", "args": ["-s", "50", "--audio_lang=rus", "-sd"]},
            "deep": {"folder": "0unsort/0web/deep", "args": []}}


def profiles_file(tmp_path):
    path = str(tmp_path / "profiles.json")
    with open(path, "w") as f:
        json.dump({"common": ["-f"], "output": "0conv", "downloads": "0unsort", "profiles": PROFILES}, f)
    return path


def test_match():
    assert enqueue.match(PROFILES, "0unsort/0web/Show.S01") == "web"
    assert enqueue.match(PROFILES, "0unsort/0web-rus/Show.S01") == "web-rus"
    assert enqueue.match(PROFILES, "0unsort/0web/deep/Show.S01") == "deep"
    assert enqueue.match(PROFILES, "0unsort/0webx/Show.S01") is None
    assert enqueue.match(PROFILES, "films/Show.S01") is None


def test_enqueue(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    show = touch(lib / "0unsort" / "0web" / "Show S01" / "Show.S01E01.mkv")
    args = ["--library", str(lib), "--profiles", profiles_file(tmp_path)]

    monkeypatch.setenv("TR_TORRENT_DIR", str(lib / "0unsort" / "0web"))
    monkeypatch.setenv("TR_TORRENT_NAME", "Show S01")
    assert enqueue.main(args) == 0
    [(job_id, job)] = jobs.listing(str(lib), "queue")
    assert job["path"] == "0unsort/0web/Show S01" and job["profile"] == "web"
    assert job["output"] == "0conv/web/Show S01"
    assert job["args"] == ["-f", "-s", "50"]

    assert enqueue.main(args) == 0  # finished again: not queued twice
    assert len(jobs.listing(str(lib), "queue")) == 1
    assert enqueue.main(args + ["--profile", "web-rus"]) == 0  # the same torrent for another profile is fine
    assert len(jobs.listing(str(lib), "queue")) == 2
    os.remove(jobs.file(str(lib), "queue", jobs.listing(str(lib), "queue")[1][0]))

    assert enqueue.main(args + [str(touch(lib / "films" / "Rio.mkv"))]) == 0  # no profile there
    assert enqueue.main(args + ["--profile", "web-rus", str(lib / "films" / "Rio.mkv")]) == 0
    assert [j["profile"] for _, j in jobs.listing(str(lib), "queue")] == ["web", "web-rus"]
    assert enqueue.main(args + [str(tmp_path)]) == 1  # outside the library
    log = open(str(lib / ".convert" / "enqueue.log")).read()
    assert "no profile, not queued: films/Rio.mkv" in log and os.path.exists(show)


def test_output_of():
    cfg = {"output": "0conv", "downloads": "0unsort", "profiles": PROFILES}
    assert enqueue.output_of(cfg, "web", "0unsort/0web/Show.S01", True) == "0conv/web/Show.S01"
    assert enqueue.output_of(cfg, "web", "0unsort/0web/Rio.mkv", False) == "0conv/web"
    assert enqueue.output_of(cfg, "web", "0unsort/0web/deep/Show", True) == "0conv/web/Show"
    assert enqueue.output_of(cfg, "bmw", "0unsort/0web-rus/Show", True) == "0conv/bmw/Show"
    assert enqueue.output_of(cfg, "web", "0unsort/Dragon 2.mkv", False) == "0conv/web"
    assert enqueue.output_of(cfg, "bmw", "series/Better Call Saul", True) == "0conv/bmw/series/Better Call Saul"
    assert enqueue.output_of(cfg, "bmw", "series/Better Call Saul/s03", True) == "0conv/bmw/series/Better Call Saul/s03"


def worker_args(lib, tmp_path):
    return ["--library", str(lib), "--once", "--name", "pc", "--log", str(tmp_path / "worker.log")]


def put(lib, job_id, state="queue", **job):
    jobs.ensure(str(lib))
    jobs.write(jobs.file(str(lib), state, job_id), dict({"id": job_id, "args": []}, **job))


def test_worker_refuses(tmp_path):
    lib = tmp_path / "lib"
    touch(lib / "0unsort" / "0web" / "x.mkv")
    put(lib, "1-gone", path="0unsort/0web/missing")
    put(lib, "2-paths", path="0unsort/0web/x.mkv", args=["-o", "/tmp"])
    put(lib, "3-out", path="0unsort/0web/x.mkv", output="0conv/../../etc")
    assert worker.main(worker_args(lib, tmp_path)) == 0
    failed = dict(jobs.listing(str(lib), "failed"))
    assert failed["1-gone"]["reason"].startswith("the source is gone")
    assert failed["2-paths"]["reason"].startswith("the job sets paths itself")
    assert "bad output folder" in failed["3-out"]["reason"]


def test_worker_takes_back_its_own_interrupted_jobs(tmp_path):
    lib = tmp_path / "lib"
    put(lib, "1-mine", "running", path="gone/a", worker="pc")
    put(lib, "2-other", "running", path="gone/b", worker="laptop")
    worker.requeue_own(str(lib), "pc")
    assert [i for i, _ in jobs.listing(str(lib), "queue")] == ["1-mine"]
    assert [i for i, _ in jobs.listing(str(lib), "running")] == ["2-other"]


@needs_ffmpeg
def test_season_through_the_queue(tmp_path):
    lib = tmp_path / "lib"
    season = lib / "0unsort" / "0web" / "Show (2001) S01"
    make_video(season / "Show.S01E01.mkv")
    make_video(season / "Show.S01E02.mkv", subs=())
    rus = make_video(lib / "0unsort" / "0web-rus" / "Rio.mkv", subs=())
    args = ["--library", str(lib), "--profiles", profiles_file(tmp_path)]
    assert enqueue.main(args + [str(season)]) == 0
    assert enqueue.main(args + [rus]) == 0

    assert worker.main(worker_args(lib, tmp_path)) == 0
    [(rus_id, rus_job)] = jobs.listing(str(lib), "done")
    assert rus_job["profile"] == "web-rus" and rus_job["exit"] == 0
    assert os.path.exists(str(lib / "0conv" / "web-rus" / "Rio.mp4"))

    # E02 has no English subtitles: the job fails, E01 is converted, the reason is in the job
    [(job_id, job)] = jobs.listing(str(lib), "failed")
    assert job["exit"] == 1 and job["worker"] == "pc"
    assert job["result"]["counts"]["converted"] == 1
    assert job["result"]["problems"][0]["video"] == "Show.S01E02.mkv"
    assert os.listdir(str(lib / "0conv" / "web" / "Show (2001) S01")) == ["Show.S01E01.mp4"]
    for ext in (".log", ".report.json"):
        assert os.path.exists(jobs.file(str(lib), "failed", job_id, ext))
    assert "OK: FFmpeg pass 2 finished" in open(jobs.file(str(lib), "failed", job_id, ".log")).read()
