"""Shared helpers for the Lambda functions: S3 I/O, prompt rendering and Bedrock calls."""
import io
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from jinja2 import Environment, FileSystemLoader, StrictUndefined

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ================== S3 Layout =====================
RAW_PREFIX = "reddit_parquet/"
CLASSIFIED_PREFIX = "classifications/"
SUMMARY_PREFIX = "summaries/"
LATEST_SUMMARY_KEY = f"{SUMMARY_PREFIX}all_categories_summary.txt"

# ================== AWS Clients ===================
s3_client = boto3.client("s3")
bedrock_client = boto3.client(
    "bedrock-runtime",
    region_name=os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-west-2")),
    # Adaptive retries absorb throttling when many posts are classified concurrently
    config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=300),
)

# ================== Prompts =======================
PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
_jinja_env = Environment(loader=FileSystemLoader(PROMPTS_DIR), undefined=StrictUndefined)


def render_prompt(template_name: str, **variables: Any) -> str:
    """Render a Jinja2 template from src/prompts/."""
    return _jinja_env.get_template(template_name).render(**variables)


# ================== Bedrock =======================
def invoke_model(prompt: str, model_id: str, max_tokens: int = 1000) -> str:
    """
    Send a single-turn prompt to a Bedrock model and return the text reply.
    Uses the Converse API, which works across Claude, Llama, Titan, etc.
    and accepts both model IDs and inference profile IDs (e.g. us.anthropic.*).
    """
    response = bedrock_client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens},
    )
    content = response["output"]["message"]["content"]
    return "".join(block.get("text", "") for block in content).strip()


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Return the first flat JSON object found in model output, if any."""
    for match in re.finditer(r"\{[^{}]*\}", text):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


# ================== S3 Utilities ==================
def list_keys(bucket: str, prefix: str, suffix: str = "") -> List[Dict[str, Any]]:
    """List objects under a prefix (with optional suffix filter), oldest first."""
    objects = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(suffix):
                objects.append(obj)
    return sorted(objects, key=lambda o: o["LastModified"])


def object_exists(bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def read_parquet(bucket: str, key: str):
    import pandas as pd  # provided by the AWSSDKPandas layer

    response = s3_client.get_object(Bucket=bucket, Key=key)
    return pd.read_parquet(io.BytesIO(response["Body"].read()), engine="pyarrow")


def write_parquet(df, bucket: str, key: str) -> None:
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    s3_client.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(),
                         ContentType="application/octet-stream")
    logger.info(f"Uploaded {len(df)} rows to s3://{bucket}/{key}")


def write_text(text: str, bucket: str, key: str, content_type: str = "text/plain; charset=utf-8") -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType=content_type)
    logger.info(f"Uploaded s3://{bucket}/{key}")


def read_text(bucket: str, key: str) -> Optional[str]:
    """Read a UTF-8 object, returning None if it does not exist."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise
    return response["Body"].read().decode("utf-8")


def basename(key: str) -> str:
    return key.rsplit("/", 1)[-1]
