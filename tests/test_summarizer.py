import json

import numpy as np
import pandas as pd

import analyzer
import common
import summarizer


def test_every_category_has_a_prompt():
    assert set(summarizer.CATEGORY_PROMPTS) == set(analyzer.CATEGORIES)


def test_format_comments_handles_parquet_arrays():
    assert summarizer.format_comments(np.array(["nice"])) == "  - nice"
    assert summarizer.format_comments(np.array([])) == "No comments"
    assert summarizer.format_comments(None) == "No comments"


def test_handler_summarizes_latest_classification(s3, bedrock):
    bedrock.reply = lambda prompt, model_id: "Students discussed CPSC 110." if "Computer Science" in prompt else "Rooms."
    old = pd.DataFrame({"Title": ["old"], "Post_Text": ["x"], "Comments": [[]], "category": ["Career"]})
    new = pd.DataFrame({
        "Title": ["CPSC 110 midterm", "Roommate?", "???"],
        "Post_Text": ["Racket is hard", "", "x"],
        "Comments": [["same", "rip"], [], []],
        "category": ["Computer Science", "Housing and Residence", "unknown"],
    })
    common.write_parquet(old, "test-bucket", "classifications/old.parquet")
    common.write_parquet(new, "test-bucket", "classifications/new.parquet")

    response = summarizer.lambda_handler({}, None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["source"] == "classifications/new.parquet"
    assert body["categories_summarized"] == ["Computer Science", "Housing and Residence"]
    assert any("CPSC 110 midterm" in p and "same" in p for p in bedrock.prompts)

    report = common.read_text("test-bucket", common.LATEST_SUMMARY_KEY)
    assert "Computer Science (1 posts)" in report
    assert "Students discussed CPSC 110." in report
    assert common.read_text("test-bucket", "summaries/new.txt") == report

    saved = json.loads(common.read_text("test-bucket", "summaries/new.json"))
    assert saved["total_posts"] == 3 and saved["unclassified_posts"] == 1
    assert saved["categories"]["Computer Science"]["top_posts"][0]["comments"] == 2


def test_parse_run_name():
    assert summarizer.parse_run_name("UBC_2026_10_11_2026_10_04") == {
        "subreddit": "UBC", "period_start": "2026-10-04", "period_end": "2026-10-11"}
    assert summarizer.parse_run_name("whatever")["subreddit"] is None


def test_handler_without_classifications_returns_404(s3, bedrock):
    assert summarizer.lambda_handler({}, None)["statusCode"] == 404
