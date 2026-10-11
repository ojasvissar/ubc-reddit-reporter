import json
import os
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, Any, List

from common import (
    CLASSIFIED_PREFIX,
    RAW_PREFIX,
    basename,
    extract_json_object,
    invoke_model,
    list_keys,
    object_exists,
    read_parquet,
    render_prompt,
    write_parquet,
)

# ================== Logging Setup ==================
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ================== Config =========================
bucket_name = os.environ.get("BUCKET_NAME")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
PROMPT_FILE = os.environ.get("PROMPT_FILE", "classify_post.jinja")
CLASSIFY_CONCURRENCY = int(os.environ.get("CLASSIFY_CONCURRENCY", "8"))
# Classification only needs the gist of a post; keep prompts well inside small-context models
MAX_POST_CHARS = 6000

# Must match the list in prompts/classify_post.jinja
CATEGORIES = [
    "Computer Science",
    "Math and Statistics",
    "General Sciences",
    "Arts and Humanities",
    "Business and Econ",
    "General Academics",
    "Advice and Tips",
    "Social",
    "Rants",
    "Mental Health and Wellbeing",
    "Housing and Residence",
    "Campus Spaces",
    "Career",
    "Admin and Logistics",
]
UNKNOWN_CATEGORY = "unknown"


# ================== Classification =================
def normalize_category(model_output: str) -> str:
    """Map raw model output onto one of CATEGORIES (or 'unknown')."""
    parsed = extract_json_object(model_output)
    candidate = str(parsed.get("category", "")) if parsed else model_output
    candidate = candidate.strip().strip('"').strip().lower()

    for category in CATEGORIES:
        if candidate == category.lower():
            return category
    # Models sometimes add a preamble or punctuation; fall back to a substring match
    lowered = model_output.lower()
    for category in CATEGORIES:
        if category.lower() in lowered:
            return category
    return UNKNOWN_CATEGORY


def classify_text(content: str, model_id: str = BEDROCK_MODEL_ID) -> str:
    """Classify a single post's text into a category name."""
    prompt = render_prompt(PROMPT_FILE, content=content[:MAX_POST_CHARS])
    output = invoke_model(prompt, model_id, max_tokens=100)
    return normalize_category(output)


def _classify_or_unknown(content: str) -> str:
    if not content:
        return UNKNOWN_CATEGORY
    try:
        return classify_text(content)
    except Exception as e:
        logger.error(f"Classification failed, marking post as unknown: {e}")
        return UNKNOWN_CATEGORY


def classify_many(texts: List[str]) -> List[str]:
    with ThreadPoolExecutor(max_workers=CLASSIFY_CONCURRENCY) as pool:
        return list(pool.map(_classify_or_unknown, texts))


def classification_key_for(raw_key: str) -> str:
    return f"{CLASSIFIED_PREFIX}{basename(raw_key)}"


def classify_file(raw_key: str) -> int:
    """Classify every post in a raw parquet file and write it (with a 'category' column) to S3."""
    df = read_parquet(bucket_name, raw_key)
    if df.empty:
        logger.warning(f"File {raw_key} is empty, skipping")
        return 0

    text_columns = [col for col in ["Title", "Post_Text"] if col in df.columns]
    if not text_columns:
        logger.warning(f"No text columns found in {raw_key}, skipping")
        return 0

    combined_texts = df[text_columns].fillna("").astype(str).agg(". ".join, axis=1).str.strip(" .")
    df["category"] = classify_many(combined_texts.tolist())
    write_parquet(df, bucket_name, classification_key_for(raw_key))
    logger.info(f"Category counts for {raw_key}: {df['category'].value_counts().to_dict()}")
    return len(df)


# ================== Lambda Handler =================
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Classify Reddit posts stored by the fetcher using a Bedrock model.

    By default every raw file that has not been classified yet is processed.
    Optional event fields:
      key (str): classify only this raw parquet key
      force (bool): re-classify files that already have results
    """
    try:
        logger.info(f"Event received: {json.dumps(event)}")
        if not bucket_name:
            raise ValueError("BUCKET_NAME environment variable not set")

        force = bool(event.get("force", False))
        if event.get("key"):
            raw_keys = [event["key"]]
        else:
            raw_keys = [obj["Key"] for obj in list_keys(bucket_name, RAW_PREFIX, ".parquet")]

        results, skipped, failed = [], [], []
        for raw_key in raw_keys:
            out_key = classification_key_for(raw_key)
            if not force and object_exists(bucket_name, out_key):
                skipped.append(raw_key)
                continue
            try:
                if classify_file(raw_key):
                    results.append(out_key)
            except Exception as e:
                logger.error(f"Error processing file {raw_key}: {str(e)}", exc_info=True)
                failed.append(raw_key)

        if not results and not skipped and not failed:
            logger.warning("No parquet files found in S3")

        return {
            "statusCode": 500 if failed else 200,
            "body": json.dumps({
                "status": "error" if failed else "success",
                "processed_files": len(results),
                "s3_keys": results,
                "skipped_already_classified": skipped,
                "failed": failed,
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
