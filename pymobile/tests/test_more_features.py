"""Tests for non-UI features: input validation, HTTP cache, snapshot testing,
app metadata."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pymobile import App, HttpCache, HttpClient, Label
from pymobile.core.bridge import StubBridge
from pymobile.core.ui.preview import assert_snapshot, render_ascii, snapshot_path
from pymobile.core.validation import (
    ValidationError,
    Validator,
    between,
    boolean,
    email,
    integer,
    length,
    matches,
    max_length,
    min_length,
    number,
    one_of,
    optional,
    regex,
    required,
)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def test_required():
    assert required(None) == "is required"
    assert required("") == "is required"
    assert required([]) == "is required"
    assert required(0) is None
    assert required("x") is None


def test_email_validator():
    assert email("a@b.com") is None
    assert email("bad") is not None


def test_length_validators():
    assert length(2, 5)("abc") is None
    assert length(2, 5)("a") is not None
    assert length(2, 5)("abcdef") is not None
    assert min_length(3)("abc") is None
    assert min_length(3)("ab") is not None
    assert max_length(3)("abc") is None
    assert max_length(3)("abcd") is not None


def test_integer_number():
    assert integer(5) is None
    assert integer("12") is None
    assert integer(1.5) is not None
    assert integer(True) is not None
    assert number(1.5) is None
    assert number("3.14") is None

def test_number_accepts_decimal_comma():
    """0.9.4: prices typed in Ukrainian/European notation ("89,90")."""
    assert number("89,90") is None
    assert number("0,5") is None
    assert number("1234,5") is None
    assert number("89.90") is None
    assert number("abc") is not None
    assert number("") is not None


def test_bounds_accept_decimal_comma():
    """0.9.4: min/max/between read the same notation as the number rule."""
    assert between(1, 2)("1,5") is None
    assert between(1, 2)("2,5") is not None


def test_between_and_bounds():
    assert between(0, 10)(5) is None
    assert between(0, 10)(20) is not None
    assert between(0, 10)("abc") is not None
    from pymobile.core.validation import max as vmax
    from pymobile.core.validation import min as vmin

    assert vmin(5)(7) is None
    assert vmin(5)(3) is not None
    assert vmax(5)(3) is None
    assert vmax(5)(7) is not None


def test_matches_and_one_of():
    assert matches("pw")("pw") is None
    assert matches("pw")("xx") is not None
    assert one_of(["a", "b"])("a") is None
    assert one_of(["a", "b"])("z") is not None


def test_regex_and_boolean():
    assert regex(r"\d{4}")("1234") is None
    assert regex(r"\d{4}")("abcd") is not None
    assert boolean(True) is None
    assert boolean("yes") is None
    assert boolean("maybe") is not None


def test_validator_combines_fields():
    v = Validator(
        [
            ("email", [required, email]),
            ("age", [integer, between(0, 120)]),
            ("code", [regex(r"\d{3}")]),
        ]
    )
    assert v.validate({"email": "a@b.c", "age": 30, "code": "123"}) == {}
    errors = v.validate({"email": "bad", "age": 200, "code": "xy"})
    assert set(errors) == {"email", "age", "code"}


def test_validator_optional_skips_empty():
    v = Validator([("email", [optional, email])])
    assert v.validate({"email": ""}) == {}
    assert v.validate({}) == {}
    assert "email" in v.validate({"email": "bad"})


def test_validator_validate_or_raise():
    v = Validator([("age", [integer, between(0, 120)])])
    v.validate_or_raise({"age": 30})  # ok
    with pytest.raises(ValidationError):
        v.validate_or_raise({"age": 999})


def test_validator_add():
    v = Validator([("name", [required])])
    v.add("name", max_length(5))
    assert v.validate({"name": "toolong"}) == {"name": "must be at most 5 characters"}


# --------------------------------------------------------------------------
# HTTP cache
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        _Handler.hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"n": %d}' % _Handler.hits)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    _Handler.hits = 0
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_cache_set_get(http_server, tmp_path):
    cache = HttpCache.at(tmp_path / "c.json")
    client = HttpClient(base_url=http_server, cache=cache, retries=0, backoff=0)
    r1 = client.get_cached("/", ttl=999)
    assert r1.status == 200
    # Second call is served from cache (no extra network hit).
    r2 = client.get_cached("/", ttl=999)
    assert r2.status == 200
    assert _Handler.hits == 1


def test_cache_fresh_served_without_network(tmp_path):
    cache = HttpCache.at(tmp_path / "c.json")
    cache.set("http://example.invalid/", 200, {"content-type": "text/plain"}, b"cached")
    # Even though the URL is unreachable, a fresh cache returns it.
    client = HttpClient(cache=cache)
    resp = client.get_cached("http://example.invalid/", ttl=999)
    assert resp.text == "cached"


def test_cache_stale_used_on_failure(tmp_path):
    cache = HttpCache.at(tmp_path / "c.json")
    cache.set("http://example.invalid/", 200, {}, b"old")
    client = HttpClient(cache=cache)
    # URL unreachable AND stale -> still returns stale copy (offline support).
    resp = client.get_cached("http://example.invalid/", ttl=-1)
    assert resp.text == "old"


def test_cache_requires_cache_configured():
    client = HttpClient()
    with pytest.raises(ValueError):
        client.get_cached("http://x/")


def test_cache_delete_and_clear(tmp_path):
    cache = HttpCache.at(tmp_path / "c.json")
    cache.set("u1", 200, {}, b"a")
    cache.set("u2", 200, {}, b"b")
    assert len(cache) == 2
    assert cache.delete("u1")
    assert not cache.delete("u1")
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0


# --------------------------------------------------------------------------
# Snapshot testing
# --------------------------------------------------------------------------
def test_snapshot_writes_then_matches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # First call writes the golden file and passes.
    assert_snapshot(Label("hello").to_dict(), __file__, name="probe", update=True)
    path = snapshot_path(__file__, "probe")
    assert path.exists()
    # Second call matches.
    assert_snapshot(Label("hello").to_dict(), __file__, name="probe")


def test_snapshot_detects_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert_snapshot(Label("v1").to_dict(), __file__, name="diff", update=True)
    with pytest.raises(AssertionError):
        assert_snapshot(Label("v2").to_dict(), __file__, name="diff")


def test_snapshot_path_location():
    path = snapshot_path(__file__, "home")
    assert path.parent.name == "snapshots"
    assert path.name.startswith("test_more_features__home")


# --------------------------------------------------------------------------
# App metadata
# --------------------------------------------------------------------------
def test_app_info_defaults():
    app = App("t", bridge=StubBridge(verbose=False))
    assert app.info["name"] == "t"
    assert app.info["version"] == "0.1.0"
    assert app.info["platform"] == "desktop"


def test_app_info_custom():
    app = App("MyApp", version="2.0.0", package="com.example.x", bridge=StubBridge(verbose=False))
    assert app.info["version"] == "2.0.0"
    assert app.info["package"] == "com.example.x"


def test_render_ascii_still_works():
    assert "hello" in render_ascii(Label("hello").to_dict())
