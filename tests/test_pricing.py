from first_try.pricing import estimate_call


def test_the_twenty_three_dollar_call():
    """Four 20s fhd clips, exactly as the documented envelope allows."""
    est = estimate_call("generate_video", {
        "requests": [{"mode": "t2v", "duration": 20, "resolution": "fhd"}] * 4
    })
    assert round(est.usd, 2) == 23.20
    assert est.exact  # video is a flat per-second rate, so this is not a guess


def test_draft_is_about_a_third():
    full = estimate_call("generate_video", {"requests": [{"mode": "t2v", "duration": 15, "resolution": "hd"}]})
    draft = estimate_call("generate_video", {"requests": [{"mode": "t2v", "duration": 15}], "draft": True})
    assert draft.usd < full.usd / 2


def test_editing_costs_more_than_generating_on_pro():
    """An asymmetry an agent cannot see from the tool schema."""
    t2i = estimate_call("generate_image", {"requests": [{"model": "flux2_pro_preview"}]})
    edit = estimate_call("generate_image", {"requests": [{"model": "flux2_pro_preview", "input_image": "u"}]})
    assert edit.usd > t2i.usd


def test_unknown_model_is_priced_pessimistically():
    """An estimate must never be accidentally optimistic."""
    unknown = estimate_call("generate_image", {"requests": [{"model": "flux9_imaginary"}]})
    most_expensive = estimate_call("generate_image", {"requests": [{"model": "flux2_max"}]})
    assert unknown.usd == most_expensive.usd


def test_free_tools_are_free():
    for tool in ("get_credits", "get_history", "get_result"):
        assert estimate_call(tool, {}).usd == 0
