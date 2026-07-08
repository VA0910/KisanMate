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
from datetime import date

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

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


def _cleanup_test_farmer():
    """Delete ONLY the farmer this suite creates (matched by its test phone).

    Never touch other non-seeded farmers: this Firestore project is shared with
    the deployed app, so a blanket "delete everything non-seeded" would wipe real
    users' documents.
    """
    try:
        farmer = firestore_client.get_farmer_by_phone(NEW_PHONE)
        if farmer is not None and farmer.id not in SEEDED:
            firestore_client.get_client().collection("farmers").document(farmer.id).delete()
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
    _cleanup_test_farmer()

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
        _cleanup_test_farmer()
        # Restore Ramesh to seeded state (the profile-edit test mutates his soil).
        seed.seed()


@pytest.fixture()
def page(base_url):
    try:
        pw = sync_playwright().start()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Playwright runtime unavailable: {exc}")
    try:
        # Fake media device so getUserMedia yields a test video stream in headless.
        browser = pw.chromium.launch(
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
        )
    except Exception as exc:
        pw.stop()
        pytest.skip(f"Chromium not installed (run `playwright install chromium`): {exc}")
    # Grant camera (for the diagnose capture); NOT geolocation, so the setup
    # wizard's silent district fallback still fires.
    context = browser.new_context(permissions=["camera"])
    pg = context.new_page()
    # A signed-in farmer with an unseen RSK officer verdict gets a one-time popup
    # that (correctly, as a modal) covers the viewport. It can appear at any point
    # after sign-in, so auto-dismiss it whenever it shows -- these tests exercise
    # the auth/profile/diagnose flows, not the verdict popup itself.
    pg.add_locator_handler(
        pg.locator("#verdict-modal:not([hidden])"),
        lambda: pg.click("#verdict-modal-dismiss-btn"),
    )
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
    # First visit plays the cinematic intro between language and login; skip it.
    page.wait_for_selector("#intro-overlay:not([hidden])")
    page.click("#intro-skip-btn")
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

    # Step 1: location. Geolocation is denied in headless, so use the live place
    # search. Mock the geocoder route so the test is deterministic and offline.
    page.route(
        "**/api/places*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"places":[{"name":"Kanpur, Uttar Pradesh","lat":26.46,"lng":80.32}]}',
        ),
    )
    page.wait_for_selector("#setup-step-location:not([hidden])")
    page.fill("#setup-place-input", "Kanpur")
    page.wait_for_selector("#setup-place-suggestions .place-option")
    page.click("#setup-place-suggestions .place-option")
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
    # The place picked from live search set its real coordinates as the location.
    assert farmer.location.mandal == "Kanpur, Uttar Pradesh"
    assert abs(farmer.location.lat - 26.46) < 0.01 and abs(farmer.location.lng - 80.32) < 0.01
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


def test_reminder_shows_on_home_and_changes_with_planting_date(page, base_url):
    # Ramesh's seeded tomato was planted 60 days ago -> an irrigation reminder is
    # DUE TODAY, computed from real dates with no action taken.
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    page.wait_for_selector("#reminders-card:not([hidden])")
    # An irrigation reminder is present and marked due today (badge).
    expect(page.locator("#reminders-card .reminder-irrigation")).to_have_count(1)
    expect(page.locator("#reminders-card .reminder-badge")).to_have_count(1)

    # Change the tomato's planting date to today on the profile page.
    page.click("#open-profile-btn")
    page.wait_for_selector("#screen-profile:not([hidden])")
    page.wait_for_selector("#profile-crops-list .crop-row")
    page.fill("#profile-crops-list .crop-row-date", date.today().isoformat())
    page.click("#profile-save-btn")
    page.wait_for_selector("#screen-home:not([hidden])")

    # Reminder recomputed (expect() polls until the background refresh lands):
    # no longer due today, and the next irrigation is 5 days out.
    page.wait_for_selector("#reminders-card:not([hidden])")
    expect(page.locator("#reminders-card .reminder-badge")).to_have_count(0)
    expect(page.locator("#reminders-card")).to_contain_text("5")


def test_diagnose_camera_capture_structured_and_dispute(page, base_url):
    """Take a live photo with the (fake) camera, get a structured result, and
    dispute it via the dispute path (not an officer verdict)."""
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    page.click("#go-diagnose-btn")
    page.wait_for_selector("#screen-diagnose:not([hidden])")

    # Live camera capture.
    page.click("#open-camera-btn")
    page.wait_for_selector("#camera-view:not([hidden])")
    page.wait_for_function("() => { var v = document.getElementById('camera-video'); return v && v.videoWidth > 0; }")
    page.click("#capture-photo-btn")
    page.wait_for_selector("#photo-preview-wrap:not([hidden])")

    with page.expect_response(lambda r: r.url.endswith("/api/diagnose")) as diag:
        page.click("#submit-diagnose-btn")
    case_id = diag.value.json().get("case_id")
    page.wait_for_selector("#diagnose-result:not([hidden])", timeout=30000)

    # Structured explanation renders in its own blocks (not the fallback paragraph).
    page.wait_for_selector("#result-explanation:not([hidden])")
    assert page.inner_text("#explain-todo").strip() != ""

    # "This isn't right" must call the DISPUTE path (never /api/confirm).
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(lambda r: "/dispute" in r.url and r.request.method == "POST") as disp:
        page.click("#not-right-btn")
    assert disp.value.status == 200
    assert disp.value.json()["status"] == "disputed"
    page.wait_for_selector("#dispute-thanks:not([hidden])")

    # Clean up the single case this test created.
    if case_id:
        try:
            firestore_client.get_client().collection("cases").document(case_id).delete()
        except Exception:
            pass


def test_intro_auto_plays_skips_and_plays_once(page, base_url):
    page.goto(base_url)
    page.wait_for_selector("#screen-welcome:not([hidden])")
    page.click("#welcome-start-btn")
    page.wait_for_selector("#screen-language:not([hidden])")
    page.click('.lang-card[data-lang="en"]')
    page.click("#language-next-btn")

    # Intro auto-plays with a caption...
    page.wait_for_selector("#intro-overlay:not([hidden])")
    first_caption = page.inner_text("#intro-caption").strip()
    assert first_caption != ""
    # ...and auto-advances to another scene with NO clicks.
    page.wait_for_function(
        "(c) => { var e = document.getElementById('intro-caption'); return e && e.textContent.trim() && e.textContent.trim() !== c; }",
        arg=first_caption, timeout=9000,
    )

    # Skip jumps straight to login.
    page.click("#intro-skip-btn")
    page.wait_for_selector("#screen-phone:not([hidden])")
    assert page.is_hidden("#intro-overlay")

    # Plays once: going language -> continue again skips straight to login.
    page.click("#phone-back-btn")
    page.wait_for_selector("#screen-language:not([hidden])")
    page.click("#language-next-btn")
    page.wait_for_selector("#screen-phone:not([hidden])")
    assert page.is_hidden("#intro-overlay")

    # "Watch intro" replays it.
    page.click("#watch-intro-btn")
    page.wait_for_selector("#intro-overlay:not([hidden])")
    page.click("#intro-skip-btn")
    page.wait_for_selector("#screen-phone:not([hidden])")


def test_recommend_conversation_text_path(page, base_url):
    """Voice-first recommend: the text-box fallback carries a free-form question
    to a structured, spoken-ready recommendation card."""
    _sign_in(page, base_url, RAMESH_PHONE)
    page.wait_for_selector("#screen-home:not([hidden])")
    page.click("#go-recommend-btn")
    page.wait_for_selector("#screen-recommend:not([hidden])")
    # The mic is the primary action; the text box is the accessible fallback.
    assert page.is_visible("#recommend-mic-btn")
    page.fill("#recommend-text-input", "what should I grow after tomatoes?")
    page.click("#recommend-ask-btn")
    # A structured recommendation card comes back (Gemini or deterministic fallback).
    page.wait_for_selector("#recommend-result:not([hidden])", timeout=30000)
    page.wait_for_selector("#recommend-list .reco-card")
    assert page.inner_text("#recommend-list .reco-name").strip() != ""
    assert "tomatoes" in page.inner_text("#recommend-question").lower()


def test_admin_login_shows_portal_with_both_tabs(page, base_url):
    page.goto(base_url + "/admin")
    page.wait_for_selector("#admin-login:not([hidden])")
    assert page.is_hidden("#admin-portal")

    # Demo credentials are shown on the login page (like the demo OTP).
    page.wait_for_function("() => document.getElementById('demo-user').textContent.trim().length > 0")
    page.fill("#admin-username", page.inner_text("#demo-user").strip())
    page.fill("#admin-password", page.inner_text("#demo-pass").strip())
    page.click("#admin-login-btn")

    page.wait_for_selector("#admin-portal:not([hidden])")
    # The login gate must actually hide (regression: [hidden] overridden by CSS).
    assert page.is_hidden("#admin-login")
    assert page.is_visible("#tab-review") and page.is_visible("#tab-disputed")


def test_farmer_session_cannot_open_admin_portal(page, base_url):
    page.goto(base_url + "/admin")
    # Simulate a signed-in FARMER (separate session key) and reload /admin.
    page.evaluate(
        "() => localStorage.setItem('kisanmate_session', JSON.stringify({farmer:{id:'ramesh'}}))"
    )
    page.reload()
    page.wait_for_selector("#admin-login:not([hidden])")
    assert page.is_hidden("#admin-portal")  # a farmer session never reveals the officer portal


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
