"""
Run the dashboard locally through the same handler the API Lambda uses.

  python scripts/dev_server.py                   # sample data, no AWS needed
  python scripts/dev_server.py --bucket <name>   # live data from your deployed bucket
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")

SAMPLE_DIR = ROOT / "scripts" / "sample_data"


def use_sample_data(api, common):
    """Point the API at an in-memory bucket seeded with scripts/sample_data/."""
    from tests.fakes import FakeS3

    fake = FakeS3()
    for path in sorted(SAMPLE_DIR.glob("*.json")):
        report = json.loads(path.read_text())
        generated = datetime.fromisoformat(report["generated_at"])
        run = path.stem
        fake.seed(f"{common.RAW_PREFIX}{run}.parquet", b"", generated - timedelta(minutes=40))
        fake.seed(f"{common.CLASSIFIED_PREFIX}{run}.parquet", b"", generated - timedelta(minutes=12))
        fake.seed(f"{common.SUMMARY_PREFIX}{run}.json", path.read_text(), generated)
    common.s3_client = fake
    api.bucket_name = "sample-bucket"


def make_handler(api):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            event = {"rawPath": urlsplit(self.path).path, "requestContext": {"http": {"method": "GET"}}}
            response = api.lambda_handler(event, None)
            body = response["body"].encode("utf-8")
            self.send_response(response["statusCode"])
            for name, value in response.get("headers", {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bucket", help="read reports from this S3 bucket instead of sample data")
    args = parser.parse_args()

    if args.bucket:
        os.environ["BUCKET_NAME"] = args.bucket
    import api
    import common

    if args.bucket:
        source = f"s3://{args.bucket}"
    else:
        use_sample_data(api, common)
        source = "sample data"

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(api))
    print(f"Dashboard ({source}) at http://127.0.0.1:{args.port}  — Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
