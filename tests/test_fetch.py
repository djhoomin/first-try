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


def test_signed_urls_keep_their_query_string():
    """Media URLs are signed. Truncating at the extension yields a 400."""
    from first_try.mcp_client import image_urls

    signed = ("https://app.bfl.ai/storage/v1/object/sign/bfl-mcp/abc/generations/"
              "def.jpg?token=eyJraWQiOiJiM2Q3NDVmMy0zNjNj.sig-_123")
    assert image_urls({"result": {"sample": signed}}) == [signed]


def test_unsigned_urls_still_match():
    from first_try.mcp_client import image_urls

    assert image_urls({"u": "https://cdn.test/a.png"}) == ["https://cdn.test/a.png"]


def test_a_trailing_quote_or_paren_is_not_part_of_the_url():
    from first_try.mcp_client import image_urls

    blob = 'see ![](https://cdn.test/a.png?token=xyz) and "https://cdn.test/b.jpg?t=1"'
    assert image_urls(blob) == ["https://cdn.test/a.png?token=xyz", "https://cdn.test/b.jpg?t=1"]


def test_review_prefers_the_local_copy_over_the_signed_url():
    """Signatures expire; the findings outlive them."""
    from first_try.review import render_review

    rows = [{"task_id": "T01", "title": "t", "needs_review": True, "errored": False,
             "checks": [{"kind": "manual", "passed": False, "detail": "needs review: look at it",
                         "skipped": False}],
             "intended_usd": 0.03, "turns": 2}]
    transcripts = {"T01": {"task_id": "T01", "calls": [
        {"name": "generate_image", "est_usd": 0.03, "args": {"requests": [{"prompt": "p"}]},
         "result_urls": ["https://app.bfl.ai/signed.jpg?token=abc"],
         "result_files": ["media/deadbeef.jpg"]}
    ]}}
    page = render_review(rows, transcripts)
    assert "media/deadbeef.jpg" in page
    assert "signed.jpg" not in page
