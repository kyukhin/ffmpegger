# ffmpegger

Converts movies and whole seasons to MP4 that plays anywhere (H.264, AAC
stereo) with English subtitles burnt in, picking the audio and subtitle tracks
itself. A torrent server queues finished downloads, a desktop converts them
when it is on.

```
torrent server                          library share                 desktop (Windows)
Transmission done ──▶ enqueue.py ──▶ .convert/queue/<id>.json ◀── worker.py ──▶ convert_eng.py ──▶ <torrent>/0out/
```

## Pieces

| File | Runs on | Does |
|---|---|---|
| `ffmpegger/convert_eng.py` | anywhere with Python 3.8+ and ffmpeg | converts one movie or a season folder |
| `enqueue.py` | the torrent server | Transmission's script-torrent-done: picks a profile by folder, queues a job |
| `worker.py` | the desktop | takes jobs from the queue, runs `convert_eng.py`, files the result |
| `ffmpegger/jobs.py` | both | the queue layout |
| `profiles.json` | the torrent server | folder → `convert_eng.py` arguments |
| `install-windows.ps1` | the desktop | Python, ffmpeg, share password, logon task |
| `cleanup-names.sh`, `inject-titles.sh` | by hand | strip release junk from names, put file names into MP4 titles |
| `transmission-invoke-convert.sh`, `convert-remote.sh` | old | the previous push over ssh/rsync to a Linux VM; removed once the queue has converted a season |

## Profiles

A torrent that finishes inside one of these folders of the library is queued;
anything else is left alone. The deepest matching folder wins.

| Profile | Folder | Result |
|---|---|---|
| web | `0unsort/0web` | English audio, English subtitles burnt in, 1600 kbit/s |
| web-rus | `0unsort/0web-rus` | Russian audio, no subtitles, 1600 kbit/s |
| bmw | `0unsort/0bmw` | as web, cropped and scaled to the car's 1920x700 screen |
| ipad-rus | `0unsort/0ipad-rus` | Russian audio, no subtitles, 800 kbit/s |

Results go to `0out` inside the torrent's folder (next to the file for a
single-file torrent), with special characters in the names replaced by dots.
The sources are never changed, so Transmission keeps seeding them.

## How convert_eng.py picks things

- **Movie or season**: one video is a movie; more are episodes, numbered by
  `S01E02`, `s01x02` or `1x02` in the file name. Videos with `sample` in the
  path and the output folder are skipped
- **Subtitles**: an English `.srt` (`en`, `eng`, `english` in its path inside
  the torrent, e.g. `Sub.Eng/` or `Movie.en.srt`) for the same episode; full
  ones over SDH over forced, then the bigger file. Without one, an English
  text track inside the video. Picture subtitles (PGS, VobSub) cannot be burnt
  in: such an episode is skipped with that reason
- **Audio**: the track tagged with the wanted language (`--audio_lang`,
  default `eng`). Untagged files (`.avi`): English is taken as the second
  track, other languages as the first; `--audio_enforce N` overrides
- **Seasons resume**: each episode is written as `.part` and renamed when
  done; episodes whose result exists are skipped, `-y` converts them again
- **Exit code** 0 when everything is converted or there already, 1 when an
  episode was skipped or failed, 2 when there are no videos. `--report FILE`
  keeps a JSON report, rewritten after every episode

```
python ffmpegger/convert_eng.py -d "Show S01" -o "Show S01/0out" -f -s 1600 -r report.json
python ffmpegger/convert_eng.py -d Rio.mkv -o 0out -f -s 1600 --video_fit=1920x700
python ffmpegger/convert_eng.py --help
```

## The queue

On the library share, next to the media:

```
.convert/queue/    <id>.json waiting
.convert/running/  <id>.json taken by a worker, <id>.log and <id>.report.json growing
.convert/done/     converted or there already
.convert/failed/   something skipped or failed
.convert/enqueue.log  one line per torrent Transmission finished
```

A job moves by renaming its `.json`, which the file server does atomically, so
two workers never take the same job. A job holds the path relative to the
library root, the profile and its arguments; `worker.py` adds who took it, when,
the exit code and a summary with every skipped or failed episode and the
reason. To run a failed job again after fixing it, move its `.json` back to
`queue/` (or queue the folder again with `enqueue.py`).

The worker converts one job at a time on every core, keeps Windows from
sleeping while a job runs, and on start returns its own unfinished jobs to
the queue: a shutdown in the middle of a season costs only the episode that was
running.

## Setup

**Torrent server.** Python 3 and the repo, the library shared over Samba to the
desktop, `.convert` writable by Transmission's user and by the share's user. A
wrapper as Transmission's `script-torrent-done-filename`:

```sh
#!/bin/sh
exec /home/kyukhin/ffmpegger/enqueue.py --library /mnt/lib
```

By hand: `enqueue.py --library /mnt/lib PATH`, `--profile NAME` to choose the
profile whatever the folder.

**Desktop (Windows).** Get the repo (`git clone` or the ZIP), then in PowerShell:

```
powershell -ExecutionPolicy Bypass -File install-windows.ps1 -Library \\SERVER\lib
```

It installs Python and ffmpeg with winget when missing, asks for the share's
password once (kept in Credential Manager), and adds the task "ffmpegger
worker" that starts at logon and restarts if it dies. The worker's log is
`%LOCALAPPDATA%\ffmpegger\worker.log`; each job's own log lies next to the job.
`-Uninstall` removes the task and the password. `-Priority low` puts games and
the desktop first; on a hybrid CPU (an i9 with P- and E-cores) Windows 11 then
converts on the efficiency cores only. In normal
priority ffmpeg also opts out of Windows' power throttling (EcoQoS), which would
otherwise keep a process without a window on the E-cores. After `git pull` nothing else is
needed; the next job runs the new code.

Any other machine works the same way: `worker.py --library /Volumes/lib`, or
`--once` to empty the queue and exit.

## Tests

```
python -m pytest tests
```

The tests make tiny videos with ffmpeg and convert them for real, through the
queue too (a few seconds); without ffmpeg those are skipped.
