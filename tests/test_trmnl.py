import logging

import requests

from gtrmnl import trmnl

MSG = (
    "Large payload received (4391 bytes), should be less than 2kb. "
    "Subscribe to TRMNL+ for higher limit."
)


def test_parse_limit():
    assert trmnl.parse_limit(MSG) == 2000
    assert trmnl.parse_limit("should be less than 10kb") == 10000
    assert trmnl.parse_limit("should be less than 5000 bytes") == 5000
    assert trmnl.parse_limit("nothing here") is None
    assert trmnl.parse_size(MSG) == 4391


class FakeResp:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def post(self, url, data, headers, timeout):
        self.calls.append((url, data, headers))
        if self.exc:
            raise self.exc
        return self.resp


URL = "https://trmnl.com/api/custom_plugins/very-secret-uuid"


def test_push_ok():
    s = FakeSession(FakeResp(200, {"message": "ok"}))
    r = trmnl.push(URL, b"{}", s)
    assert r.ok and s.calls[0][2]["Content-Type"] == "application/json"


def test_push_422_limit():
    r = trmnl.push(URL, b"{}", FakeSession(FakeResp(422, {"error": MSG})))
    assert not r.ok and r.status == 422 and r.limit == 2000


def test_push_429_retry_after():
    r = trmnl.push(URL, b"{}", FakeSession(FakeResp(429, "slow down", {"Retry-After": "120"})))
    assert r.status == 429 and r.retry_after == 120


def test_network_error_hides_url(caplog):
    exc = requests.ConnectionError(f"failed to reach {URL}")
    with caplog.at_level(logging.DEBUG):
        r = trmnl.push(URL, b"{}", FakeSession(exc=exc))
    assert not r.ok and "secret" not in r.message and "secret" not in caplog.text
