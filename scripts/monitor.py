#!/usr/bin/env python3
"""Profiling logger. Writes CSV files (each with a timestamp column) into --out:
  host_cpu.csv  overall host CPU %, every 1 s
  pods.csv      `kubectl top pods -n emi` for every pod, every 15 s
  hpa.csv       (with --hpa) HPA replicas + average CPU utilization, every 15 s
Stop with Ctrl+C (or --duration)."""
import argparse
import csv
import json
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import psutil

NS = "emi"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def kubectl(*args):
    """stdout of a kubectl command, or None if it failed."""
    try:
        r = subprocess.run(["kubectl", *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


class Csv:
    def __init__(self, path, header):
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(header)

    def row(self, *values):
        self.w.writerow(values)
        self.f.flush()


def to_millicores(s):
    return float(s[:-1]) if s.endswith("m") else float(s) * 1000


def to_mib(s):
    for unit, factor in (("Ki", 1 / 1024), ("Mi", 1), ("Gi", 1024)):
        if s.endswith(unit):
            return float(s[:-2]) * factor
    return float(s) / 2**20  # plain bytes


def host_loop(out, stop):
    while not stop.is_set():
        out.row(now(), psutil.cpu_percent(interval=1))  # blocks 1 s -> one sample per second


def every(seconds, stop, fn):
    nxt = time.monotonic()
    while not stop.is_set():
        fn()
        nxt += seconds
        stop.wait(max(0, nxt - time.monotonic()))


def sample_top(out):
    ts, text = now(), kubectl("top", "pods", "-n", NS, "--no-headers")
    if text is None:
        print(f"{ts} kubectl top failed (metrics-server not ready yet?)", file=sys.stderr)
        return
    for line in text.splitlines():
        p = line.split()
        if len(p) >= 3:
            try:
                out.row(ts, p[0], round(to_millicores(p[1]), 1), round(to_mib(p[2]), 1))
            except ValueError:
                pass


def sample_hpa(out):
    ts = now()
    try:
        h = json.loads(kubectl("get", "hpa", "emi-web", "-n", NS, "-o", "json"))["status"]
        d = json.loads(kubectl("get", "deploy", "emi-web", "-n", NS, "-o", "json"))["status"]
    except (TypeError, ValueError, KeyError):
        return  # HPA or deployment not readable right now
    util = next((m["resource"]["current"].get("averageUtilization", "")
                 for m in h.get("currentMetrics", [])
                 if m.get("type") == "Resource" and m["resource"]["name"] == "cpu"), "")
    out.row(ts, h.get("currentReplicas", ""), h.get("desiredReplicas", ""), d.get("readyReplicas", 0), util)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder, e.g. results/run_r3_rate100")
    ap.add_argument("--interval", type=float, default=15, help="kubectl sampling period in seconds")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    ap.add_argument("--hpa", action="store_true", help="also record HPA / replica counts")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    jobs = [(host_loop, (Csv(out / "host_cpu.csv", ["timestamp", "host_cpu_percent"]), stop)),
            (every, (args.interval, stop, lambda o=Csv(out / "pods.csv", ["timestamp", "pod", "cpu_millicores", "memory_mib"]): sample_top(o)))]
    if args.hpa:
        hpa_csv = Csv(out / "hpa.csv", ["timestamp", "current_replicas", "desired_replicas", "ready_replicas", "cpu_util_percent"])
        jobs.append((every, (args.interval, stop, lambda: sample_hpa(hpa_csv))))
    threads = [threading.Thread(target=fn, args=a, daemon=True) for fn, a in jobs]
    for t in threads:
        t.start()
    print(f"{now()} monitoring -> {out}  (Ctrl+C to stop)")
    stop.wait(args.duration)
    stop.set()
    for t in threads:
        t.join(timeout=25)


if __name__ == "__main__":
    main()
