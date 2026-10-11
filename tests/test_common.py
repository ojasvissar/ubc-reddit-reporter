import os

import pytest

import common
from summarizer import CATEGORY_PROMPTS


def test_extract_json_object_finds_json_in_chatter():
    assert common.extract_json_object('Sure! {"category": "Career"} Hope that helps') == {"category": "Career"}
    assert common.extract_json_object("no json here") is None


@pytest.mark.parametrize("template", sorted(set(CATEGORY_PROMPTS.values())))
def test_summary_prompts_include_posts(template):
    rendered = common.render_prompt(template, posts="POST_MARKER")
    assert "POST_MARKER" in rendered


def test_classify_prompt_includes_content():
    assert "CONTENT_MARKER" in common.render_prompt("classify_post.jinja", content="CONTENT_MARKER")


def test_every_prompt_file_is_used():
    files = {f for f in os.listdir(common.PROMPTS_DIR) if f.endswith(".jinja")}
    assert files == set(CATEGORY_PROMPTS.values()) | {"classify_post.jinja"}


def test_invoke_model_uses_converse(bedrock):
    bedrock.reply = lambda prompt, model_id: f"{model_id}:{prompt}"
    assert common.invoke_model("hello", "us.anthropic.test") == "us.anthropic.test:hello"
