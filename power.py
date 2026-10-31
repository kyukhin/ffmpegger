#!/usr/bin/python3
"""Switches the converting desktop on and off from the torrent server:

    power.py wake --mac bc:fc:e7:b5:d3:30          # Wake-on-LAN magic packet out of --interface
    power.py --library /mnt/lib off caligula        # the worker runs `shutdown /s /t 60` within a minute
    power.py --library /mnt/lib off-when-idle caligula   # ... once the queue is empty
    power.py --library /mnt/lib cancel caligula     # forget a command not yet carried out
    power.py --library /mnt/lib pause caligula      # convert nothing: the running job freezes where it is
    power.py --library /mnt/lib resume caligula     # go on
    power.py --library /mnt/lib low caligula        # someone plays: idle priority on half of the CPUs
    power.py --library /mnt/lib normal caligula     # full speed again
    power.py --library /mnt/lib status              # every worker's last heartbeat
    power.py --library /mnt/lib auto --name caligula --mac bc:fc:e7:b5:d3:30 --interface eth0
                                                    # once a minute: wake for jobs, off when idle (see auto)

Off goes through the worker (worker.py), which reads .convert/control/<name>.json
on the share, also while it converts; so the worker must be running, which it
is after the user logs in. Pause stays until resume, through reboots, and auto
does not wake a paused worker; low likewise until normal. Wake needs Wake-on-LAN in the BIOS and the
network card's driver, see README.md."""

import argparse
import datetime
import os
import socket
import subprocess
import sys

from ffmpegger import jobs

ONLINE = 180  # seconds: a worker seen more recently than this is on (it beats every 20 to 60 s)
EVENTS_KEEP = 20


def magic_packet(mac):
    digits = mac.replace(":", "").replace("-", "").lower()
    if len(digits) != 12 or any(c not in "0123456789abcdef" for c in digits):
        raise ValueError("not a MAC address: " + mac)
    return b"\xff" * 6 + bytes.fromhex(digits) * 16


def wake(mac, interface=None, broadcasts=("255.255.255.255",)):
    packet = magic_packet(mac)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    if interface:
        # Out of the LAN port, not a VPN or Docker bridge (SO_BINDTODEVICE, Linux)
        s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_BINDTODEVICE", 25), interface.encode() + b"\0")
    for addr in broadcasts:
        for port in (9, 7):
            s.sendto(packet, (addr, port))
    s.close()


def statuses(library):
    """[(name, heartbeat, seconds since)] of every worker that ever beat."""
    folder = os.path.dirname(jobs.worker_file(library, "x"))
    out = []
    for n in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if n.endswith(".json") and not n.startswith("."):
            try:
                beat = jobs.read(os.path.join(folder, n))
            except (OSError, ValueError):
                continue
            seen = datetime.datetime.fromisoformat(beat["seen"]).timestamp()
            out.append((beat["name"], beat, datetime.datetime.now().timestamp() - seen))
    return out


def answers(ip):
    """One ping, a second at most: whether the machine is there at all."""
    try:
        return subprocess.call(["ping", "-c1", "-W1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except OSError:
        return True  # cannot tell: trust the heartbeat


def is_on(beat, now):
    """A fresh heartbeat, not saying it is shutting down, from a machine that answers a ping."""
    if not beat or beat.get("state") == "off" or now - ts(beat["seen"]) >= ONLINE:
        return False
    return answers(beat["ip"]) if beat.get("ip") else True


def ts(iso):
    return datetime.datetime.fromisoformat(iso).timestamp() if iso else None


def auto(library, name, mac, interface=None, idle_off=10, wake_every=10, now=None):
    """One look, run every minute on the torrent server. Jobs waiting and the worker off: wake it, again
    every wake_every minutes while it stays off. The worker on, woken by this, nobody logged in since,
    the queue empty and the worker idle for idle_off minutes: switch it off. A machine someone switched
    on or logged in to is theirs and is left alone. Returns the event it caused, or None; the events
    are kept in .convert/power/<name>.json for the bot."""
    now = now or datetime.datetime.now().timestamp()
    path = jobs.power_file(library, name)
    try:
        st = jobs.read(path)
    except (OSError, ValueError):
        st = {}
    try:
        beat = jobs.read(jobs.worker_file(library, name))
    except (OSError, ValueError):
        beat = None
    online = is_on(beat, now)
    waiting = len(jobs.listing(library, "queue"))
    paused = os.path.exists(jobs.pause_file(library, name))
    mine = [i for i, j in jobs.listing(library, "running") if j.get("worker") == name]
    users = beat.get("users") if beat else None
    event = None

    if online:
        st["wake_tries"] = 0
        if users:
            st["woken"] = None  # someone logged in: their session from now on
        if st.get("woken") and users == [] and not waiting and not mine and beat.get("state") == "idle":
            st["idle_since"] = st.get("idle_since") or now
            if now - st["idle_since"] >= idle_off * 60:
                os.makedirs(os.path.dirname(jobs.control_file(library, name)), exist_ok=True)
                jobs.write(jobs.control_file(library, name), {"action": "off", "at": jobs.now(), "by": "power.py auto"})
                event = ("off", "switching off: nothing to do for %d min, nobody logged in" % idle_off)
                st["woken"] = st["idle_since"] = None
        else:
            st["idle_since"] = None
    else:
        st["idle_since"] = None
        if not waiting or paused:
            st["wake_tries"] = 0
        elif not st.get("last_wake") or now - st["last_wake"] >= wake_every * 60:
            wake(mac, interface)
            st["last_wake"], st["woken"] = now, now
            st["wake_tries"] = st.get("wake_tries", 0) + 1
            event = (("wake", "woken: %d job%s waiting" % (waiting, "" if waiting == 1 else "s"))
                     if st["wake_tries"] == 1 else
                     ("stuck", "still off after %d wake packets, %d jobs waiting" % (st["wake_tries"], waiting)))
    if event:
        st["events"] = (st.get("events", []) + [{"t": jobs.now(), "kind": event[0], "text": event[1],
                                                 "waiting": waiting}])[-EVENTS_KEEP:]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    jobs.write(path, st)
    return event


def main(argv=None):
    p = argparse.ArgumentParser(description="Switch the converting desktop on and off.")
    p.add_argument("--library", help="library root on this server (for off, off-when-idle, cancel, status)")
    sub = p.add_subparsers(dest="action", required=True)
    w = sub.add_parser("wake")
    w.add_argument("--mac", required=True)
    w.add_argument("--interface", help="send out of this network interface, e.g. eth0")
    w.add_argument("--broadcast", action="append", help="broadcast address, default 255.255.255.255")
    for action in ("off", "off-when-idle", "cancel", "pause", "resume", "low", "normal"):
        sub.add_parser(action).add_argument("name", help="the worker's name, its machine's hostname")
    for action in ("pause", "low"):
        sub.choices[action].add_argument("--by", help="who asked, for the bot; this host by default")
    sub.choices["low"].add_argument("--cpus", type=float, default=0.5, help="share of the CPUs to use, half by default")
    sub.add_parser("status")
    au = sub.add_parser("auto")
    au.add_argument("--name", required=True)
    au.add_argument("--mac", required=True)
    au.add_argument("--interface")
    au.add_argument("--idle-off", type=int, default=10, help="minutes idle before switching off a machine it woke")
    au.add_argument("--wake-every", type=int, default=10, help="minutes between wake packets while it stays off")
    a = p.parse_args(argv)

    if a.action == "wake":
        wake(a.mac, a.interface, a.broadcast or ("255.255.255.255",))
        print("magic packet sent to", a.mac)
        return 0
    if not a.library:
        p.error("--library is needed for " + a.action)
    if a.action == "auto":
        event = auto(a.library, a.name, a.mac, a.interface, a.idle_off, a.wake_every)
        if event:
            print("%s: %s" % (a.name, event[1]))
        return 0
    if a.action == "status":
        now = datetime.datetime.now().timestamp()
        for name, beat, ago in statuses(a.library):
            state = ("on, " + beat["state"] + (" " + beat["job"] if beat.get("job") else "")) if is_on(beat, now) \
                else "off or not running"
            pause = " (pause asked)" if os.path.exists(jobs.pause_file(a.library, name)) else ""
            pause += " (low asked)" if os.path.exists(jobs.low_file(a.library, name)) else ""
            print("%-12s %s%s, last seen %s (%d s ago)" % (name, state, pause, beat["seen"], ago))
        return 0
    if a.action in ("pause", "resume"):
        path = jobs.pause_file(a.library, a.name)
        if a.action == "pause":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            jobs.write(path, {"at": jobs.now(), "by": a.by or socket.gethostname()})
            print("%s: paused until resume, a running job freezes within 20 s" % a.name)
        elif os.path.exists(path):
            os.remove(path)
            print("%s: goes on within 20 s" % a.name)
        else:
            print("%s was not paused" % a.name)
        return 0
    if a.action in ("low", "normal"):
        path = jobs.low_file(a.library, a.name)
        if a.action == "low":
            if not 0 < a.cpus <= 1:
                p.error("--cpus is a share: more than 0, at most 1")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            jobs.write(path, {"at": jobs.now(), "by": a.by or socket.gethostname(), "cpus": a.cpus})
            print("%s: idle priority on %d%% of the CPUs until normal, within 20 s" % (a.name, round(a.cpus * 100)))
        elif os.path.exists(path):
            os.remove(path)
            print("%s: full speed again within 20 s" % a.name)
        else:
            print("%s was not lowered" % a.name)
        return 0
    path = jobs.control_file(a.library, a.name)
    if a.action == "cancel":
        try:
            os.remove(path)
            print("cancelled the command for", a.name)
        except FileNotFoundError:
            print("no command waiting for", a.name)
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    jobs.write(path, {"action": a.action, "at": jobs.now(), "by": socket.gethostname()})
    print("%s: %s within a minute" % (a.name, "switching off" if a.action == "off" else "off when the queue is empty"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
