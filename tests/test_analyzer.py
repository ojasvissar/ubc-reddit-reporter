import json

import pandas as pd
import pytest

import analyzer
import common


@pytest.mark.parametrize("output,expected", [
    ('{"category": "Computer Science"}', "Computer Science"),
    ('Here you go:\n{"category": "housing and residence"}', "Housing and Residence"),
    ("Category: Career", "Career"),
    ('{"category": "Underwater Basket Weaving"}', "unknown"),
    ("", "unknown"),
])
def test_normalize_category(output, expected):
    assert analyzer.normalize_category(output) == expected


def test_categories_match_classify_prompt():
    prompt = common.render_prompt("classify_post.jinja", content="")
    for category in analyzer.CATEGORIES:
        assert f"\n{category}\n" in prompt


def _raw_df():
    return pd.DataFrame({
        "Title": ["CPSC 110 midterm", "Looking for a roommate"],
        "Post_Text": ["Racket is hard", None],
        "Post_URL": ["u1", "u2"],
        "Comments": [["same"], []],
        "Created_UTC": ["2026-10-01T00:00:00+00:00"] * 2,
        "Subreddit": ["UBC", "UBC"],
    })


def _reply(prompt, model_id):
    return '{"category": "Computer Science"}' if "CPSC" in prompt else '{"category": "Housing and Residence"}'


def test_handler_classifies_new_files_and_keeps_columns(s3, bedrock):
    bedrock.reply = _reply
    common.write_parquet(_raw_df(), "test-bucket", "reddit_parquet/UBC_2026_10_08_2026_10_01.parquet")

    response = analyzer.lambda_handler({}, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["s3_keys"] == ["classifications/UBC_2026_10_08_2026_10_01.parquet"]
    out = common.read_parquet("test-bucket", body["s3_keys"][0])
    assert list(out["category"]) == ["Computer Science", "Housing and Residence"]
    assert {"Title", "Post_Text", "Comments", "Post_URL"} <= set(out.columns)


def test_handler_skips_already_classified(s3, bedrock):
    common.write_parquet(_raw_df(), "test-bucket", "reddit_parquet/a.parquet")
    common.write_parquet(_raw_df(), "test-bucket", "classifications/a.parquet")

    body = json.loads(analyzer.lambda_handler({}, None)["body"])

    assert body["processed_files"] == 0
    assert body["skipped_already_classified"] == ["reddit_parquet/a.parquet"]
    assert bedrock.prompts == []


def test_bedrock_failure_marks_post_unknown(s3, bedrock):
    def boom(prompt, model_id):
        raise RuntimeError("throttled")
    bedrock.reply = boom
    common.write_parquet(_raw_df(), "test-bucket", "reddit_parquet/a.parquet")

    analyzer.lambda_handler({}, None)

    out = common.read_parquet("test-bucket", "classifications/a.parquet")
    assert set(out["category"]) == {"unknown"}
