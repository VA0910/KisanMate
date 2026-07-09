"""mandi.py: Agmarknet client -- filter normalization and location tiering.

Exercises two real bugs caught while debugging "no rate available" locally:
(1) filters are exact-match against Title Case values, so a lowercase
    "tomato" from the intent classifier must be normalized before querying;
(2) district and market are NOT the same thing and must not be ANDed
    together as one query, or a real row reported under a different market
    in the same district is silently missed.
"""
import mandi


def _row(commodity="Tomato", market="Guntur", district="Guntur", state="Andhra Pradesh",
         arrival_date="08/07/2026", modal_price="1800"):
    return {
        "commodity": commodity, "variety": "Local", "market": market, "district": district,
        "state": state, "arrival_date": arrival_date, "min_price": "1500",
        "max_price": "2000", "modal_price": modal_price,
    }


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
