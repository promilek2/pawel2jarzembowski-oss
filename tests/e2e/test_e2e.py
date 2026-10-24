"""Testy przeglądarkowe (Playwright + axe-core). Uruchom: pytest -m e2e

Wymagają `pip install -e ".[e2e]"` i przeglądarki Chromium (`playwright install chromium`).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.e2e

playwright_api = pytest.importorskip("playwright.sync_api")
axe_module = pytest.importorskip("axe_playwright_python.sync_playwright")

from tests.e2e.server import running_server  # noqa: E402

MOBILE = {"width": 360, "height": 780}


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_server() as url:
        yield url


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with playwright_api.sync_playwright() as p:
        executable = os.environ.get("TERMIN_CHROMIUM")
        b = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE, locale="pl-PL")
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base_url)
    yield pg
    context.close()
    assert not errors, errors


def search(page, what: str = "okulsta", where: str = "81-310 Gdynia") -> None:  # type: ignore[no-untyped-def]
    page.fill("#co", what)
    page.keyboard.press("Escape")
    page.fill("#gdzie", where)
    page.click("button[type=submit]")


def no_horizontal_scroll(page) -> bool:  # type: ignore[no-untyped-def]
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))


def test_search_flow_with_variant_choice(page) -> None:  # type: ignore[no-untyped-def]
    search(page)
    page.wait_for_selector("#choices:not([hidden])")
    page.get_by_role("button", name="Dla dorosłych").click()
    page.wait_for_selector("#result-list li")
    first = page.locator("#result-list article").first
    assert "03.11.2026" in first.inner_text()
    assert "Dlaczego tu" in first.inner_text()
    assert "stan na" in first.inner_text()
    assert first.get_by_role("link", name="Zadzwoń").get_attribute("href").startswith("tel:+48")
    assert "Znaleziono" in page.locator("#status").inner_text()
    assert "q=okulsta" in page.url  # link do udostępnienia
    assert no_horizontal_scroll(page)


def test_shared_link_runs_search(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE)
    pg = context.new_page()
    pg.goto(f"{base_url}/?q=okulista&gdzie=Gdynia&wariant=dzieci&r=100")
    pg.wait_for_selector("#result-list li")
    assert "dla dzieci" in pg.locator("#results-heading").inner_text()
    context.close()


def test_map_is_optional_and_lazy(page) -> None:  # type: ignore[no-untyped-def]
    assert page.evaluate("typeof window.L") == "undefined"
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.click("#toggle-map")
    page.wait_for_selector(".leaflet-marker-icon")
    assert page.locator("#toggle-map").get_attribute("aria-expanded") == "true"
    assert page.locator("#map").get_attribute("aria-label")
    page.click("#show-stats")
    page.wait_for_selector("#stats table")
    assert "Mediana" in page.locator("#stats").inner_text()


def test_large_text_mode(page) -> None:  # type: ignore[no-untyped-def]
    before = page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")
    page.click("#large-text")
    after = page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")
    assert after >= before * 1.2
    assert page.locator("#large-text").get_attribute("aria-pressed") == "true"
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    assert no_horizontal_scroll(page)
    page.reload()
    assert page.evaluate("document.documentElement.classList.contains('large-text')")


def test_keyboard_navigation(page) -> None:  # type: ignore[no-untyped-def]
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.className") == "skip-link"
    page.keyboard.press("Enter")
    assert page.evaluate("document.activeElement.id") == "co"
    page.keyboard.type("okul")
    page.wait_for_selector("#co-list li")
    page.keyboard.press("ArrowDown")
    assert page.locator("#co").get_attribute("aria-activedescendant") == "sug-0"
    page.keyboard.press("Enter")
    assert page.locator("#co").input_value() == "okulista"
    page.keyboard.press("Tab")  # → pole „Gdzie jesteś?”
    page.keyboard.type("Gdynia")
    page.keyboard.press("Enter")  # wysłanie formularza
    page.wait_for_selector("#choices:not([hidden])")
    assert page.evaluate("document.activeElement.textContent") == "Dla dorosłych"
    page.keyboard.press("Enter")
    page.wait_for_selector("#result-list li")
    # przyciski karty są osiągalne klawiaturą
    page.locator("#result-list article").first.get_by_role("button", name="Obserwuj").focus()
    page.keyboard.press("Enter")
    assert page.locator("#watch-dialog").evaluate("d => d.open")
    page.keyboard.press("Escape")
    assert not page.locator("#watch-dialog").evaluate("d => d.open")


def test_watch_dialog_subscribes(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.locator("#result-list article").first.get_by_role("button", name="Obserwuj").click()
    assert page.locator("#watch-target").input_value().startswith("termin-")
    page.click("#watch-submit")
    # (wait_for_function używa eval, który nasza CSP słusznie blokuje)
    page.wait_for_selector("#watch-result:has-text('/stop/')")


def test_language_switch(page) -> None:  # type: ignore[no-untyped-def]
    page.select_option("#lang", "uk")
    page.wait_for_selector("html[lang=uk]", state="attached")
    assert page.locator("label[for=co]").inner_text() == "Що ви шукаєте?"
    page.select_option("#lang", "en")
    page.wait_for_selector("html[lang=en]", state="attached")
    assert "What are you looking for" in page.locator("label[for=co]").inner_text()


def _axe_violations(page) -> list[str]:  # type: ignore[no-untyped-def]
    results = axe_module.Axe().run(
        page,
        options={
            "runOnly": {"type": "tag", "values": ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]}
        },
    )
    return [f"{v['id']}: {v['help']} ({len(v['nodes'])})" for v in results.response["violations"]]


def test_axe_start_page(page) -> None:  # type: ignore[no-untyped-def]
    assert _axe_violations(page) == []


def test_axe_results_large_text_and_dialog(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.locator("#result-list details.script summary").first.click()
    assert _axe_violations(page) == []
    page.click("#large-text")
    assert _axe_violations(page) == []
    page.locator("#result-list article").first.get_by_role("button", name="Obserwuj").click()
    assert _axe_violations(page) == []


def test_pwa_assets(page, base_url) -> None:  # type: ignore[no-untyped-def]
    manifest = page.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
    assert manifest["display"] == "standalone"
    assert page.evaluate("navigator.serviceWorker.ready.then(r => Boolean(r.active))")
    icons = {i["purpose"] for i in manifest["icons"]}
    assert {"any", "maskable"} <= icons


def test_axe_dark_mode(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE, color_scheme="dark")
    pg = context.new_page()
    pg.goto(base_url)
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    assert _axe_violations(pg) == []
    context.close()


def test_pwa_installable(page) -> None:  # type: ignore[no-untyped-def]
    """Chromium: brak błędów instalowalności (manifest, ikony, service worker)."""
    page.evaluate("navigator.serviceWorker.ready.then(r => Boolean(r.active))")
    cdp = page.context.new_cdp_session(page)
    result = cdp.send("Page.getInstallabilityErrors")
    assert result["installabilityErrors"] == [], result
