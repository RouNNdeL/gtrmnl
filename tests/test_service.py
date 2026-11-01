import json
import logging

from gtrmnl import payload, service
from gtrmnl.state import State
from gtrmnl.trmnl import PushResult


class Poster:
    def __init__(self, *results):
        self.results = list(results)
        self.bodies = []

    def __call__(self, url, body):
        self.bodies.append(body)
        return self.results.pop(0)


def test_push_then_unchanged(model, cfg):
    st = State()
    post = Poster(PushResult(True, 200))
    out = service.deliver(model, cfg, st, 1000.0, post=post)
    assert out.pushed and st.last_hash and st.last_push == 1000.0
    out = service.deliver(model, cfg, st, 1900.0, post=post)
    assert not out.pushed and out.reason == "unchanged"


def test_heartbeat(model, cfg):
    st = State()
    post = Poster(PushResult(True, 200), PushResult(True, 200))
    service.deliver(model, cfg, st, 0.0, post=post)
    out = service.deliver(model, cfg, st, cfg.heartbeat_hours * 3600.0, post=post)
    assert out.pushed and len(post.bodies) == 2


def test_422_learns_limit_and_refits(model, cfg):
    st = State()
    msg = "Large payload received (1345 bytes), should be less than 1kb."
    post = Poster(PushResult(False, 422, msg, limit=1000), PushResult(True, 200))
    out = service.deliver(model, cfg, st, 0.0, post=post)
    assert out.pushed and st.learned_limit == 1000
    assert len(post.bodies[1]) <= 1000 < len(post.bodies[0])
    assert st.effective_limit(cfg.payload_limit) == 1000


def test_422_without_parsable_limit_shrinks(model, cfg):
    st = State()
    post = Poster(PushResult(False, 422, "too big"), PushResult(True, 200))
    out = service.deliver(model, cfg, st, 0.0, post=post)
    assert out.pushed and st.learned_limit < len(post.bodies[0])


def test_429_backs_off(model, cfg):
    st = State()
    post = Poster(PushResult(False, 429, "", retry_after=60))
    out = service.deliver(model, cfg, st, 0.0, post=post)
    assert out.reason == "429" and st.backoff_until == service.DEFAULT_BACKOFF_S
    out = service.deliver(model, cfg, st, 10.0, post=post)
    assert out.reason == "backoff" and len(post.bodies) == 1


def test_auth_flag_pushed_once(cfg):
    st = State(last_hash="abc")
    post = Poster(PushResult(True, 200))
    service.push_auth_flag(cfg, st, 5.0, post=post)
    service.push_auth_flag(cfg, st, 6.0, post=post)
    body = json.loads(post.bodies[0])
    assert body["merge_strategy"] == "deep_merge" and body["merge_variables"]["err"] == "auth"
    assert len(post.bodies) == 1 and st.last_hash is None


def test_webhook_url_never_logged(model, cfg, caplog):
    st = State()
    post = Poster(PushResult(False, 500, "boom"))
    with caplog.at_level(logging.DEBUG):
        service.deliver(model, cfg, st, 0.0, post=post)
    assert "secret-uuid" not in caplog.text
    assert "secret-uuid" not in repr(cfg)


def test_health(cfg):
    ok, _ = service.healthy(cfg, State(), 1000.0)
    assert not ok
    ok, _ = service.healthy(cfg, State(last_success=1000.0), 1000.0 + 25 * 60)
    assert ok
    ok, _ = service.healthy(cfg, State(last_success=1000.0), 1000.0 + 40 * 60)
    assert not ok
    ok, why = service.healthy(cfg, State(last_success=1000.0, auth_failed=True), 1001.0)
    assert not ok and "login" in why


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    State(last_hash="x", learned_limit=2000).save(p)
    st = State.load(p)
    assert st.last_hash == "x" and st.learned_limit == 2000
    p.write_text("not json")
    assert State.load(p) == State()


def test_fit_payload_size_matches_post(model):
    f = payload.fit(model, 3000)
    assert len(payload.body_bytes(f.payload)) == f.size <= 3000
