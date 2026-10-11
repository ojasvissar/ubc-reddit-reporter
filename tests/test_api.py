import json

import api
import common


def call(path, method="GET"):
    response = api.lambda_handler({"rawPath": path, "requestContext": {"http": {"method": method}}}, None)
    body = response["body"]
    return response["statusCode"], (json.loads(body) if "json" in response["headers"]["Content-Type"] else body)


def seed_reports():
    for run in ("UBC_2026_10_04_2026_09_27", "UBC_2026_10_11_2026_10_04"):
        report = {"id": run, "categories": {"Career": {"post_count": 1, "summary": run}}}
        common.write_text(json.dumps(report), "test-bucket", f"summaries/{run}.json")


def test_serves_dashboard_page(s3):
    status, body = call("/")
    assert status == 200
    assert "<title>UBC Reddit Reporter</title>" in body


def test_lists_reports_newest_first(s3):
    seed_reports()
    status, body = call("/api/reports")
    assert status == 200
    assert [r["id"] for r in body["reports"]] == ["UBC_2026_10_11_2026_10_04", "UBC_2026_10_04_2026_09_27"]


def test_latest_and_specific_report(s3):
    seed_reports()
    assert call("/api/reports/latest")[1]["id"] == "UBC_2026_10_11_2026_10_04"
    assert call("/api/reports/UBC_2026_10_04_2026_09_27")[1]["categories"]["Career"]["summary"] == "UBC_2026_10_04_2026_09_27"


def test_missing_and_invalid_reports(s3):
    assert call("/api/reports/latest")[0] == 404
    assert call("/api/reports/nope")[0] == 404
    assert call("/api/reports/..%2F..%2Fsecret")[0] == 400


def test_pipeline_status(s3):
    common.write_text("", "test-bucket", "reddit_parquet/a.parquet")
    status, body = call("/api/status")
    assert status == 200
    steps = {s["id"]: s for s in body["steps"]}
    assert steps["fetch"]["files"] == 1 and steps["fetch"]["last_run"]
    assert steps["summarize"]["last_run"] is None


def test_rejects_writes_and_unknown_routes(s3):
    assert call("/api/reports", method="POST")[0] == 405
    assert call("/api/nothing")[0] == 404
