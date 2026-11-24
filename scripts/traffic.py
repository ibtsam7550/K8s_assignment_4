#!/usr/bin/env python3
"""Send POST /api/emi requests with random valid inputs and log which pod answered.

  scripts/traffic.py --label dist_r3 --count 30        # Milestone 3.2 / 3.3: requests per pod
  scripts/traffic.py --label rollout --seconds 120     # Milestone 3.4 / 3.5: keep a loop running

Writes results/traffic_<label>.csv and prints a per-pod table plus the number of failed requests
(any non-200 answer, timeout or connection error)."""
import argparse
import collections
import csv
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


def one(url, host):
    body = json.dumps({"principal": random.randint(10_000, 5_000_000),
                       "rate": round(random.uniform(1, 30), 2),
                       "months": random.randint(6, 360)}).encode()
    req = urllib.request.Request(url, body, {"Content-Type": "application/json", "Host": host})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r).get("servedBy", "?")
    except urllib.error.HTTPError as e:
        return e.code, "-"
    except Exception as e:  # timeout, connection refused, bad JSON ...
        return type(e).__name__, "-"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://emi.local/api/emi")
    ap.add_argument("--label", default="run")
    ap.add_argument("--count", type=int, default=30, help="number of requests (default 30)")
    ap.add_argument("--seconds", type=float, help="run for N seconds instead of --count requests")
    ap.add_argument("--delay", type=float, default=0.1, help="pause between requests (default 0.1 s)")
    args = ap.parse_args()
    # Look the name up once, IPv4 only: on macOS a normal lookup of *.local names can stall ~5 s per request.
    u = urllib.parse.urlsplit(args.url)
    ip = socket.gethostbyname(u.hostname)
    url = u._replace(netloc=ip + (f":{u.port}" if u.port else "")).geturl()

    rows, end = [], (time.time() + args.seconds if args.seconds else None)
    try:
        while (len(rows) < args.count) if end is None else (time.time() < end):
            ts = datetime.now().astimezone().isoformat(timespec="milliseconds")
            status, pod = one(url, u.netloc)
            rows.append((len(rows) + 1, ts, status, pod))
            print(f"{rows[-1][0]:5d}  {ts}  {status}  {pod}")
            time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nstopped by user")

    path = Path(__file__).resolve().parent.parent / "results" / f"traffic_{args.label}.csv"
    path.parent.mkdir(exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seq", "timestamp", "status", "pod"])
        w.writerows(rows)

    ok = collections.Counter(pod for _, _, status, pod in rows if status == 200)
    failed = sum(1 for r in rows if r[2] != 200)
    print(f"\n{'pod':<40}requests")
    for pod, n in sorted(ok.items()):
        print(f"{pod:<40}{n}")
    print(f"\ntotal={len(rows)}  ok={sum(ok.values())}  failed={failed}  -> {path}")


if __name__ == "__main__":
    main()
