"""Real-browser checks of the landing page and dashboard (needs playwright + chromium; skipped without them).

Runs against the local server over the bundled fixture, and over the saved real scans through the
same api/index.py entry Vercel uses, so the CSP and the hosted labels are tested as deployed.
"""

import importlib.util
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from ryo_pulse.dashboard import serve  # noqa: E402

expect = sync_api.expect
# wait_for_function evaluates a string, which the app's CSP (no unsafe-eval) rightly blocks; use locators
from ryo_pulse.store import load_fixture  # noqa: E402

ROOT = Path(__file__).parent.parent.parent


def _run(httpd):
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}"


@pytest.fixture(scope="module")
def local_url():
    httpd = serve(lambda: load_fixture(ROOT / "tests/fixtures/demo_window.json"), port=0)
    yield _run(httpd)
    httpd.shutdown()


@pytest.fixture(scope="module")
def hosted_url():
    spec = importlib.util.spec_from_file_location("vercel_index_e2e", ROOT / "api" / "index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mod.handler)
    yield _run(httpd)
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def problems():
    return []


@pytest.fixture
def page(browser, problems):
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type in ("error", "warning") and problems.append(m.text))
    pg.on("pageerror", lambda e: problems.append(str(e)))
    yield pg
    ctx.close()


def test_landing_loads_without_errors_or_csp_violations(page, problems, local_url):
    page.goto(local_url + "/")
    page.wait_for_selector("#strips .strip-row")
    assert page.locator("#stats .stat").count() == 4
    assert page.locator("#strips .strip-row").count() == 5
    page.get_by_role("link", name="Open the dashboard").first.click()
    page.wait_for_url("**/board")
    page.wait_for_selector("#rows tr")
    assert problems == []


def test_dashboard_filters_search_details_and_settings(page, problems, local_url):
    page.goto(local_url + "/board")
    page.wait_for_selector("#rows .rowbtn")
    assert page.locator("#rows .rowbtn").count() == 5
    assert "fixture data" in page.locator("#source").inner_text().lower()

    # status filter buttons
    page.locator("#filterBtns button", has_text="persistent").click()
    assert sorted(page.locator("#rows .rowbtn").all_inner_texts()) == ["BTC", "SOL"]
    page.locator("#filterBtns button", has_text="All").click()

    # search box
    page.fill("#q", "pe")
    assert page.locator("#rows .rowbtn").all_inner_texts() == ["PEPE"]
    page.fill("#q", "")

    # expand a row's details
    btc = page.locator("#rows .rowbtn", has_text="BTC")
    btc.click()
    assert page.locator("#rows .rowbtn", has_text="BTC").get_attribute("aria-expanded") == "true"
    assert "replay_hash" in page.locator("tr.detail").inner_text()

    # settings: watch a token, then reset
    page.click("#openSettings")
    page.fill("input[name=watch]", "doge")
    page.locator("#settings button[type=submit]").click()
    expect(page.locator("#rows .rowbtn").first).to_have_text("DOGE")
    page.reload()
    expect(page.locator("#rows .rowbtn").first).to_have_text("DOGE")  # remembered
    page.click("#openSettings")
    page.click("#reset")
    page.click("#closeSettings")
    expect(page.locator("#rows .rowbtn").first).not_to_have_text("DOGE")

    # invalid setting gives a readable error, not a blank page
    page.click("#openSettings")
    page.fill("input[name=watch]", ",".join(f"T{i}" for i in range(60)))
    page.locator("#settings button[type=submit]").click()
    page.wait_for_selector("#alert:not([hidden])")
    assert "at most" in page.locator("#alertText").inner_text()
    assert [p for p in problems if "400" not in p] == []


def test_dashboard_keyboard_shortcuts(page, local_url):
    page.goto(local_url + "/board")
    page.wait_for_selector("#rows .rowbtn")
    page.keyboard.press("2")  # persistent
    assert page.locator("#rows .rowbtn").count() == 2
    page.keyboard.press("0")
    assert page.locator("#rows .rowbtn").count() == 5
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "q"


def test_api_down_shows_error(page, local_url):
    page.route("**/api/board*", lambda route: route.abort())
    page.goto(local_url + "/board")
    page.wait_for_selector("#alert:not([hidden])")
    assert "Could not load" in page.locator("#alertText").inner_text()


def test_hosted_copy_labels_and_phone_layout(browser, hosted_url):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True)
    page = ctx.new_page()
    problems = []
    page.on("console", lambda m: m.type == "error" and problems.append(m.text))
    page.on("pageerror", lambda e: problems.append(str(e)))
    for path in ("/", "/board"):
        page.goto(hosted_url + path)
        page.wait_for_load_state("networkidle")
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{path} scrolls sideways by {overflow}px on a phone"
    assert "saved copy" in page.locator("#source").inner_text().lower()
    assert page.locator("#rows .rowbtn").count() > 0
    assert problems == []
    ctx.close()


def test_page_cannot_be_framed(page, local_url):
    page.set_content(f'<iframe id="f" src="{local_url}/board"></iframe>')
    page.wait_for_timeout(1500)
    frame = page.frame_locator("#f")
    assert frame.locator("#rows").count() == 0
