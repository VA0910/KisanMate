"""End-to-end browser test of the phone + OTP sign-in flow.

Drives the real SPA in a headless Chromium to prove the four acceptance cases:
  1. brand-new phone  -> Welcome -> language -> phone -> OTP -> profile setup
  2. Ramesh's phone   -> straight to home, greeted as Ramesh
  3. sign-out         -> returns to Welcome
  4. reload           -> stays signed in (localStorage session)

This is intentionally environment-gated so the normal `pytest` run stays green
on machines without a browser or Firestore:
  - skips if Playwright isn't installed,
  - skips if Chromium can't launch,
  - skips if Firestore (which the auth endpoints need) isn't reachable.

Run it after `pip install playwright && playwright install chromium` on a machine
with Firestore credentials:  pytest tests/test_auth_flow_e2e.py -v
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


@pytest.fixture(scope="module")
def base_url():
    if not _firestore_reachable():
        pytest.skip("Firestore not reachable; the auth endpoints need it")
    # Ensure the demo farmers (with phones) exist.
    import seed
    seed.seed()

    port = _free_port()
    proc = subprocess.Popen(
        [os.sys.executable, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    url = f"http://127.0.0.1:{port}"
    try:
        # Wait for the server to accept connections.
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
        # Clean up any farmer docs the new-phone case created.
        try:
            for f in firestore_client.list_farmers():
                if f.id not in SEEDED:
                    firestore_client.get_client().collection("farmers").document(f.id).delete()
        except Exception:
            pass


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
    context = browser.new_context()  # fresh localStorage per test
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


def test_new_phone_reaches_profile_setup(page, base_url):
    _onboard_to_phone(page, base_url)
    # A phone unlikely to collide with anything seeded.
    page.fill("#phone-input", "9123456780")
    page.click("#send-otp-btn")
    page.wait_for_selector("#screen-otp:not([hidden])")

    code = page.inner_text("#demo-otp-code").strip()
    assert len(code) == 4 and code.isdigit()

    page.fill("#otp-input", code)
    page.click("#verify-otp-btn")
    page.wait_for_selector("#screen-profile:not([hidden])")


def test_ramesh_phone_goes_straight_home(page, base_url):
    _onboard_to_phone(page, base_url)
    page.fill("#phone-input", RAMESH_PHONE)
    page.click("#send-otp-btn")
    page.wait_for_selector("#screen-otp:not([hidden])")
    code = page.inner_text("#demo-otp-code").strip()
    page.fill("#otp-input", code)
    page.click("#verify-otp-btn")

    page.wait_for_selector("#screen-home:not([hidden])")
    assert "Ramesh" in page.inner_text("#home-heading")


def test_sign_out_returns_to_welcome(page, base_url):
    _onboard_to_phone(page, base_url)
    page.fill("#phone-input", RAMESH_PHONE)
    page.click("#send-otp-btn")
    page.wait_for_selector("#screen-otp:not([hidden])")
    page.fill("#otp-input", page.inner_text("#demo-otp-code").strip())
    page.click("#verify-otp-btn")
    page.wait_for_selector("#screen-home:not([hidden])")

    page.click("#sign-out-btn")
    page.wait_for_selector("#screen-welcome:not([hidden])")
    assert page.evaluate("() => localStorage.getItem('kisanmate_session')") is None


def test_reload_keeps_signed_in(page, base_url):
    _onboard_to_phone(page, base_url)
    page.fill("#phone-input", RAMESH_PHONE)
    page.click("#send-otp-btn")
    page.wait_for_selector("#screen-otp:not([hidden])")
    page.fill("#otp-input", page.inner_text("#demo-otp-code").strip())
    page.click("#verify-otp-btn")
    page.wait_for_selector("#screen-home:not([hidden])")

    page.reload()
    page.wait_for_selector("#screen-home:not([hidden])")
    assert "Ramesh" in page.inner_text("#home-heading")
