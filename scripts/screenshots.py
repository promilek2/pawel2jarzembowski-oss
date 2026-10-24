"""Zrzuty ekranu do README z serwera testowego (dane fikcyjne z tests/fixtures).

Użycie: python scripts/screenshots.py   (wymaga .[e2e] i Chromium)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from tests.e2e.server import running_server  # noqa: E402

OUT = ROOT / "docs" / "img"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with running_server() as url, sync_playwright() as p:
        exe = os.environ.get("TERMIN_CHROMIUM")
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page.goto(f"{url}/?q=okulsta&gdzie=81-310%20Gdynia&r=30&wariant=dorosli")
        page.wait_for_selector("#result-list li")
        page.evaluate("document.querySelector('#results-heading').scrollIntoView()")
        page.screenshot(path=OUT / "telefon-wyniki.png")
        page.locator("#result-list details.script summary").first.click()
        page.evaluate("document.querySelector('#result-list details.script').scrollIntoView()")
        page.screenshot(path=OUT / "telefon-skrypt.png")

        desk = browser.new_page(viewport={"width": 1280, "height": 900})
        desk.goto(f"{url}/?q=okulista&gdzie=Gdynia&r=50&wariant=dorosli")
        desk.wait_for_selector("#result-list li")
        desk.click("#show-stats")
        desk.wait_for_selector("#stats table")
        desk.evaluate("document.querySelector('#results-heading').scrollIntoView()")
        desk.screenshot(path=OUT / "komputer-statystyki.png")
        browser.close()
    print("zapisano zrzuty w", OUT)


if __name__ == "__main__":
    main()
