import json
import os
import logging
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd

from common import (
    CLASSIFIED_PREFIX,
    LATEST_SUMMARY_KEY,
    SUMMARY_PREFIX,
    basename,
    invoke_model,
    list_keys,
    read_parquet,
    render_prompt,
    write_text,
)

# ================== Logging ==================
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ================== Config ==================
bucket_name = os.environ.get("BUCKET_NAME")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MAX_POST_CHARS = 3000
# Upper bound on the posts text sent per category; lower it for small-context models
MAX_PROMPT_CHARS = int(os.environ.get("MAX_PROMPT_CHARS", "400000"))
TOP_POSTS_PER_CATEGORY = 5

# Category (as produced by the analyzer) -> summary prompt in src/prompts/
CATEGORY_PROMPTS = {
    "Computer Science": "Computer_Science.jinja",
    "Math and Statistics": "Math_and_Statistics.jinja",
    "General Sciences": "General_Sciences.jinja",
    "Arts and Humanities": "Arts_and_Humanities.jinja",
    "Business and Econ": "Business_and_Econ.jinja",
    "General Academics": "General_Academics.jinja",
    "Advice and Tips": "Advice_and_Tips.jinja",
    "Social": "Social_Events.jinja",
    "Rants": "Rants_and_Complaints.jinja",
    "Mental Health and Wellbeing": "Mental_Health_and_Wellbeing.jinja",
    "Housing and Residence": "Housing_and_Residence.jinja",
    "Campus Spaces": "Campus_Spaces.jinja",
    "Career": "Career.jinja",
    "Admin and Logistics": "Admin_and_Logistics.jinja",
}


# ================== Formatting ==================
def format_comments(comments) -> str:
    if comments is None or (isinstance(comments, float) and np.isnan(comments)):
        return "No comments"
    if isinstance(comments, np.ndarray):
        comments = comments.tolist()
    if isinstance(comments, list):
        if len(comments) == 0:
            return "No comments"
        formatted = []
        for comment in comments[:10]:
            if isinstance(comment, dict):
                comment_text = comment.get("text", comment.get("body", str(comment)))
            else:
                comment_text = str(comment)
            comment_text = comment_text.strip()
            if len(comment_text) > 200:
                comment_text = comment_text[:200] + "..."
            formatted.append(f"  - {comment_text}")
        if len(comments) > 10:
            formatted.append(f"  ... and {len(comments) - 10} more comments")
        return "\n".join(formatted)
    return str(comments)[:500]


def format_posts_for_prompt(posts: pd.DataFrame) -> str:
    """Render a category's posts as numbered blocks, stopping at MAX_PROMPT_CHARS."""
    blocks = []
    total_chars = 0
    for _, row in posts.iterrows():
        title = row.get("Title")
        title = title if isinstance(title, str) and title.strip() else "No title"
        content = row.get("Post_Text")
        content = content[:MAX_POST_CHARS] if isinstance(content, str) and content.strip() else "No content"
        block = (
            f"Post {len(blocks) + 1}:\n"
            f"Title: {title}\n"
            f"Content: {content}\n"
            f"Comments:\n{format_comments(row.get('Comments'))}"
        )
        if blocks and total_chars + len(block) > MAX_PROMPT_CHARS:
            logger.warning(f"Prompt limit reached; summarizing {len(blocks)} of {len(posts)} posts")
            break
        blocks.append(block)
        total_chars += len(block)
    return "\n\n---\n\n".join(blocks)


def summarize_category(posts: pd.DataFrame, prompt_file: str, model_id: str = BEDROCK_MODEL_ID) -> str:
    prompt = render_prompt(prompt_file, posts=format_posts_for_prompt(posts))
    return invoke_model(prompt, model_id, max_tokens=2000)


def build_report(summaries: Dict[str, Dict[str, Any]], source_key: str) -> str:
    total_posts = sum(s["post_count"] for s in summaries.values())
    lines = [
        "UBC Reddit Weekly Summary",
        f"Source: {basename(source_key)} ({total_posts} categorized posts)",
        "",
    ]
    for category, entry in summaries.items():
        lines.append(f"{category} ({entry['post_count']} posts)")
        lines.append(entry["summary"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _comment_count(comments) -> int:
    if isinstance(comments, (list, np.ndarray)):
        return len(comments)
    return 0


def top_posts(posts: pd.DataFrame, limit: int = TOP_POSTS_PER_CATEGORY) -> List[Dict[str, Any]]:
    """The most-discussed posts in a category, for linking from the report."""
    ranked = posts.assign(_comments=posts.get("Comments", pd.Series(dtype=object)).map(_comment_count))
    ranked = ranked.sort_values("_comments", ascending=False, kind="stable").head(limit)
    return [
        {
            "title": str(row.get("Title") or "Untitled"),
            "url": row.get("Post_URL") if isinstance(row.get("Post_URL"), str) else None,
            "comments": int(row["_comments"]),
        }
        for _, row in ranked.iterrows()
    ]


def parse_run_name(run_name: str) -> Dict[str, Optional[str]]:
    """'UBC_2026_10_11_2026_10_04' -> subreddit and ISO period dates."""
    match = re.match(r"^(.+)_(\d{4})_(\d{2})_(\d{2})_(\d{4})_(\d{2})_(\d{2})$", run_name)
    if not match:
        return {"subreddit": None, "period_start": None, "period_end": None}
    g = match.groups()
    return {
        "subreddit": g[0],
        "period_end": f"{g[1]}-{g[2]}-{g[3]}",
        "period_start": f"{g[4]}-{g[5]}-{g[6]}",
    }


# ================== Main Processing ==================
def latest_classification_key() -> Optional[str]:
    objects = list_keys(bucket_name, CLASSIFIED_PREFIX, ".parquet")
    return objects[-1]["Key"] if objects else None


def summarize_file(classified_key: str) -> Dict[str, Any]:
    df = read_parquet(bucket_name, classified_key)
    if "category" not in df.columns:
        raise ValueError(f"{classified_key} has no 'category' column; run the analyzer first")

    summaries: Dict[str, Dict[str, Any]] = {}
    failed = []
    for category, prompt_file in CATEGORY_PROMPTS.items():
        posts = df[df["category"] == category]
        if posts.empty:
            continue
        try:
            summary = summarize_category(posts, prompt_file)
        except Exception as e:
            logger.error(f"Failed to summarize category {category}: {e}", exc_info=True)
            failed.append(category)
            continue
        summaries[category] = {
            "post_count": int(len(posts)),
            "summary": summary,
            "top_posts": top_posts(posts),
        }

    if not summaries:
        raise RuntimeError(f"No categories could be summarized from {classified_key} (failed: {failed})")

    report = build_report(summaries, classified_key)
    run_name = basename(classified_key).removesuffix(".parquet")
    json_key = f"{SUMMARY_PREFIX}{run_name}.json"
    text_key = f"{SUMMARY_PREFIX}{run_name}.txt"

    report_json = {
        "id": run_name,
        "source": classified_key,
        **parse_run_name(run_name),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": BEDROCK_MODEL_ID,
        "total_posts": int(len(df)),
        "unclassified_posts": int((~df["category"].isin(list(CATEGORY_PROMPTS))).sum()),
        "categories": summaries,
    }
    write_text(json.dumps(report_json, indent=2), bucket_name, json_key, content_type="application/json")
    write_text(report, bucket_name, text_key)
    # The emailer always sends the most recent report
    write_text(report, bucket_name, LATEST_SUMMARY_KEY)

    return {
        "source": classified_key,
        "categories_summarized": list(summaries),
        "categories_failed": failed,
        "s3_keys": [json_key, text_key, LATEST_SUMMARY_KEY],
    }


# ================== Lambda Handler =================
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Summarize classified posts per category using a Bedrock model.

    Optional event fields:
      key (str): classified parquet key to summarize (default: most recent one)
    """
    try:
        logger.info(f"Event received: {json.dumps(event)}")
        if not bucket_name:
            raise ValueError("BUCKET_NAME environment variable not set")

        classified_key = event.get("key") or latest_classification_key()
        if not classified_key:
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "status": "error",
                    "message": f"No classified files found under {CLASSIFIED_PREFIX}; run the analyzer first",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            }

        result = summarize_file(classified_key)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "status": "success",
                **result,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }, indent=2)
        }

    except Exception as e:
        logger.error(f"Error in lambda_handler: {str(e)}", exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({
                "status": "error",
                "message": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })
        }
