"""Loan EMI Calculator API (Flask + MongoDB)."""
import math
import os
import socket
from datetime import datetime, timezone

import pymongo
from flask import Flask, jsonify, request
from pymongo import DESCENDING, MongoClient, ReadPreference
from pymongo.errors import PyMongoError

# Configuration comes from the environment only (no hard-coded hosts or passwords).
MONGO_URL = os.environ.get("MONGO_URL")
if not MONGO_URL:
    raise RuntimeError("MONGO_URL environment variable is required")

POD = os.environ.get("HOSTNAME", "unknown")  # Kubernetes sets HOSTNAME to the pod name


def _pod_ip():
    try:
        return os.environ.get("POD_IP") or socket.gethostbyname(socket.gethostname())
    except OSError:
        return "unknown"


POD_IP = _pod_ip()

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 4096  # requests are tiny; reject anything bigger

client = MongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=8000, connectTimeoutMS=3000)
calcs = client.get_default_database("emidb").calculations

# (field, maximum). Every field must be a finite number > 0 and <= maximum.
FIELDS = (("principal", 1e12), ("rate", 100), ("months", 600))


def parse(data):
    """Validate the request body and return (principal, annual_rate, months)."""
    if not hasattr(data, "get"):
        raise ValueError("Send a JSON object with principal, rate and months")
    values = []
    for name, limit in FIELDS:
        raw = data.get(name)
        if raw is None or isinstance(raw, (bool, list, dict)):
            raise ValueError(f"{name} is required and must be a number")
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"{name} must be a number") from None
        if not math.isfinite(v) or not 0 < v <= limit:
            raise ValueError(f"{name} must be greater than 0 and at most {limit:g}")
        values.append(v)
    if values[2] != int(values[2]):
        raise ValueError("months must be a whole number")
    return values[0], values[1], int(values[2])


def compute(principal, annual_rate, months):
    """EMI = P*r*(1+r)^n / ((1+r)^n - 1), written as P*r / (1-(1+r)^-n) so it stays accurate
    for tiny rates and cannot overflow."""
    r = annual_rate / 12 / 100
    emi = principal * r / -math.expm1(-months * math.log1p(r))
    total = emi * months
    return emi, total, total - principal


_indexed = False


def ensure_index():
    global _indexed
    if not _indexed:
        calcs.create_index([("createdAt", DESCENDING), ("_id", DESCENDING)])
        _indexed = True


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/emi")
def api_emi():
    try:
        principal, rate, months = parse(request.get_json(silent=True) or request.form)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    emi, total, interest = compute(principal, rate, months)
    doc = {
        "principal": principal,
        "rate": rate,
        "months": months,
        "emi": round(emi, 2),
        "servedBy": POD,
        "createdAt": datetime.now(timezone.utc),
    }
    try:
        ensure_index()
        calcs.insert_one(doc)
    except PyMongoError:
        app.logger.exception("MongoDB write failed")
        return jsonify(error="Database unavailable, please retry"), 503
    return jsonify(
        emi=round(emi, 2),
        totalPayment=round(total, 2),
        totalInterest=round(interest, 2),
        servedBy=POD,
        podIp=POD_IP,
    )


@app.get("/api/history")
def api_history():
    try:
        rows = list(calcs.find({}, {"_id": 0}).sort([("createdAt", DESCENDING), ("_id", DESCENDING)]).limit(10))
    except PyMongoError:
        app.logger.exception("MongoDB read failed")
        return jsonify(error="Database unavailable, please retry"), 503
    for row in rows:
        row["createdAt"] = row["createdAt"].isoformat(timespec="seconds")
    return jsonify(rows)


@app.get("/health")
def health():
    """200 only if MongoDB answers. PRIMARY_PREFERRED means a replica-set election (no primary
    for a few seconds) does not take every pod out of the Service; 2 s cap keeps probes fast."""
    try:
        with pymongo.timeout(2):
            client.admin.command("ping", read_preference=ReadPreference.PRIMARY_PREFERRED)
    except PyMongoError:
        return jsonify(status="mongodb unreachable"), 503
    return jsonify(status="ok")


if __name__ == "__main__":  # local development only; the container uses gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
