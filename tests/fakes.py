"""In-memory fakes for S3 and Bedrock, shared by the tests and scripts/dev_server.py."""
import io
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError


class FakeS3:
    """Minimal in-memory stand-in for the S3 calls the functions make."""

    def __init__(self):
        self.objects = {}
        self._start = datetime.now(timezone.utc)
        self._clock = 0

    def seed(self, key, body, last_modified):
        """Store an object with an explicit LastModified (used by the dev server)."""
        self.objects[key] = (body.encode("utf-8") if isinstance(body, str) else body, last_modified)

    def put_object(self, Bucket, Key, Body, **kwargs):
        if hasattr(Body, "read"):
            Body = Body.read()
        # Strictly increasing timestamps so "latest object" lookups are deterministic
        self._clock += 1
        self.objects[Key] = (Body, self._start + timedelta(milliseconds=self._clock))
        return {}

    def _missing(self, op):
        return ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, op)

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self._missing("GetObject")
        return {"Body": io.BytesIO(self.objects[Key][0])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {}

    def get_paginator(self, name):
        fake = self

        class Paginator:
            def paginate(self, Bucket, Prefix):
                contents = [{"Key": k, "LastModified": v[1]}
                            for k, v in fake.objects.items() if k.startswith(Prefix)]
                yield {"Contents": contents} if contents else {}

        return Paginator()


class FakeBedrock:
    """Returns replies from a callable(prompt, model_id) and records prompts."""

    def __init__(self, reply=lambda prompt, model_id: "ok"):
        self.reply = reply
        self.prompts = []

    def converse(self, modelId, messages, inferenceConfig):
        prompt = messages[0]["content"][0]["text"]
        self.prompts.append(prompt)
        return {"output": {"message": {"role": "assistant", "content": [{"text": self.reply(prompt, modelId)}]}}}
