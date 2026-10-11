import os
import sys

import pytest

# Lambda handlers live in src/ and import each other as top-level modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
os.environ.setdefault("BUCKET_NAME", "test-bucket")

import common  # noqa: E402
from tests.fakes import FakeBedrock, FakeS3  # noqa: E402


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(common, "s3_client", fake)
    return fake


@pytest.fixture
def bedrock(monkeypatch):
    fake = FakeBedrock()
    monkeypatch.setattr(common, "bedrock_client", fake)
    return fake
