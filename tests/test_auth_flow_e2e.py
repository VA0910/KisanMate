"""End-to-end browser tests: phone + OTP sign-in, profile setup, and the profile page.

Drives the real SPA in a headless Chromium to prove:
  1. brand-new phone  -> Welcome -> language -> phone -> OTP -> profile SETUP wizard
  2. completing setup writes location + soil + crops (with planting dates) to Firestore
  3. Ramesh's phone   -> straight to home, greeted as Ramesh
  4. sign-out (from the profile page) -> returns to Welcome
  5. reload           -> stays signed in (localStorage session)
  6. profile page: change soil, save, and the change survives a reload

Environment-gated so the normal `pytest` run stays green without a browser or
Firestore: skips if Playwright isn't installed, if Chromium can't launch, or if
Firestore isn't reachable.

Run with:  pytest tests/test_auth_flow_e2e.py -v
(after `pip install playwright && playwright install chromium`, with Firestore creds)
"""
import os
import socket
import subprocess
import time

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

import firestore_client  # noqa: E402

SEEDED = {"ramesh", "lakshmi", "venkat", "sita"}
RAMESH_PHONE = "9876500001"
NEW_PHONE = "9123456781"


def _firestore_reachable() -> bool:
    try:
        list(firestore_client.get_client().collection("farmers").limit(1).stream())
        return True
    except Exception:
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cleanup_non_seeded():
    try:
        for f in firestore_client.list_farmers():
            if f.id not in SEEDED:
                firestore_client.get_client().collection("farmers").document(f.id).delete()
    except Exception:
        pass


@pytest.fixture(scope="module")
def base_url():
    if not _firestore_reachable():
        pytest.skip("Firestore not reachable; the profile endpoints need it")
    import seed
    import seed_crops
    seed.seed()          # demo farmers (with phones)
    seed_crops.seed()    # crops catalog (tomato etc.) for the crop picker
    _cleanup_non_seeded()

    port = _free_port()
    proc = subprocess.Popen(
        [os.sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not start")
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        _cleanup_non_seeded()
        # Restore Ramesh to seeded state (the profile-edit test mutates his soil).
        seed.seed()


@pytest.fixture()
def page(base_url):
    try:
        pw = sync_playwright().start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Playwright runtime unavailable: {exc}")
    try:
        browser = pw.chromium.launch()
    except Exception as exc:
        pw.stop()
        pytest.skip(f"Chromium not installed (run `playwright install chromium`): {exc}")
    # No geolocation permission -> the setup wizard's silent district fallback fires.
    context = browser.new_context()
    pg = context.new_page()
    try:
        yield pg
    finally:
        context.close()
        browser.close()
        pw.stop()


def _onboard_to_phone(page, base_url):
    page.goto(base_url)
    page.wait_for_selector("#screen-welcome:not([hidden])")
    page.click("#welcome-start-btn")
    page.wait_for_selector("#screen-language:not([hidden])")
    page.click('.lang-card[data-lang="en"]')
    page.click("#language-next-btn")
    page.wait_for_selector("#screen-phone:not([hidden])")


def _sign_in(page, base_url, phone):
    _onboard_to_phone(page, base_url)
    page.fill("#phone-input", phone)
    page.click("#send-otp-btn")
    page.wait_for_selector("#screen-otp:not([hidden])")
    page.fill("#otp-input", page.inner_text("#demo-otp-code").strip())
    page.click("#verify-otp-btn")


def test_new_phone_setup_persists_location_soil_crops(page, base_url):
    _sign_in(page, base_url, NEW_PHONE)

    # New user lands on the stepped setup wizard.
    page.wait_for_selector("#screen-profile-setup:not([hidden])")

    # Step 1: location -- geolocation is denied in headless, so the silent district
    # fallback leaves Guntur selected. Advance.
    page.wait_for_selector("#setup-step-location:not([hidden])")
    page.click("#setup-location-next")

    # Step 2: soil.
    page.wait_for_selector("#setup-step-soil:not([hidden])")
    page.click('label[for="setup-soil-black"]')
    page.click("#setup-soil-next")

    # Step 3: crops -- pick a crop and give it a planting date, then finish.
    page.wait_for_selector("#setup-step-crops:not([hidden])")
    page.wait_for_selector("#setup-crops-list .crop-row")
    page.select_option("#setup-crops-list .crop-row-select", "tomato")
    page.fill("#setup-crops-list .crop-row-date", "2026-06-15")
    page.click("#setup-finish-btn")

    page.wait_for_selector("#screen-home:not([hidden])")

    # The farmer document now carries location, soil, and crops with planting dates.
    farmer = firestore_client.get_farmer_by_phone(NEW_PHONE)
    assert farmer is not None
    assert farmer.soil_type == "black"
    assert farmer.location.mandal == "Guntur"
    assert isinstance(farmer.location.lat, float) and isinstance(farmer.location.lng, float)
    assert [{"crop_id": c.crop_id, "planting_date": c.planting_date} for c in farmer.current_crops] == [
        {"crop_id": "tomato", "planting_date": "2026-06-15"}
    ]


def test_ramesh_phone_goes_straight_home(page, base_url):
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    assert "Ramesh" in page.inner_text("#home-heading")


def test_sign_out_from_profile_returns_to_welcome(page, base_url):
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    page.click("#open-profile-btn")
    page.wait_for_selector("#screen-profile:not([hidden])")
    page.click("#sign-out-btn")
    page.wait_for_selector("#screen-welcome:not([hidden])")
    assert page.evaluate("() => localStorage.getItem('kisanmate_session')") is None


def test_reload_keeps_signed_in(page, base_url):
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    page.reload()
    page.wait_for_selector("#screen-home:not([hidden])")
    assert "Ramesh" in page.inner_text("#home-heading")


def test_profile_edit_soil_persists_across_reload(page, base_url):
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")

    # Open profile, change soil to red, save.
    page.click("#open-profile-btn")
    page.wait_for_selector("#screen-profile:not([hidden])")
    page.wait_for_selector("#profile-soil-options .chip")
    page.click('label[for="profile-soil-red"]')
    page.click("#profile-save-btn")
    page.wait_for_selector("#screen-home:not([hidden])")

    # Persisted server-side...
    assert firestore_client.get_farmer("ramesh").soil_type == "red"

    # ...and survives a full reload: reopening the profile shows red selected.
    page.reload()
    page.wait_for_selector("#screen-home:not([hidden])")
    page.click("#open-profile-btn")
    page.wait_for_selector("#profile-soil-options .chip")
    assert page.is_checked("#profile-soil-red")
