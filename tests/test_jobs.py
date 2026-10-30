"""The queue: enqueue.py on the server side, worker.py on the converting side,
with a temp folder standing in for the share."""

import json
import os

import pytest

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


def test_parse_labels():
    assert enqueue.parse_labels("show:Better Call Saul, convert:bmw,convert:web,kids") == \
        {"show": "Better Call Saul", "film": None, "convert": ["bmw", "web"]}
    assert enqueue.parse_labels("film,convert:web+bmw")["film"] is True
    assert enqueue.parse_labels("film:Rio (2011)")["film"] == "Rio (2011)"
    assert enqueue.parse_labels(None) == {"show": None, "film": None, "convert": []}


def test_labels(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    args = ["--library", str(lib), "--profiles", profiles_file(tmp_path)]
    pack = lib / "0unsort" / "Show.S01-S02"
    touch(pack / "Show.S01E01.mkv")
    touch(pack / "Show.S02E01.mkv")
    monkeypatch.setenv("TR_TORRENT_DIR", str(lib / "0unsort"))
    monkeypatch.setenv("TR_TORRENT_NAME", "Show.S01-S02")
    monkeypatch.setenv("TR_TORRENT_LABELS", "show:Show,convert:web")
    assert enqueue.main(args) == 0
    assert os.path.exists(str(lib / "series" / "Show" / "s02" / "Show.S02E01.mkv"))
    assert [(j["path"], j["output"]) for _, j in jobs.listing(str(lib), "queue")] == [
        ("series/Show/s01", "0conv/web/series/Show/s01"), ("series/Show/s02", "0conv/web/series/Show/s02")]
    assert len(os.listdir(str(lib / ".convert" / "arranged"))) == 1

    # Two profiles, no arranging: the torrent itself, once per profile; an unknown profile is said
    movie = touch(lib / "0unsort" / "Rio.2011.mkv")
    monkeypatch.setenv("TR_TORRENT_NAME", "Rio.2011.mkv")
    monkeypatch.setenv("TR_TORRENT_LABELS", "convert:web,convert:web-rus,convert:nope")
    assert enqueue.main(args) == 1
    rio = [(j["profile"], j["output"]) for _, j in jobs.listing(str(lib), "queue") if j["path"] == "0unsort/Rio.2011.mkv"]
    assert sorted(rio) == [("web", "0conv/web"), ("web-rus", "0conv/web-rus")]
    assert "no profile nope" in open(str(lib / ".convert" / "enqueue.log")).read()

    # A film with its own title, arranged and converted
    monkeypatch.setenv("TR_TORRENT_LABELS", "film:Rio (2011),convert:web")
    assert enqueue.main(args) == 0
    assert os.path.exists(str(lib / "films" / "Rio (2011)" / "Rio.2011.mkv")) and os.path.exists(movie)
    assert ("films/Rio (2011)", "0conv/web/films/Rio (2011)") in [(j["path"], j["output"])
                                                                   for _, j in jobs.listing(str(lib), "queue")]

    # A label that is not ours: the profile folder decides, as before
    touch(lib / "0unsort" / "0web" / "Kids" / "Kids.S01E01.mkv")
    monkeypatch.setenv("TR_TORRENT_DIR", str(lib / "0unsort" / "0web"))
    monkeypatch.setenv("TR_TORRENT_NAME", "Kids")
    monkeypatch.setenv("TR_TORRENT_LABELS", "kids")
    assert enqueue.main(args) == 0
    assert ("0unsort/0web/Kids", "web") in [(j["path"], j["profile"]) for _, j in jobs.listing(str(lib), "queue")]


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


def test_magic_packet():
    import power
    packet = power.magic_packet("bc:fc:e7:b5:d3:30")
    assert len(packet) == 102 and packet[:6] == b"\xff" * 6 and packet[6:12] == bytes.fromhex("bcfce7b5d330")
    assert power.magic_packet("BC-FC-E7-B5-D3-30") == packet
    with pytest.raises(ValueError):
        power.magic_packet("bc:fc:e7")


def test_worker_heartbeat_and_power_commands(tmp_path, monkeypatch):
    import power
    lib = tmp_path / "lib"
    jobs.ensure(str(lib))
    offs = []
    monkeypatch.setattr(worker, "power_off", lambda library, name, why: offs.append(why) or
                        os.remove(jobs.control_file(library, name)))
    assert worker.main(worker_args(lib, tmp_path)) == 0
    [(name, beat, ago)] = power.statuses(str(lib))
    assert name == "pc" and beat["state"] == "idle" and ago < 60

    # off-when-idle waits for an empty queue; off goes at once
    assert power.main(["--library", str(lib), "off-when-idle", "pc"]) == 0
    put(lib, "1-gone", path="gone/x")
    assert worker.main(worker_args(lib, tmp_path)) == 0
    assert offs == ["the queue is empty"] and [i for i, _ in jobs.listing(str(lib), "failed")] == ["1-gone"]
    assert power.main(["--library", str(lib), "off", "pc"]) == 0
    assert worker.main(worker_args(lib, tmp_path)) == 0
    assert offs[-1] == "asked to switch off"
    assert power.main(["--library", str(lib), "off", "pc"]) == 0
    assert power.main(["--library", str(lib), "cancel", "pc"]) == 0
    assert not os.path.exists(jobs.control_file(str(lib), "pc"))


def test_power_auto(tmp_path, monkeypatch):
    import datetime
    import power
    lib = str(tmp_path / "lib")
    jobs.ensure(lib)
    sent = []
    monkeypatch.setattr(power, "wake", lambda mac, interface=None, broadcasts=None: sent.append(mac))
    t0 = datetime.datetime(2026, 10, 28, 12, 0).timestamp()

    def beat(at, state="idle", users=()):
        os.makedirs(os.path.dirname(jobs.worker_file(lib, "pc")), exist_ok=True)
        seen = datetime.datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")
        jobs.write(jobs.worker_file(lib, "pc"), {"name": "pc", "seen": seen, "state": state,
                                                  "users": list(users) if users is not None else None})

    def run(minutes):
        return power.auto(lib, "pc", "bc:fc:e7:b5:d3:30", idle_off=10, wake_every=10, now=t0 + minutes * 60)

    # Off with nothing to do: left alone. A job comes: woken, then again only after wake_every
    assert run(0) is None and sent == []
    put(tmp_path / "lib", "1-job", path="x")
    assert run(1)[0] == "wake" and len(sent) == 1
    assert run(5) is None and len(sent) == 1
    assert run(11)[0] == "stuck" and len(sent) == 2

    # It came up, took the job and finished it: idle for 10 minutes with nobody logged in, then off
    os.remove(jobs.file(lib, "queue", "1-job"))
    beat(t0 + 12 * 60)
    assert run(12) is None
    beat(t0 + 21 * 60)
    assert run(21) is None
    beat(t0 + 22 * 60)
    assert run(22)[0] == "off"
    assert jobs.read(jobs.control_file(lib, "pc"))["action"] == "off"
    assert [e["kind"] for e in jobs.read(jobs.power_file(lib, "pc"))["events"]] == ["wake", "stuck", "off"]
    assert [e["waiting"] for e in jobs.read(jobs.power_file(lib, "pc"))["events"]] == [1, 1, 0]


def test_power_auto_leaves_people_alone(tmp_path, monkeypatch):
    import datetime
    import power
    lib = str(tmp_path / "lib")
    jobs.ensure(lib)
    monkeypatch.setattr(power, "wake", lambda *a, **k: None)
    t0 = datetime.datetime(2026, 10, 28, 12, 0).timestamp()

    def beat(at, users):
        os.makedirs(os.path.dirname(jobs.worker_file(lib, "pc")), exist_ok=True)
        seen = datetime.datetime.fromtimestamp(at).astimezone().isoformat(timespec="seconds")
        jobs.write(jobs.worker_file(lib, "pc"), {"name": "pc", "seen": seen, "state": "idle", "users": users})

    # Switched on by hand: never woken by us, so never switched off by us
    for m in range(0, 30, 5):
        beat(t0 + m * 60, [])
        assert power.auto(lib, "pc", "aa:bb:cc:dd:ee:ff", now=t0 + m * 60) is None

    # Woken by us, but someone logged in, or it cannot tell who is: left on
    put(tmp_path / "lib", "1-job", path="x")
    assert power.auto(lib, "pc", "aa:bb:cc:dd:ee:ff", now=t0 + 3600)[0] == "wake"
    os.remove(jobs.file(lib, "queue", "1-job"))
    for users in (["kyukh"], None, []):
        for m in range(61, 90, 5):
            beat(t0 + m * 60, users)
            assert power.auto(lib, "pc", "aa:bb:cc:dd:ee:ff", now=t0 + m * 60) is None
    assert not os.path.exists(jobs.control_file(lib, "pc"))


def test_power_is_on(monkeypatch):
    import datetime
    import power
    now = datetime.datetime(2026, 10, 28, 12, 0).timestamp()
    seen = datetime.datetime.fromtimestamp(now - 30).astimezone().isoformat(timespec="seconds")
    monkeypatch.setattr(power, "answers", lambda ip: ip == "192.168.1.201")
    assert power.is_on({"seen": seen, "state": "idle", "ip": "192.168.1.201"}, now)
    assert not power.is_on({"seen": seen, "state": "idle", "ip": "192.168.1.202"}, now)  # switched off by hand
    assert not power.is_on({"seen": seen, "state": "off", "ip": "192.168.1.201"}, now)   # shutting down
    assert power.is_on({"seen": seen, "state": "idle"}, now)                              # no address: the beat
    old = datetime.datetime.fromtimestamp(now - 600).astimezone().isoformat(timespec="seconds")
    assert not power.is_on({"seen": old, "state": "idle", "ip": "192.168.1.201"}, now)


def test_worker_address():
    assert worker.address("/Volumes/lib") is None
    assert worker.address("\\\\127.0.0.1\\lib") == "127.0.0.1"


def test_update_pulls_the_checkout(tmp_path, monkeypatch):
    import subprocess

    def sh(cwd, *args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), check=True,
                       capture_output=True)

    origin, clone = tmp_path / "origin", tmp_path / "clone"
    origin.mkdir()
    sh(origin, "init", "-q", "-b", "master")
    touch(origin / "a.py")
    sh(origin, "add", "a.py")
    sh(origin, "commit", "-q", "-m", "one")
    sh(tmp_path, "clone", "-q", str(origin), str(clone))
    monkeypatch.setattr(worker, "HERE", str(clone))
    assert worker.update() is False
    touch(origin / "b.py")
    sh(origin, "add", "b.py")
    sh(origin, "commit", "-q", "-m", "two")
    assert worker.update() is True and os.path.exists(str(clone / "b.py"))
    assert worker.version() == subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(origin),
                                              capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(worker, "HERE", str(tmp_path / "not-a-checkout"))
    assert worker.update() is False


def test_worker_restarts_for_new_code_before_a_job(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    put(lib, "1-job", path="x")
    monkeypatch.setattr(worker, "update", lambda: True)
    args = [a for a in worker_args(lib, tmp_path) if a != "--once"] + ["--child"]
    assert worker.main(args) == worker.RESTART
    assert [i for i, _ in jobs.listing(str(lib), "queue")] == ["1-job"]  # left for the new code


def test_supervisor_starts_the_worker_again(monkeypatch):
    codes, started, slept, updates = [worker.RESTART, 1], [], [], []

    class Child:
        _handle = 0

        def __init__(self, cmd, **kw):
            if not codes:
                raise KeyboardInterrupt  # enough
            started.append(cmd)
            self.code = codes.pop(0)

        def wait(self):
            return self.code

    monkeypatch.setattr(worker.subprocess, "Popen", Child)
    monkeypatch.setattr(worker.time, "sleep", slept.append)
    monkeypatch.setattr(worker, "update", lambda: updates.append(1))
    with pytest.raises(KeyboardInterrupt):
        worker.supervise(["--library", "L"])
    assert len(started) == 2 and started[0][-3:] == ["--child", "--library", "L"]
    assert slept == [60] and updates == [1]  # at once after an update, after a minute and a pull when it died


def test_paused_worker_takes_no_job(tmp_path):
    import power
    lib = tmp_path / "lib"
    put(lib, "1-gone", path="gone/x")
    assert power.main(["--library", str(lib), "pause", "pc", "--by", "@me"]) == 0
    assert jobs.read(jobs.pause_file(str(lib), "pc"))["by"] == "@me"
    assert worker.main(worker_args(lib, tmp_path)) == 0
    assert [i for i, _ in jobs.listing(str(lib), "queue")] == ["1-gone"]
    assert jobs.read(jobs.worker_file(str(lib), "pc"))["state"] == "paused"
    assert power.main(["--library", str(lib), "resume", "pc"]) == 0
    assert worker.main(worker_args(lib, tmp_path)) == 0
    assert [i for i, _ in jobs.listing(str(lib), "failed")] == ["1-gone"]


@pytest.mark.skipif(os.name == "nt", reason="ps and signals")
def test_pause_freezes_the_converter_and_its_children(tmp_path):
    import subprocess
    import time
    lib = str(tmp_path / "lib")
    proc = subprocess.Popen(["sh", "-c", "sleep 30 & wait"])
    try:
        for _ in range(50):
            if len(worker.members(proc, None)) == 2:
                break
            time.sleep(0.1)

        def stat(pid):
            return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()

        assert worker.hold(lib, "pc", proc, None, None) is None  # not paused
        os.makedirs(os.path.dirname(jobs.pause_file(lib, "pc")))
        jobs.write(jobs.pause_file(lib, "pc"), {})
        frozen = worker.hold(lib, "pc", proc, None, None)
        assert sorted(frozen) == sorted(worker.members(proc, None)) and len(frozen) == 2
        assert all(stat(p).startswith("T") for p in frozen)
        assert worker.hold(lib, "pc", proc, None, frozen) == frozen  # still paused: nothing again
        os.remove(jobs.pause_file(lib, "pc"))
        assert worker.hold(lib, "pc", proc, None, frozen) is None
        assert not any(stat(p).startswith("T") for p in frozen)
    finally:
        for p in worker.members(proc, None):
            os.kill(p, 9)
        proc.wait()


def test_power_auto_does_not_wake_a_paused_worker(tmp_path, monkeypatch):
    import power
    lib = str(tmp_path / "lib")
    sent = []
    monkeypatch.setattr(power, "wake", lambda *a, **k: sent.append(1))
    put(tmp_path / "lib", "1-job", path="x")
    assert power.main(["--library", lib, "pause", "pc"]) == 0
    assert power.auto(lib, "pc", "aa:bb:cc:dd:ee:ff") is None and sent == []
    assert power.main(["--library", lib, "resume", "pc"]) == 0
    assert power.auto(lib, "pc", "aa:bb:cc:dd:ee:ff")[0] == "wake" and sent == [1]
