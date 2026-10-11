import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import common
import reddit_fetcher


def _post(i, days_ago):
    comments = MagicMock()
    comments.list.return_value = [SimpleNamespace(body=f"comment {i}")]
    return SimpleNamespace(
        id=str(i),
        title=f"title {i}",
        selftext=f"text {i}",
        permalink=f"/r/UBC/comments/{i}/",
        comments=comments,
        created_utc=(datetime.now(timezone.utc) - timedelta(days=days_ago)).timestamp(),
    )


def test_handler_stores_recent_posts(s3, monkeypatch):
    posts = [_post(1, 1), _post(2, 3), _post(3, 10)]
    reddit = MagicMock()
    reddit.subreddit.return_value.new.return_value = iter(posts)
    monkeypatch.setattr(reddit_fetcher.praw, "Reddit", MagicMock(return_value=reddit))
    monkeypatch.setattr(reddit_fetcher, "REDDIT_CLIENT_ID", "id")
    monkeypatch.setattr(reddit_fetcher, "REDDIT_CLIENT_SECRET", "secret")

    response = reddit_fetcher.lambda_handler({"days_back": 7}, None)

    assert response["statusCode"] == 200
    key = json.loads(response["body"])["files_stored"][0]
    assert key.startswith(common.RAW_PREFIX + "UBC_")
    df = common.read_parquet("test-bucket", key)
    assert list(df["Title"]) == ["title 1", "title 2"]
    assert list(df["Comments"].iloc[0]) == ["comment 1"]
    assert df["Post_URL"].iloc[0] == "https://www.reddit.com/r/UBC/comments/1/"


def test_missing_credentials_returns_error(s3, monkeypatch):
    monkeypatch.setattr(reddit_fetcher, "REDDIT_CLIENT_ID", None)
    assert reddit_fetcher.lambda_handler({}, None)["statusCode"] == 500
