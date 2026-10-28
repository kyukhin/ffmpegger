#!/usr/bin/python3
"""Switches the converting desktop on and off from the torrent server:

    power.py wake --mac bc:fc:e7:b5:d3:30          # Wake-on-LAN magic packet out of --interface
    power.py --library /mnt/lib off caligula        # the worker runs `shutdown /s /t 60` within a minute
    power.py --library /mnt/lib off-when-idle caligula   # ... once the queue is empty
    power.py --library /mnt/lib cancel caligula     # forget a command not yet carried out
    power.py --library /mnt/lib status              # every worker's last heartbeat

Off goes through the worker (worker.py), which reads .convert/control/<name>.json
on the share, also while it converts; so the worker must be running, which it
is after the user logs in. Wake needs Wake-on-LAN in the BIOS and the network
card's driver, see README.md."""

import argparse
import datetime
import os
import socket
import sys

from ffmpegger import jobs

ONLINE = 180  # seconds: a worker seen more recently than this is on (it beats every 20 to 60 s)


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


def main(argv=None):
    p = argparse.ArgumentParser(description="Switch the converting desktop on and off.")
    p.add_argument("--library", help="library root on this server (for off, off-when-idle, cancel, status)")
    sub = p.add_subparsers(dest="action", required=True)
    w = sub.add_parser("wake")
    w.add_argument("--mac", required=True)
    w.add_argument("--interface", help="send out of this network interface, e.g. eth0")
    w.add_argument("--broadcast", action="append", help="broadcast address, default 255.255.255.255")
    for action in ("off", "off-when-idle", "cancel"):
        sub.add_parser(action).add_argument("name", help="the worker's name, its machine's hostname")
    sub.add_parser("status")
    a = p.parse_args(argv)

    if a.action == "wake":
        wake(a.mac, a.interface, a.broadcast or ("255.255.255.255",))
        print("magic packet sent to", a.mac)
        return 0
    if not a.library:
        p.error("--library is needed for " + a.action)
    if a.action == "status":
        for name, beat, ago in statuses(a.library):
            state = ("on, " + beat["state"] + (" " + beat["job"] if beat.get("job") else "")) if ago < ONLINE \
                else "off or not running"
            print("%-12s %s, last seen %s (%d s ago)" % (name, state, beat["seen"], ago))
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
