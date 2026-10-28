# ffmpegger

Converts movies and whole seasons to MP4 that plays anywhere (H.264, AAC
stereo) with English subtitles burnt in, picking the audio and subtitle tracks
itself. A torrent server arranges finished downloads into a library and queues
them, a desktop converts them when it is on.

```
torrent server                                  library share                   desktop (Windows)
Transmission done ──▶ enqueue.py ─┬─ arrange.py ──▶ series/<Show>/s01/…
                                  └────────────▶ .convert/queue/<id>.json ◀── worker.py ──▶ 0conv/<profile>/…
```

## Pieces

| File | Runs on | Does |
|---|---|---|
| `ffmpegger/convert_eng.py` | anywhere with Python 3.8+ and ffmpeg | converts one movie or a season folder |
| `enqueue.py` | the torrent server | Transmission's script-torrent-done: by the torrent's labels (or its folder) arranges it and queues jobs |
| `arrange.py` | the torrent server | hardlinks a download into `series/<Show>/s01/` or `films/<Title>/` |
| `worker.py` | the desktop | takes jobs from the queue, runs `convert_eng.py`, files the result |
| `ffmpegger/jobs.py` | both | the queue layout |
| `profiles.json` | the torrent server | folder → `convert_eng.py` arguments |
| `install-windows.ps1` | the desktop | Python, ffmpeg, share password, logon task |
| `cleanup-names.sh`, `inject-titles.sh` | by hand | strip release junk from names, put file names into MP4 titles |
| `transmission-invoke-convert.sh`, `convert-remote.sh` | old | the previous push over ssh/rsync to a Linux VM; removed once the queue has converted a season |

## Labels

The bot puts labels on a torrent when it adds it; when the torrent finishes,
`enqueue.py` gets them from Transmission and acts on them, wherever the torrent
was downloaded:

| Label | Does |
|---|---|
| `show:Better Call Saul` | arranges the episodes into `series/Better Call Saul/s01/`, `s02/`, … by `SxxEyy` |
| `film` or `film:Rio (2011)` | arranges it into `films/Rio (2011)/`, the title from the name when not given |
| `convert:bmw` | queues a conversion with that profile, of each arranged season or film folder, else of the torrent itself; one label per profile |

Arranging uses hardlinks: no extra space, the torrent keeps seeding from where
it is, deleting the torrent leaves the library alone. Several torrents of one
show meet in one folder; an episode already there from another release is kept
and the new one reported. English and Russian `.srt` files follow their video as
`<video>.en.srt`, `<video>.ru.srt`. Each run leaves a record in
`.convert/arranged/`. By hand: `arrange.py --library /mnt/lib --show NAME PATH`,
or `enqueue.py --library /mnt/lib --labels "show:NAME,convert:web" PATH`.

## Profiles

A torrent without labels for us that finishes inside one of these folders of
the library is queued with that profile; anything else is left alone. The
deepest matching folder wins.

| Profile | Folder | Result |
|---|---|---|
| web | `0unsort/0web` | English audio, English subtitles burnt in, 1600 kbit/s |
| web-rus | `0unsort/0web-rus` | Russian audio, no subtitles, 1600 kbit/s |
| bmw | `0unsort/0bmw` | as web, cropped and scaled to the car's 1920x700 screen |
| ipad-rus | `0unsort/0ipad-rus` | Russian audio, no subtitles, 800 kbit/s |

Results go to `0conv/<profile>/` plus the source's path, with its subfolders:
`series/Better Call Saul/s01` converts into `0conv/bmw/series/Better Call Saul/s01/`,
a download into `0unsort/0web/Show.S01` into `0conv/web/Show.S01/` (the profile or
downloads folder is dropped). Special characters in file names become dots. The
sources are never changed, so Transmission keeps seeding them, and converting a
folder again only does what is not there yet.

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

On Windows the worker copies each video to the local temp folder before
converting it (`--local_copy`, skipped when the disk is short of space): ffmpeg's
subtitles filter reads the whole file before each pass, so two passes over the
share would read a 12 GB film four times. The worker converts one job at a time
on every core, keeps Windows from sleeping while a job runs, and on start returns its own unfinished jobs to
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

Run it as administrator. It installs Python and ffmpeg with winget when missing,
asks for the share's password once (kept in Credential Manager) and for this
account's Windows password (kept by Task Scheduler), and adds the task
"ffmpegger worker" that starts with Windows, before anyone logs in, and
restarts if it dies. `-AtLogon` starts it at logon instead, without the Windows
password. The worker's log is
`%LOCALAPPDATA%\ffmpegger\worker.log`; each job's own log lies next to the job.
`-Uninstall` removes the task and the password. `-Priority low` puts games and
the desktop first; on a hybrid CPU (an i9 with P- and E-cores) Windows 11 then
converts on the efficiency cores only. In normal
priority ffmpeg also opts out of Windows' power throttling (EcoQoS), which would
otherwise keep a process without a window on the E-cores. After `git pull` nothing else is
needed; the next job runs the new code.

**Power.** `power.py` on the torrent server switches the desktop on and off:
`power.py wake --mac <MAC> --interface eth0` sends a Wake-on-LAN packet;
`power.py --library /mnt/lib off <name>` (or `off-when-idle`) leaves a command
in `.convert/control/` that the worker carries out within a minute, also while
it converts, with `shutdown /s /t 60` (cancel on the machine with `shutdown /a`);
`status` lists the workers' heartbeats from `.convert/workers/`. Wake-on-LAN
needs, on an ASUS board, Advanced → APM Configuration → Power On By PCI-E
Enabled and ErP Ready Disabled; in Windows, the network card's Wake on Magic
Packet and Enable PME, its "allow this device to wake the computer" and "only a
magic packet", and Fast Startup off (`powercfg /h off`). The port's LED stays
lit when the machine is off if the card listens.

Any other machine works the same way: `worker.py --library /Volumes/lib`, or
`--once` to empty the queue and exit.

## Tests

```
python -m pytest tests
```

The tests make tiny videos with ffmpeg and convert them for real, through the
queue too (a few seconds); without ffmpeg those are skipped.
