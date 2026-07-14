"""mandi.py: Agmarknet client -- filter normalization and location tiering.

Exercises two real bugs caught while debugging "no rate available" locally:
(1) filters are exact-match against Title Case values, so a lowercase
    "tomato" from the intent classifier must be normalized before querying;
(2) district and market are NOT the same thing and must not be ANDed
    together as one query, or a real row reported under a different market
    in the same district is silently missed.
"""
import time

import pytest

import firestore_client
import mandi


def _row(commodity="Tomato", market="Guntur", district="Guntur", state="Andhra Pradesh",
         arrival_date="08/07/2026", modal_price="1800"):
    return {
        "commodity": commodity, "variety": "Local", "market": market, "district": district,
        "state": state, "arrival_date": arrival_date, "min_price": "1500",
        "max_price": "2000", "modal_price": modal_price,
    }


@pytest.fixture(autouse=True)
def _no_persistent_cache(monkeypatch):
    """Everything below exercises the tiering/normalization logic in-process,
    same as before the Firestore-backed persistent cache was added -- keep
    these offline regardless of whether GOOGLE_CLOUD_PROJECT happens to be set
    in the environment. The persistent cache itself is covered separately
    below, with an explicit fake firestore_client."""
    monkeypatch.setattr(mandi, "_persistent_cache_enabled", lambda: False)


def test_fetch_normalizes_filters_to_title_case(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"records": []}

    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        return FakeResponse()

    monkeypatch.setattr(mandi, "requests", type("R", (), {"get": staticmethod(fake_get)}))
    monkeypatch.setenv("DATA_GOV_API_KEY", "test-key")

    mandi._fetch("tomato", state="andhra pradesh", district="guntur", market="guntur")

    assert captured["filters[commodity]"] == "Tomato"
    assert captured["filters[state]"] == "Andhra Pradesh"
    assert captured["filters[district]"] == "Guntur"
    assert captured["filters[market]"] == "Guntur"


def test_get_price_widens_from_market_to_district_not_anded(monkeypatch):
    """A wrong/unknown exact market name shouldn't hide a real row reported
    under a different market in the same district."""
    calls = []

    def fake_fetch(commodity, state=None, district=None, market=None, limit=10):
        calls.append({"state": state, "district": district, "market": market})
        if market:
            return []  # no row for that specific (wrong-guess) market
        if district:
            return [_row(market="Tenali")]  # real row, different market, same district
        return []

    monkeypatch.setattr(mandi, "_fetch", fake_fetch)
    mandi._CACHE.clear()

    result = mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur", market="Guntur")

    assert result is not None
    assert result["market"] == "Tenali"
    # market tier tried first (and alone, not ANDed with a broader retry), then district tier
    assert calls[0]["market"] == "Guntur"
    assert calls[1]["market"] is None and calls[1]["district"] == "Guntur"


def test_get_price_widens_to_state_when_district_has_no_rows(monkeypatch):
    def fake_fetch(commodity, state=None, district=None, market=None, limit=10):
        if district:
            return []
        if state:
            return [_row(district="Vijayawada")]
        return []

    monkeypatch.setattr(mandi, "_fetch", fake_fetch)
    mandi._CACHE.clear()

    result = mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur")
    assert result is not None and result["district"] == "Vijayawada"


def test_get_price_returns_none_without_fabricating(monkeypatch):
    monkeypatch.setattr(mandi, "_fetch", lambda *a, **k: [])
    mandi._CACHE.clear()
    assert mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur") is None


def test_fetch_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("DATA_GOV_API_KEY", raising=False)
    try:
        mandi._fetch("tomato")
        assert False, "expected MandiError"
    except mandi.MandiError:
        pass


# --- persistent (Firestore-backed) cache ----------------------------------------
# A fake in-memory Firestore, standing in for the real mandi_cache collection --
# same convention as tests/test_confirm_endpoint.py.

@pytest.fixture
def fake_persistent_cache(monkeypatch):
    monkeypatch.setattr(mandi, "_persistent_cache_enabled", lambda: True)
    store: dict[str, dict] = {}

    def fake_get(key):
        return store.get(key)

    def fake_set(key, records):
        store[key] = {"records": records, "fetched_at": time.time()}

    monkeypatch.setattr(firestore_client, "get_mandi_cache", fake_get)
    monkeypatch.setattr(firestore_client, "set_mandi_cache", fake_set)
    # fetched_at above is a plain float (seconds since epoch), not the datetime a
    # real Firestore SERVER_TIMESTAMP round-trips as -- patch the age check to match.
    monkeypatch.setattr(mandi, "_cache_age_seconds", lambda fetched_at: time.time() - fetched_at)
    return store


def test_warm_populates_persistent_cache_for_get_price(monkeypatch, fake_persistent_cache):
    calls = []

    def fake_fetch(commodity, state=None, district=None, market=None, limit=10):
        calls.append(1)
        return [_row()]

    monkeypatch.setattr(mandi, "_fetch", fake_fetch)
    mandi._CACHE.clear()

    mandi.warm("tomato", state="Andhra Pradesh", district="Guntur")
    assert len(calls) == 1  # warm() always makes a live call

    result = mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur")
    assert result is not None and result["market"] == "Guntur"
    assert len(calls) == 1  # served from the persistent cache, no second live call


def test_persistent_cache_expires_after_ttl(monkeypatch, fake_persistent_cache):
    calls = []
    monkeypatch.setattr(mandi, "_fetch", lambda *a, **k: calls.append(1) or [_row()])
    mandi._CACHE.clear()

    mandi.warm("tomato", state="Andhra Pradesh", district="Guntur")
    key = mandi._persistent_key("tomato", "Andhra Pradesh", "Guntur", None)
    fake_persistent_cache[key]["fetched_at"] -= mandi._PERSISTENT_TTL + 1  # force staleness

    mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur")
    assert len(calls) == 2  # stale entry ignored, live fetch made again


def test_persistent_cache_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(mandi, "_persistent_cache_enabled", lambda: False)
    monkeypatch.setattr(
        firestore_client, "get_mandi_cache",
        lambda key: (_ for _ in ()).throw(AssertionError("should not touch Firestore when disabled")),
    )
    monkeypatch.setattr(mandi, "_fetch", lambda *a, **k: [_row()])
    mandi._CACHE.clear()

    result = mandi.get_price("tomato", state="Andhra Pradesh", district="Guntur")
    assert result is not None
