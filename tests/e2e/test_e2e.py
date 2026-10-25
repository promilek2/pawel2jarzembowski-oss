"""Testy przeglądarkowe (Playwright + axe-core). Uruchom: pytest -m e2e

Wymagają `pip install -e ".[e2e]"` i przeglądarki Chromium (`playwright install chromium`).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.e2e

playwright_api = pytest.importorskip("playwright.sync_api")
axe_module = pytest.importorskip("axe_playwright_python.sync_playwright")

from tests.e2e.server import running_server  # noqa: E402

MOBILE = {"width": 360, "height": 780}
# Decyzja o pamięci podjęta: baner nie zasłania przycisków w testach (ma osobny test).
NO_BANNER = "try { localStorage.setItem('termin:consent', JSON.stringify('denied')); } catch (e) {}"


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
    context.add_init_script(NO_BANNER)
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
    # od 07.2026 NFZ podaje prognozę (PCUŚ) zamiast pierwszego wolnego terminu
    assert "Prognoza NFZ" in first.inner_text() and "To nie jest wolny termin" in first.inner_text()
    assert "Dlaczego tu" in first.inner_text()
    assert "stan na" in first.inner_text()
    assert first.get_by_role("link", name="Zadzwoń").get_attribute("href").startswith("tel:+48")
    assert "Znaleziono" in page.locator("#status").inner_text()
    assert "q=okulsta" in page.url  # link do udostępnienia
    assert no_horizontal_scroll(page)


def test_shared_link_runs_search(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE)
    context.add_init_script(NO_BANNER)
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
    page.wait_for_selector("#watch-result:has-text('zgod')")  # bez zgody nie zapisujemy
    page.check("#watch-consent")
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
    context.add_init_script(NO_BANNER)
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


def test_storage_banner_consent(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE)
    pg = context.new_page()
    pg.goto(base_url)
    banner = pg.locator("#storage-banner")
    banner.wait_for(state="visible")
    assert _axe_violations(pg) == []
    pg.click("#storage-deny")
    assert banner.is_hidden()
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    # bez zgody nic nie zapisujemy
    assert pg.evaluate("localStorage.getItem('termin:last')") is None
    pg.reload()
    assert pg.locator("#storage-banner").is_hidden()
    pg.evaluate("localStorage.setItem('termin:consent', JSON.stringify('granted'))")
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    assert pg.evaluate("localStorage.getItem('termin:last') !== null")
    context.close()


def test_register_login_account_flow(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#account-btn")
    dialog = page.locator("#auth-dialog")
    assert dialog.evaluate("d => d.open")
    assert _axe_violations(page) == []
    page.click("#tab-register")
    page.fill("#auth-email", "babcia@example.com")
    page.fill("#auth-password", "Kolejka-do-okulisty-7")
    page.click("#auth-submit")
    page.wait_for_selector("#auth-error:not([hidden])")  # bez akceptacji regulaminu
    page.check("#auth-terms")
    page.click("#auth-submit")
    page.wait_for_selector("#account-label:has-text('Konto')")
    assert not dialog.evaluate("d => d.open")
    # po rejestracji: kody odzyskiwania do zapisania
    page.wait_for_selector("#codes-dialog[open]")
    assert page.locator("#codes-list li").count() == 10
    assert _axe_violations(page) == []
    page.locator("#codes-dialog").get_by_role("button", name="Zapisałem/zapisałam kody").click()
    # obserwacja zapisana na koncie
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.locator("#result-list article").first.get_by_role("button", name="Obserwuj").click()
    page.check("#watch-consent")
    page.click("#watch-submit")
    page.wait_for_selector("#watch-result:has-text('/stop/')")
    page.keyboard.press("Escape")
    page.click("#account-btn")
    page.wait_for_selector("#account-subs li button")
    assert "babcia@example.com" in page.locator("#account-email").inner_text()
    assert _axe_violations(page) == []
    page.click("#account-logout")
    page.wait_for_selector("#account-label:has-text('Zaloguj')")
    # ponowne logowanie
    page.click("#account-btn")
    page.fill("#auth-email", "babcia@example.com")
    page.fill("#auth-password", "Kolejka-do-okulisty-7")
    page.click("#auth-submit")
    page.wait_for_selector("#account-label:has-text('Konto')")


def _register(page, email: str) -> list[str]:  # type: ignore[no-untyped-def]
    page.click("#account-btn")
    page.click("#tab-register")
    page.fill("#auth-email", email)
    page.fill("#auth-password", "Kolejka-do-okulisty-7")
    page.check("#auth-terms")
    page.click("#auth-submit")
    page.wait_for_selector("#codes-dialog[open]")
    codes = [c.strip() for c in page.locator("#codes-list li").all_inner_texts()]
    page.click("#codes-dialog [data-close].btn")
    return codes


def test_forgot_password_with_recovery_code(page) -> None:  # type: ignore[no-untyped-def]
    codes = _register(page, "dziadek@example.com")
    page.click("#account-btn")
    page.click("#account-logout")
    page.wait_for_selector("#account-label:has-text('Zaloguj')")
    page.click("#account-btn")
    page.click("#auth-forgot")  # bez poczty od razu formularz z kodem
    page.wait_for_selector("#auth-code-field:not([hidden])")
    assert _axe_violations(page) == []
    page.fill("#auth-email", "dziadek@example.com")
    page.fill("#auth-code", codes[0].lower())
    page.fill("#auth-password", "Nowe-haslo-do-kolejki-9")
    page.wait_for_selector(".strength[data-level='3']")
    page.click("#auth-submit")
    page.wait_for_selector("#account-label:has-text('Konto')")
    page.click("#account-btn")
    page.wait_for_selector("#security-codes:has-text('9 z 10')")
    page.click("#sessions-details summary")
    page.wait_for_selector("#account-sessions li")
    assert "To urządzenie" in page.locator("#account-sessions").inner_text()


def test_two_factor_setup_and_login(page) -> None:  # type: ignore[no-untyped-def]
    import time

    from termin.security import totp_now

    _register(page, "wnuczka@example.com")
    page.click("#account-btn")
    page.click("#twofa-details summary")
    page.fill("#twofa-password", "Kolejka-do-okulisty-7")
    page.click("#twofa-start button[type=submit]")
    page.wait_for_selector("#twofa-setup:not([hidden])")
    assert page.locator("#twofa-link").get_attribute("href").startswith("otpauth://totp/")  # type: ignore[union-attr]
    assert _axe_violations(page) == []
    secret = page.locator("#twofa-secret").inner_text().replace(" ", "")
    page.fill("#twofa-code", totp_now(secret, time.time()))
    page.click("#twofa-confirm button[type=submit]")
    page.wait_for_selector("#security-2fa[data-on='true']")
    page.click("#account-logout")
    page.wait_for_selector("#account-label:has-text('Zaloguj')")
    page.click("#account-btn")
    page.fill("#auth-email", "wnuczka@example.com")
    page.fill("#auth-password", "Kolejka-do-okulisty-7")
    page.click("#auth-submit")
    page.wait_for_selector("#auth-code-field:not([hidden])")
    assert page.locator("#account-label").inner_text() == "Zaloguj"
    page.fill("#auth-code", totp_now(secret, time.time() + 30))  # następny kod (poprzedni zużyty)
    page.click("#auth-submit")
    page.wait_for_selector("#account-label:has-text('Konto')")


def test_saved_search_filter_print_and_call_mode(page) -> None:  # type: ignore[no-untyped-def]
    page.click(".more summary")
    page.check("#accessible")
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    assert "dostepne=true" in page.url
    assert "Dojazd:" in page.locator("#result-list").inner_text()
    # zapisane wyszukiwanie wraca na stronę startową
    page.click("#save-search")
    page.wait_for_selector("#saved-searches:not([hidden]) .saved-chip")
    assert "Okulista" in page.locator("#saved-list").inner_text()
    # lista do druku (bez okna drukowania w teście)
    page.evaluate("() => { window.print = () => { document.body.dataset.printed = '1'; }; }")
    page.click("#print-list")
    page.wait_for_selector("body[data-printed='1']")
    assert page.locator("#print-sheet tbody tr").count() >= 1
    # tryb „Dzwonię”
    card = page.locator("#result-list article").first
    card.locator(".script summary").click()
    card.get_by_role("button", name="Tryb rozmowy (duże litery)").click()
    page.wait_for_selector("#call-dialog[open] .call-number")
    assert "Co powiedzieć" in page.locator("#call-dialog").inner_text()
    assert _axe_violations(page) == []
    page.locator("#call-dialog").get_by_role("button", name="Sprawdziłem telefonicznie").click()
    page.wait_for_selector("#auth-dialog[open]")  # bez konta: logowanie


def test_report_prompt_after_call(page) -> None:  # type: ignore[no-untyped-def]
    page.clock.install()
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    link = page.locator("#result-list article").first.locator("a.call")
    link.evaluate("a => a.addEventListener('click', e => e.preventDefault())")  # bez tel:
    link.click()
    page.clock.fast_forward(10_000)  # „rozmowa” trwała 10 s
    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector("#toast:not([hidden])")
    assert "Jak poszła rozmowa" in page.locator("#toast").inner_text()
    assert _axe_violations(page) == []
    page.locator("#toast").get_by_role("button", name="Sprawdziłem telefonicznie").click()
    page.wait_for_selector("#auth-dialog[open]")  # bez konta: logowanie


def test_personal_features_need_login(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.locator("#result-list article").first.get_by_role(
        "button", name="Sprawdziłem telefonicznie"
    ).click()
    page.wait_for_selector("#auth-dialog[open]")
    assert "po zalogowaniu" in page.locator("#auth-status").inner_text()


def test_phone_report_cases_and_notes(page) -> None:  # type: ignore[no-untyped-def]
    _register(page, "corka@example.com")
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    card = page.locator("#result-list article").first
    # „Sprawdziłem telefonicznie” → zgoda na dane o zdrowiu → zgłoszenie
    card.get_by_role("button", name="Sprawdziłem telefonicznie").click()
    page.wait_for_selector("#report-dialog[open]")
    page.locator("#report-dialog label", has_text="Tak").first.click()
    page.locator("#report-dialog label", has_text="Inny termin").click()
    page.fill("#report-date", "2026-12-01")
    page.check("#report-case")
    assert _axe_violations(page) == []
    page.click("#report-form button[type=submit]")
    page.wait_for_selector("#health-dialog[open]")
    assert _axe_violations(page) == []
    page.check("#health-check")
    page.click("#health-accept")
    page.wait_for_selector(".community:not([hidden])")
    assert "Sprawdzone telefonicznie" in card.locator(".community").inner_text()
    # prywatna notatka
    card.get_by_role("button", name="Notatka").click()
    card.locator("textarea").fill("dzwonić po 7:30")
    card.get_by_role("button", name="Zapisz notatkę").click()
    page.wait_for_selector(".my-note:has-text('dzwonić po 7:30')")
    # „Moje sprawy”: wizyta z formularza zgłoszenia
    page.click("#account-btn")
    page.click("#account-cases")
    page.wait_for_selector("#cases-dialog[open] .case")
    assert page.locator("#cases-list select").first.input_value() == "booked"
    assert page.locator("#cases-list input[type=date]").first.input_value() == "2026-12-01"
    assert _axe_violations(page) == []
    page.fill("#case-new-label", "Kardiolog, skierowanie od dr. Nowaka")
    page.click("#case-new button[type=submit]")
    page.wait_for_selector(".case:has-text('Kardiolog')")
    page.locator(".case", has_text="Kardiolog").locator("select").select_option("waiting")
    page.wait_for_selector(".case--waiting")


def test_install_dialog_and_legal_pages(page, base_url) -> None:  # type: ignore[no-untyped-def]
    page.evaluate("document.querySelector('#install-dialog').showModal()")
    assert "Do ekranu początkowego" in page.locator("#install-dialog").inner_text()
    assert _axe_violations(page) == []
    page.keyboard.press("Escape")
    for path in ("/regulamin", "/prywatnosc", "/cookies", "/dostepnosc", "/kontakt"):
        page.goto(base_url + path)
        assert page.locator("h1").first.inner_text()
        assert _axe_violations(page) == [], path


def test_help_chat_faq_mode(page) -> None:  # type: ignore[no-untyped-def]
    page.wait_for_selector("#help-btn:not([hidden])")
    page.click("#help-btn")
    page.wait_for_selector("#chat-panel[open] #chat-input")
    assert page.locator("#help-btn").get_attribute("aria-expanded") == "true"
    assert "wbudowanej pomocy" in page.locator("#chat-intro").inner_text()
    page.locator("#chat-suggest").get_by_role("button", name="Jak zainstalować aplikację?").click()
    page.wait_for_selector("#chat-log .chat-assistant:not(.chat-busy)")
    assert "Zainstaluj" in page.locator("#chat-log .chat-assistant").last.inner_text()
    assert page.locator("#chat-suggest").is_hidden()
    page.fill("#chat-input", "zapomniałem hasła")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-log .chat-assistant:nth-of-type(4)")
    assert "Nie pamiętam hasła" in page.locator("#chat-log").inner_text()
    assert no_horizontal_scroll(page)
    assert _axe_violations(page) == []
    page.keyboard.press("Escape")
    page.wait_for_selector("#chat-panel", state="hidden")
    assert page.evaluate("() => document.activeElement.id") == "help-btn"


def test_help_chat_ai_notice(page) -> None:  # type: ignore[no-untyped-def]
    reply = {"reply": "Kliknij „Pokaż mapę” nad wynikami.", "mode": "ai", "topic": None}
    info = {"enabled": True, "ai": True}
    page.route(
        "**/api/assistant",
        lambda route: route.fulfill(json=info if route.request.method == "GET" else reply),
    )
    page.click("#help-btn")
    page.wait_for_selector("#chat-notice:not([hidden])")
    assert page.locator("#chat-form").is_hidden()
    assert "Anthropic" in page.locator("#chat-notice").inner_text()
    assert _axe_violations(page) == []
    page.get_by_role("button", name="Rozumiem").click()
    page.fill("#chat-input", "gdzie mapa?")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-log .chat-assistant:not(.chat-busy)")
    assert "Pokaż mapę" in page.locator("#chat-log").inner_text()


def test_results_tip_points_to_faster_place(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    tip = page.locator("#results-tip")
    assert "Najkrótsza prognoza w okolicy" in tip.inner_text()
    assert "GDYNIA" not in tip.inner_text()
    tip.get_by_role("button").click()
    assert page.evaluate("() => document.activeElement.id").startswith("r-")
    assert _axe_violations(page) == []


def test_desktop_copy_phone_and_help_shortcut(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    context.add_init_script(NO_BANNER)
    pg = context.new_page()
    pg.goto(base_url)
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    pg.locator(".copy-phone").first.click()
    pg.wait_for_selector("#status:has-text('Skopiowano numer')")
    assert pg.evaluate("() => navigator.clipboard.readText()").strip()
    pg.goto(f"{base_url}/?pomoc=1")
    pg.wait_for_selector("#chat-panel[open]")
    context.close()


def test_touch_phone_has_no_copy_button(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE, has_touch=True, is_mobile=True)
    context.add_init_script(NO_BANNER)
    pg = context.new_page()
    pg.goto(base_url)
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    assert pg.locator(".copy-phone").count() == 0
    assert pg.locator("#result-list a.call").count() >= 1
    context.close()


def test_help_chat_in_ukrainian(page) -> None:  # type: ignore[no-untyped-def]
    page.select_option("#lang", "uk")
    page.wait_for_selector("html[lang=uk]", state="attached")
    page.click("#help-btn")
    page.wait_for_selector("#chat-panel[open]")
    assert page.locator("#chat-title").inner_text() == "Як це працює?"
    page.locator("#chat-suggest button").nth(1).click()
    page.wait_for_selector("#chat-log .chat-assistant:not(.chat-busy)")
    assert "Встановити" in page.locator("#chat-log .chat-assistant").last.inner_text()


def test_help_chat_suggestions_follow_results(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.click("#help-btn")
    page.wait_for_selector("#chat-panel[open] #chat-suggest button")
    first = page.locator("#chat-suggest button").first.inner_text()
    assert "IKP" in first or "przybliżona" in first


def test_send_list_copies_short_text(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    context.add_init_script(NO_BANNER + " try { delete Navigator.prototype.share; } catch (e) {}")
    pg = context.new_page()
    pg.goto(base_url)
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    pg.click("#send-list")
    pg.wait_for_selector("#status:has-text('Lista skopiowana')")
    text = pg.evaluate("() => navigator.clipboard.readText()")
    assert text.startswith("Termin: Okulista")
    assert "1. " in text and "tel. " in text and "5. " in text and "6. " not in text
    assert "prognoza NFZ" in text and base_url in text
    context.close()


def test_theme_switch_persists(page) -> None:  # type: ignore[no-untyped-def]
    page.select_option("#theme", "dark")
    assert page.evaluate("() => document.documentElement.dataset.theme") == "dark"
    bg = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
    page.reload()
    page.wait_for_selector("html[data-theme=dark]", state="attached")
    assert page.locator("#theme").input_value() == "dark"
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    assert _axe_violations(page) == []
    page.select_option("#theme", "auto")
    assert page.evaluate("() => document.documentElement.dataset.theme") is None
    assert page.evaluate("() => getComputedStyle(document.body).backgroundColor") != bg


def test_slash_focuses_search(page) -> None:  # type: ignore[no-untyped-def]
    page.locator("body").click(position={"x": 5, "y": 300})
    page.keyboard.press("/")
    assert page.evaluate("() => document.activeElement.id") == "co"
    assert page.locator("#co").input_value() == ""


def test_show_more_results(page) -> None:  # type: ignore[no-untyped-def]
    def small_first_page(route) -> None:  # type: ignore[no-untyped-def]
        url = route.request.url
        route.continue_(url=url if "limit=" in url else url + "&limit=10")

    page.route("**/api/search?*", small_first_page)
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    assert page.locator("#result-list > li").count() == 10
    more = page.locator("#results-more button")
    assert more.inner_text().startswith("Pokaż jeszcze")
    more.click()
    page.wait_for_selector("#result-list > li:nth-child(11)")
    assert page.evaluate(
        "() => document.activeElement.closest('li') === "
        "document.querySelector('#result-list > li:nth-child(11)')"
    )
    assert page.locator("#results-more").is_hidden()


def test_nfz_down_suggests_official_search(page) -> None:  # type: ignore[no-untyped-def]
    body = {"detail": "API NFZ jest niedostępne (timeout).", "code": "nfz"}
    page.route("**/api/search?*", lambda route: route.fulfill(status=502, json=body))
    search(page, "okulista dorośli")
    page.wait_for_selector("#form-error:not([hidden]) a")
    assert "nie odpowiada" in page.locator("#form-error").inner_text()
    assert "nfz.gov.pl" in page.locator("#form-error a").get_attribute("href")


def test_quick_filters_hide_results_without_new_search(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#results-filters:not([hidden]) .chip")
    total = page.locator("#result-list > li").count()
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url) if "/api/search" in r.url else None)
    chips = page.locator("#results-filters .chip")
    chips.first.click()
    assert chips.first.get_attribute("aria-pressed") == "true"
    visible = page.locator("#result-list > li:visible").count()
    assert 0 < visible < total
    assert f"{visible} z {total}" in page.locator(".filters-count").inner_text()
    assert _axe_violations(page) == []
    chips.first.click()
    assert page.locator("#result-list > li:visible").count() == total
    assert requests == []


FAKE_TTS = """
window.__spoken = [];
class FakeUtterance { constructor(text) { this.text = text; } }
const voices = [{ localService: false, lang: "pl-PL" }, { localService: true, lang: "pl-PL" }];
Object.defineProperty(window, "SpeechSynthesisUtterance", { value: FakeUtterance });
Object.defineProperty(window, "speechSynthesis", { value: {
  speaking: false, getVoices: () => voices, addEventListener() {}, cancel() {},
  speak(u) { window.__spoken.push({ text: u.text, local: u.voice.localService }); },
} });
"""


def test_call_mode_reads_aloud_with_local_voice(browser, base_url) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport=MOBILE)
    context.add_init_script(NO_BANNER + FAKE_TTS)
    pg = context.new_page()
    pg.goto(base_url)
    search(pg, "okulista dorośli")
    pg.wait_for_selector("#result-list li")
    card = pg.locator("#result-list article").first
    card.locator(".script summary").click()
    card.get_by_role("button", name="Tryb rozmowy (duże litery)").click()
    pg.get_by_role("button", name="Przeczytaj na głos").click()
    spoken = pg.evaluate("() => window.__spoken")
    assert len(spoken) == 1 and spoken[0]["local"] is True
    assert "prognoza NFZ" in spoken[0]["text"] and "0 0" in spoken[0]["text"]
    context.close()


def test_help_chat_show_me_opens_the_right_place(page) -> None:  # type: ignore[no-untyped-def]
    page.click("#help-btn")
    page.fill("#chat-input", "jak zainstalować aplikację?")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-log .chat-show")
    page.click("#chat-log .chat-show")
    page.wait_for_selector("#install-dialog[open]")
    assert page.locator("#chat-panel").is_hidden()
    page.keyboard.press("Escape")
    page.click("#help-btn")
    page.fill("#chat-input", "gdzie filtr bez barier?")
    page.press("#chat-input", "Enter")
    page.wait_for_selector("#chat-log .chat-assistant:nth-of-type(4) .chat-show")
    page.locator("#chat-log .chat-show").last.click()
    assert page.evaluate("() => document.activeElement.id") == "accessible"


def test_restore_cases_from_backup_file(page) -> None:  # type: ignore[no-untyped-def]
    _register(page, "kopia@example.com")
    page.click("#account-btn")
    page.wait_for_selector("#account-dialog[open] #account-restore")
    backup = {"personal": {"cases": [{"label": "Ortopeda", "visit_date": "2026-12-10"}],
                           "facility_notes": []}}  # fmt: skip
    raw = json.dumps(backup).encode()
    file = {"name": "termin.json", "mimeType": "application/json", "buffer": raw}
    page.locator("#account-restore + input[type=file]").set_input_files(files=[file])
    page.wait_for_selector("#health-dialog[open]")
    page.check("#health-check")
    page.click("#health-accept")
    page.wait_for_selector("#account-status:has-text('Przywrócono: spraw 1')")
    page.click("#account-cases")
    page.wait_for_selector("#cases-dialog[open] .case")
    assert "Ortopeda" in page.locator("#cases-list").inner_text()


def test_saved_search_shows_changes_since_last_check(page) -> None:  # type: ignore[no-untyped-def]
    search(page, "okulista dorośli")
    page.wait_for_selector("#result-list li")
    page.click("#save-search")
    page.wait_for_selector("#saved-searches:not([hidden]) .saved-chip")
    # udajemy, że przy poprzednim sprawdzeniu pierwsza placówka miała prognozę o 10 dni dłuższą
    page.evaluate("""() => {
      const saved = JSON.parse(localStorage.getItem('termin:saved'));
      const [id, snap] = Object.entries(saved[0].snap)[0];
      snap.d += 10; snap.f = null; snap.p = true;
      saved[0].snapAt = '2026-10-01';
      localStorage.setItem('termin:saved', JSON.stringify(saved));
      window.__changed = id;
    }""")
    page.locator("#saved-list .chip").first.click()
    page.wait_for_selector(".change--better")
    assert "krócej o 10 dni" in page.locator(".change--better").first.inner_text()
    assert page.locator(".change").count() == 1
