import json
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

import pandas as pd
import praw

from common import RAW_PREFIX, write_parquet

# ============ Logging Setup ============
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ============ Config ============
bucket_name = os.environ.get("BUCKET_NAME")

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "ubc-reddit-reporter")
# Comma-separated list, e.g. "UBC,UBCO"
REDDIT_SUBREDDITS = [s.strip() for s in os.environ.get("REDDIT_SUBREDDITS", "UBC").split(",") if s.strip()]


# ============ Core Functions ============

def fetch_reddit_posts(subreddit_name: str, days_back: int = 7) -> pd.DataFrame:
    """
    Fetch recent Reddit posts (and their loaded comments) from a subreddit.
    Returns a DataFrame.
    """
    if not all([REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT]):
        raise ValueError("Missing Reddit credentials in environment variables.")

    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT
    )

    subreddit = reddit.subreddit(subreddit_name)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    posts_dict = {
        "Title": [],
        "Post_Text": [],
        "Post_URL": [],
        "Comments": [],
        "Created_UTC": [],
        "Subreddit": [],
    }

    logger.info(f"Fetching posts from r/{subreddit_name} for last {days_back} days")
    for post in subreddit.new(limit=1000):
        post_time = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
        if post_time < cutoff:
            break

        comments = []
        try:
            # Only use comments returned with the post; expanding every
            # "load more comments" link costs one API call each and can
            # exhaust the Lambda timeout on busy weeks.
            post.comment_sort = "top"
            post.comments.replace_more(limit=0)
            comments = [c.body for c in post.comments.list()]
        except Exception as e:
            logger.warning(f"Could not load comments for post {post.id}: {e}")

        posts_dict["Title"].append(post.title)
        posts_dict["Post_Text"].append(post.selftext)
        posts_dict["Post_URL"].append(f"https://www.reddit.com{post.permalink}")
        posts_dict["Comments"].append(comments)
        posts_dict["Created_UTC"].append(post_time.isoformat())
        posts_dict["Subreddit"].append(subreddit_name)

    logger.info(f"Fetched {len(posts_dict['Title'])} posts from r/{subreddit_name}")
    return pd.DataFrame(posts_dict)


def raw_key_for(subreddit_name: str, days_back: int, now: datetime) -> str:
    old_date = (now - timedelta(days=days_back)).strftime('%Y_%m_%d')
    today_date = now.strftime('%Y_%m_%d')
    return f"{RAW_PREFIX}{subreddit_name}_{today_date}_{old_date}.parquet"


def store_parquet_in_s3(df: pd.DataFrame, subreddit_name: str, days_back: int) -> Optional[str]:
    """
    Convert DataFrame to Parquet and upload to S3.
    """
    if df.empty:
        logger.warning(f"No posts found for r/{subreddit_name}, skipping S3 upload.")
        return None

    key = raw_key_for(subreddit_name, days_back, datetime.now(timezone.utc))
    write_parquet(df, bucket_name, key)
    return key


# ============ Lambda Handler ============

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler to fetch Reddit posts from the configured subreddits
    and store them as Parquet files in S3.

    Optional event fields:
      days_back (int): how many days of posts to fetch (default 7)
    """
    logger.info(f"Event received: {json.dumps(event)}")

    try:
        if not bucket_name:
            raise ValueError("BUCKET_NAME environment variable is not set")

        days_back = int(event.get("days_back", 7))
        stored_files = []

        for subreddit_name in REDDIT_SUBREDDITS:
            df = fetch_reddit_posts(subreddit_name, days_back)
            key = store_parquet_in_s3(df, subreddit_name, days_back)
            if key:
                stored_files.append(key)

        result = {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bucket": bucket_name,
            "subreddits": REDDIT_SUBREDDITS,
            "files_stored": stored_files,
            "total_files": len(stored_files)
        }

        logger.info(f"Successfully stored {len(stored_files)} files to S3.")
        return {"statusCode": 200, "body": json.dumps(result, indent=2)}

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
