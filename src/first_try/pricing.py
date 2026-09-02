"""Cost estimation for FLUX calls, from BFL's published pricing.

Every image number here is a LOWER BOUND. FLUX.2 bills by megapixel and the
published table quotes "from" prices at the smallest output size, so a real run
costs at least what this reports and possibly more. Video is exact, because it
is a flat rate per second of output.

Reporting a floor is deliberate rather than lazy. "Agents spent at least $X" is
a claim nobody can argue with. "Agents spent exactly $X" invites one correction
that would sink the credibility of everything else in the report.

Source: https://docs.bfl.ai pricing page, read 2026-09-02. 1 credit = $0.01 USD.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["Estimate", "estimate_call", "IMAGE_FLOOR_USD", "VIDEO_USD_PER_SECOND"]

# Floor price per image at the smallest billed resolution. Editing costs more
# than text-to-image on pro, which is the kind of asymmetry an agent has no way
# to discover from the tool schema.
IMAGE_FLOOR_USD: dict[str, dict[str, float]] = {
    "flux2_klein_4b": {"t2i": 0.014, "edit": 0.014},
    "flux2_klein_9b_preview": {"t2i": 0.015, "edit": 0.015},
    "flux2_pro_preview": {"t2i": 0.030, "edit": 0.045},
    "flux2_max": {"t2i": 0.070, "edit": 0.070},
    "flux2_flex": {"t2i": 0.050, "edit": 0.050},
}
DEFAULT_IMAGE_MODEL = "flux2_pro_preview"

# Exact, per second of output.
VIDEO_USD_PER_SECOND: dict[tuple[str, str], float] = {
    ("t2v", "hd"): 0.17, ("t2v", "fhd"): 0.29,
    ("i2v", "hd"): 0.17, ("i2v", "fhd"): 0.29,
    ("v2v", "hd"): 0.43, ("v2v", "fhd"): 0.54,
}
VIDEO_DRAFT_USD_PER_SECOND: dict[str, float] = {"t2v": 0.06, "i2v": 0.06, "v2v": 0.12}

# Used when a request says duration "auto" or omits it. The documented range is
# 5 to 20 seconds; assume the midpoint and mark the estimate inexact.
ASSUMED_AUTO_DURATION_S = 12.0

# vto is priced as a single edit-class generation. Not separately published, so
# treated as pro-edit and flagged inexact.
VTO_FLOOR_USD = 0.045


@dataclass(frozen=True)
class Estimate:
    """What a call costs, and how much to trust the number."""

    usd: float
    exact: bool
    note: str = ""

    def __add__(self, other: "Estimate") -> "Estimate":
        return Estimate(
            usd=self.usd + other.usd,
            exact=self.exact and other.exact,
            note="; ".join(n for n in (self.note, other.note) if n),
        )


ZERO = Estimate(0.0, exact=True, note="")


def _image_request_usd(req: dict[str, Any]) -> float:
    model = req.get("model") or DEFAULT_IMAGE_MODEL
    prices = IMAGE_FLOOR_USD.get(model)
    if prices is None:
        # An unknown model is priced at the most expensive known one, so an
        # estimate is never accidentally optimistic.
        prices = IMAGE_FLOOR_USD["flux2_max"]
    kind = "edit" if _has_input_image(req) else "t2i"
    return prices[kind]


def _has_input_image(req: dict[str, Any]) -> bool:
    if req.get("input_image"):
        return True
    return any(k.startswith("input_image") and req[k] for k in req)


def _video_request_usd(req: dict[str, Any], draft: bool) -> tuple[float, bool]:
    mode = (req.get("mode") or "t2v").lower()
    resolution = (req.get("resolution") or "hd").lower()
    duration = req.get("duration")
    exact = True
    if duration in (None, "auto"):
        duration = ASSUMED_AUTO_DURATION_S
        exact = False
    seconds = float(duration)
    if draft:
        rate = VIDEO_DRAFT_USD_PER_SECOND.get(mode, VIDEO_DRAFT_USD_PER_SECOND["t2v"])
    else:
        rate = VIDEO_USD_PER_SECOND.get((mode, resolution), VIDEO_USD_PER_SECOND[("t2v", "fhd")])
    return seconds * rate, exact


def estimate_call(tool: str, args: dict[str, Any]) -> Estimate:
    """Lower-bound cost of one MCP tool call.

    Free tools return zero rather than being special-cased at the call site, so
    the ledger can price every call uniformly.
    """
    args = args or {}

    if tool in {"get_credits", "get_history", "get_result"}:
        return ZERO

    if tool == "generate_image":
        requests = args.get("requests") or [args]
        total = sum(_image_request_usd(r) for r in requests)
        return Estimate(total, exact=False, note="image floor price, megapixel scaling ignored")

    if tool == "generate_variations":
        count = int(args.get("count") or args.get("n") or 4)
        # Variations reuse the original model, which we cannot see from the call
        # alone. Price at the default and say so.
        unit = IMAGE_FLOOR_USD[DEFAULT_IMAGE_MODEL]["t2i"]
        return Estimate(count * unit, exact=False, note="variations priced at the default model")

    if tool == "vto":
        return Estimate(VTO_FLOOR_USD, exact=False, note="vto priced as one pro edit")

    if tool == "generate_video":
        requests = args.get("requests") or [args]
        draft = bool(args.get("draft"))
        total, exact = 0.0, True
        for req in requests:
            usd, req_exact = _video_request_usd(req, draft or bool(req.get("draft")))
            total += usd
            exact = exact and req_exact
        note = "" if exact else "duration assumed for auto-length clips"
        return Estimate(total, exact=exact, note=note)

    if tool == "enhance_video":
        # Re-renders a draft at fhd. Duration is carried by the draft, which the
        # call does not restate, so this is a guess and must be labelled one.
        usd = ASSUMED_AUTO_DURATION_S * VIDEO_USD_PER_SECOND[("t2v", "fhd")]
        return Estimate(usd, exact=False, note="enhance duration unknown from the call")

    return Estimate(0.0, exact=False, note=f"unpriced tool: {tool}")
