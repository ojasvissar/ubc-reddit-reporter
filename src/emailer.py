import json
import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any

import requests

from common import LATEST_SUMMARY_KEY, read_text

# ================== Logging Setup ==================
logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# ================== Config ========================
bucket_name = os.environ.get("BUCKET_NAME")

# ================== Postmark Config ================
POSTMARK_SERVER_TOKEN = os.environ.get("POSTMARK_SERVER_TOKEN")
POSTMARK_API_URL = "https://api.postmarkapp.com/email/withTemplate"
FROM_EMAIL = os.environ.get("FROM_EMAIL", "sender@example.com")
TO_EMAIL = os.environ.get("TO_EMAIL", "recipient@example.com")
TEMPLATE_ALIAS = os.environ.get("TEMPLATE_ALIAS", "comment-notification")
SUBREDDIT_URL = f"https://www.reddit.com/r/{os.environ.get('REDDIT_SUBREDDITS', 'UBC').split(',')[0].strip()}"


def send_postmark_email(summary_text: str) -> Dict[str, Any]:
    """Send email using Postmark API."""
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": POSTMARK_SERVER_TOKEN
    }

    payload = {
        "From": FROM_EMAIL,
        "To": TO_EMAIL,
        "TemplateAlias": TEMPLATE_ALIAS,
        "TemplateModel": {
            "product_url": SUBREDDIT_URL,
            "message": summary_text,
            "body": summary_text,
            "commenter_name": "UBC Reddit Reporter",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            "action_url": SUBREDDIT_URL,
            "notifications_url": SUBREDDIT_URL
        }
    }

    try:
        response = requests.post(POSTMARK_API_URL, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        return {"status": "success", "response": response.json()}
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send email: {str(e)}")
        return {"status": "error", "message": str(e)}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler to send email with summary from S3.

    Optional event fields:
      key (str): S3 key of the summary text to send (default: latest summary)
    """
    try:
        logger.info(f"Event received: {json.dumps(event)}")

        if not bucket_name:
            raise ValueError("BUCKET_NAME environment variable not set")
        if not POSTMARK_SERVER_TOKEN:
            raise ValueError("POSTMARK_SERVER_TOKEN environment variable not set")

        # Read summary from S3
        summary_key = event.get("key") or LATEST_SUMMARY_KEY
        summary_text = read_text(bucket_name, summary_key)

        if not summary_text:
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "status": "error",
                    "message": f"Summary file s3://{bucket_name}/{summary_key} not found or empty",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            }

        # Send email
        email_result = send_postmark_email(summary_text)

        if email_result["status"] == "success":
            return {
                "statusCode": 200,
                "body": json.dumps({
                    "status": "success",
                    "message": "Email sent successfully",
                    "email_response": email_result["response"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            }
        else:
            return {
                "statusCode": 500,
                "body": json.dumps({
                    "status": "error",
                    "message": email_result["message"],
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
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
