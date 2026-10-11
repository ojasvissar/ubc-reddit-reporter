import json
from unittest.mock import Mock

import pytest

import common
import emailer


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(emailer, "POSTMARK_SERVER_TOKEN", "test-token")


def test_sends_latest_summary(s3, token, monkeypatch):
    common.write_text("Weekly summary", "test-bucket", common.LATEST_SUMMARY_KEY)
    post = Mock(return_value=Mock(status_code=200, json=lambda: {"MessageID": "1"}))
    monkeypatch.setattr(emailer.requests, "post", post)

    response = emailer.lambda_handler({}, None)

    assert response["statusCode"] == 200
    payload = post.call_args.kwargs["json"]
    assert payload["TemplateModel"]["body"] == "Weekly summary"
    assert post.call_args.kwargs["headers"]["X-Postmark-Server-Token"] == "test-token"
    assert post.call_args.kwargs["timeout"]


def test_missing_summary_returns_404(s3, token):
    assert emailer.lambda_handler({}, None)["statusCode"] == 404


def test_missing_token_is_an_error(s3, monkeypatch):
    monkeypatch.setattr(emailer, "POSTMARK_SERVER_TOKEN", None)
    response = emailer.lambda_handler({}, None)
    assert response["statusCode"] == 500
    assert "POSTMARK_SERVER_TOKEN" in json.loads(response["body"])["message"]
