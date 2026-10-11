"""End-to-end: fetch -> classify -> summarize -> email, with Reddit, Bedrock, S3 and Postmark faked."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import analyzer
import api
import emailer
import reddit_fetcher
import summarizer

POSTS = [
    ("CPSC 110 midterm was brutal", "Recursion everywhere", ["same", "rip", "practice exams helped"]),
    ("Looking for a sublet in Kits", "Rent is wild", ["try the AMS housing board"]),
    ("U-Pass not renewing", "Compass card issue", []),
]


def _reddit():
    posts = []
    for i, (title, text, comments) in enumerate(POSTS):
        tree = MagicMock()
        tree.list.return_value = [SimpleNamespace(body=c) for c in comments]
        posts.append(SimpleNamespace(
            id=str(i), title=title, selftext=text, permalink=f"/r/UBC/comments/{i}/", comments=tree,
            created_utc=(datetime.now(timezone.utc) - timedelta(hours=i + 1)).timestamp(),
        ))
    reddit = MagicMock()
    reddit.subreddit.return_value.new.return_value = iter(posts)
    return reddit


def _bedrock_reply(prompt, model_id):
    if "text classification model" in prompt:
        if "CPSC" in prompt:
            return '{"category": "Computer Science"}'
        if "sublet" in prompt:
            return '{"category": "Housing and Residence"}'
        return '{"category": "Admin and Logistics"}'
    topic = prompt.split("'")[1]
    return f"Summary of {topic}."


def test_full_pipeline(s3, bedrock, monkeypatch):
    monkeypatch.setattr(reddit_fetcher.praw, "Reddit", MagicMock(return_value=_reddit()))
    monkeypatch.setattr(reddit_fetcher, "REDDIT_CLIENT_ID", "id")
    monkeypatch.setattr(reddit_fetcher, "REDDIT_CLIENT_SECRET", "secret")
    monkeypatch.setattr(emailer, "POSTMARK_SERVER_TOKEN", "token")
    post = Mock(return_value=Mock(status_code=200, json=lambda: {"MessageID": "1"}))
    monkeypatch.setattr(emailer.requests, "post", post)
    bedrock.reply = _bedrock_reply

    for handler in (reddit_fetcher, analyzer, summarizer, emailer):
        response = handler.lambda_handler({}, None)
        assert response["statusCode"] == 200, (handler.__name__, response["body"])

    email_body = post.call_args.kwargs["json"]["TemplateModel"]["body"]
    assert "Computer Science (1 posts)" in email_body
    assert "Summary of Housing and Residence." in email_body

    report = json.loads(api.lambda_handler({"rawPath": "/api/reports/latest"}, None)["body"])
    assert report["total_posts"] == 3
    assert report["subreddit"] == "UBC"
    cs = report["categories"]["Computer Science"]
    assert cs["top_posts"][0] == {
        "title": "CPSC 110 midterm was brutal",
        "url": "https://www.reddit.com/r/UBC/comments/0/",
        "comments": 3,
    }
