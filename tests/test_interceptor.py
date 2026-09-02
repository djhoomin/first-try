import pytest

from first_try.interceptor import Interceptor, Policy
from first_try.transcript import Transcript


class Server:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.exc:
            raise self.exc
        return {"status": "ready"}


def test_dry_run_records_intent_without_spending():
    t = Transcript("T11", "test", dry_run=True)
    server = Server()
    Interceptor(server, t, Policy(dry_run=True)).call(
        "generate_video",
        {"requests": [{"mode": "t2v", "duration": 20, "resolution": "fhd"}] * 4},
        turn=1,
    )
    assert server.calls == []          # never reached the server
    assert t.spend_usd == 0
    assert round(t.intended_usd, 2) == 23.20


def test_blocked_calls_return_success_not_an_error():
    """Otherwise we measure recovery from our own safety rail."""
    t = Transcript("T11", "test", dry_run=True)
    result = Interceptor(Server(), t, Policy(dry_run=True)).call("generate_image", {}, turn=1)
    assert "error" not in result


def test_free_tools_run_even_in_dry_run():
    t = Transcript("T09", "test", dry_run=True)
    server = Server()
    Interceptor(server, t, Policy(dry_run=True)).call("get_credits", {}, turn=1)
    assert server.calls == [("get_credits", {})]


def test_per_call_cap_blocks_one_call_without_blocking_cheap_ones():
    t = Transcript("T10", "test", dry_run=False)
    server = Server()
    i = Interceptor(server, t, Policy(dry_run=False, budget_usd=100, per_call_cap_usd=1.0))
    i.call("generate_video", {"requests": [{"mode": "t2v", "duration": 20, "resolution": "fhd"}]}, turn=1)
    i.call("generate_image", {"requests": [{"model": "flux2_klein_4b"}]}, turn=2)
    assert t.calls[0].blocked and not t.calls[1].blocked
    assert len(server.calls) == 1


def test_budget_stops_the_run_before_it_overspends():
    t = Transcript("T06", "test", dry_run=False)
    i = Interceptor(Server(), t, Policy(dry_run=False, budget_usd=0.05, per_call_cap_usd=10))
    i.call("generate_image", {"requests": [{"model": "flux2_max"}]}, turn=1)   # 0.07 > 0.05
    assert t.calls[0].blocked


def test_server_errors_reach_the_agent_verbatim():
    """Recovery is being measured, so the real error text has to get through."""
    t = Transcript("T13", "test", dry_run=False)
    server = Server(exc=RuntimeError("vto is not a model; use the vto tool"))
    result = Interceptor(server, t, Policy(dry_run=False)).call(
        "generate_image", {"requests": [{"model": "vto"}]}, turn=1
    )
    assert "use the vto tool" in result["error"]
    assert t.calls[0].failed
    assert t.turns_to_first_success() is None
