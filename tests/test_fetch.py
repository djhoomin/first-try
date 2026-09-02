"""Recovering the images from a run that recorded only receipts."""

import json

from first_try.fetch import harvest, normalise


def test_harvest_finds_prompt_and_media_in_an_unknown_shape():
    """History shapes vary by server; this must work without an adapter."""
    payload = {
        "data": {"items": [
            {"prompt": "A misty pine forest at dawn",
             "request_id": "abc-123",
             "result": {"sample": "https://cdn.test/one.png"}},
            {"prompt": "no media on this one", "status": "pending"},
            {"nested": {"deeper": [
                {"prompt": "A quiet street", "outputs": [{"url": "https://cdn.test/two.jpg"}]}
            ]}},
        ]}
    }
    got = harvest(payload)
    assert [g["prompt"] for g in got] == ["A misty pine forest at dawn", "A quiet street"]
    assert got[0]["urls"] == ["https://cdn.test/one.png"]
    assert got[0]["request_id"] == "abc-123"


def test_prompts_match_across_whitespace_and_case():
    """The two representations of a prompt are rarely byte-identical."""
    sent = "A quiet, empty residential street  scene at golden hour"
    seen = "a quiet, empty residential STREET scene at golden hour"
    assert normalise(sent) == normalise(seen)


def test_normalise_truncates_so_a_trailing_edit_still_matches():
    long = "x" * 200
    assert normalise(long + "tail-a") == normalise(long + "tail-b")


def test_harvest_ignores_items_with_no_media():
    assert harvest({"items": [{"prompt": "nothing here"}]}) == []


def test_harvest_survives_junk():
    assert harvest(None) == [] and harvest("a string") == [] and harvest(42) == []
