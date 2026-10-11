import json
import os
import logging
import re
from typing import Dict, Any, Optional

from common import (
    CLASSIFIED_PREFIX,
    RAW_PREFIX,
    SUMMARY_PREFIX,
    basename,
    list_keys,
    read_text,
)

# ================== Logging Setup ==================
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ================== Config =========================
bucket_name = os.environ.get("BUCKET_NAME")
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
REPORT_ID = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")

PIPELINE_STEPS = [
    ("fetch", "Reddit Fetcher", RAW_PREFIX, ".parquet"),
    ("classify", "Analyzer", CLASSIFIED_PREFIX, ".parquet"),
    ("summarize", "Summarizer", SUMMARY_PREFIX, ".json"),
]


# ================== Responses ======================
def _response(status: int, body: str, content_type: str, cache: str = "no-cache") -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type, "Cache-Control": cache},
        "body": body,
    }


def _json(status: int, payload: Any) -> Dict[str, Any]:
    return _response(status, json.dumps(payload), "application/json")


def _error(status: int, message: str) -> Dict[str, Any]:
    return _json(status, {"error": message})


# ================== Routes =========================
def index_page() -> Dict[str, Any]:
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        return _response(200, f.read(), "text/html; charset=utf-8", cache="max-age=60")


def list_reports() -> Dict[str, Any]:
    objects = list_keys(bucket_name, SUMMARY_PREFIX, ".json")
    reports = [
        {"id": basename(obj["Key"]).removesuffix(".json"), "last_modified": obj["LastModified"].isoformat()}
        for obj in reversed(objects)
    ]
    return _json(200, {"reports": reports})


def get_report(report_id: Optional[str]) -> Dict[str, Any]:
    if report_id is None:
        objects = list_keys(bucket_name, SUMMARY_PREFIX, ".json")
        if not objects:
            return _error(404, "No reports yet. Run the fetcher, analyzer and summarizer first.")
        report_id = basename(objects[-1]["Key"]).removesuffix(".json")
    elif not REPORT_ID.match(report_id):
        return _error(400, "Invalid report id")

    text = read_text(bucket_name, f"{SUMMARY_PREFIX}{report_id}.json")
    if text is None:
        return _error(404, f"Report {report_id} not found")
    report = json.loads(text)
    report.setdefault("id", report_id)
    return _json(200, report)


def pipeline_status() -> Dict[str, Any]:
    steps = []
    for step_id, name, prefix, suffix in PIPELINE_STEPS:
        objects = list_keys(bucket_name, prefix, suffix)
        latest = objects[-1] if objects else None
        steps.append({
            "id": step_id,
            "name": name,
            "prefix": prefix,
            "files": len(objects),
            "latest_key": latest["Key"] if latest else None,
            "last_run": latest["LastModified"].isoformat() if latest else None,
        })
    return _json(200, {"steps": steps})


# ================== Lambda Handler =================
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Read-only HTTP API (API Gateway HTTP API, payload v2) behind the dashboard.

      GET /                      dashboard page
      GET /api/reports           report ids, newest first
      GET /api/reports/latest    most recent report
      GET /api/reports/{id}      a specific report
      GET /api/status            last output of each pipeline step
    """
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = (event.get("rawPath") or "/").rstrip("/") or "/"

    try:
        if method != "GET":
            return _error(405, "Method not allowed")
        if path in ("/", "/index.html"):
            return index_page()
        if not bucket_name:
            raise ValueError("BUCKET_NAME environment variable not set")
        if path == "/api/reports":
            return list_reports()
        if path == "/api/reports/latest":
            return get_report(None)
        if path.startswith("/api/reports/"):
            return get_report(path[len("/api/reports/"):])
        if path == "/api/status":
            return pipeline_status()
        return _error(404, "Not found")
    except Exception as e:
        logger.error(f"Error handling {method} {path}: {e}", exc_info=True)
        return _error(500, "Internal error")
