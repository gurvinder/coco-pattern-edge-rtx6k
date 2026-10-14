#!/usr/bin/env python3
"""Send only public canary data to the isolated model server; never exec in it."""
import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request


def test(base):
    if not base.startswith("http://") or base.endswith("/"):
        raise ValueError("use the isolated Pod IP URL without a trailing slash")
    health = json.loads(urllib.request.urlopen(base + "/health", timeout=10).read())
    assert health == {"status": "ready", "model": "qwen3-0.6b-luks", "device": "cpu"}
    body = {
        "model": health["model"],
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
        ],
        "max_tokens": 32,
        "temperature": 0,
    }

    def request(payload):
        return urllib.request.Request(
            base + "/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )

    response = json.loads(urllib.request.urlopen(request(body), timeout=90).read())
    assert response["test"]["device"] == "cpu"
    assert "Paris" in response["choices"][0]["message"]["content"]
    assert 0 < response["usage"]["completion_tokens"] <= body["max_tokens"]
    try:
        urllib.request.urlopen(
            request(dict(body, model="unapproved-model")), timeout=10
        )
        raise AssertionError("unapproved model accepted")
    except urllib.error.HTTPError as error:
        assert error.code == 400
    return {
        "health": health,
        "request": body,
        "response": response,
        "unapprovedModelRejected": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as stream:
        stream.write(json.dumps(test(args.base_url), indent=2) + "\n")
