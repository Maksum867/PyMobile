"""Real Chromium smoke tests (requires playwright and its Chromium runtime)."""

import threading
from pathlib import Path

import pytest
from PIL import Image as PILImage

from pymobile import (
    App,
    BottomNavigation,
    Button,
    Chip,
    Column,
    Dialog,
    Divider,
    Image,
    Label,
    Screen,
    ScrollView,
    SegmentedButtons,
    Style,
    TextInput,
    Theme,
)
from pymobile.core.bridge import WebBridge
from pymobile.core.ui.web import WebPreview

# Browser integration is optional in a basic development installation.
_playwright = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed")
expect, sync_playwright = _playwright.expect, _playwright.sync_playwright


def launch_chromium(playwright):
    if not Path(playwright.chromium.executable_path).is_file():
        pytest.skip("Chromium is not installed; run playwright install chromium")
    try:
        return playwright.chromium.launch(args=["--no-sandbox"])
    except _playwright.Error as error:
        if any(
            message in str(error)
            for message in (
                "Host system is missing dependencies",
                "error while loading shared libraries",
            )
        ):
            pytest.skip(
                "Chromium system dependencies are missing; run playwright install-deps chromium"
            )
        raise


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = launch_chromium(playwright)
        try:
            yield browser
        finally:
            browser.close()


class Demo(Screen):
    def build(self):
        self.chip = Chip(
            "Choice", selected=True, on_press=lambda: self.chip.set_selected(not self.chip.selected)
        )
        self.dialog = Dialog(
            Label("Modal content"),
            Button("Close", on_press=lambda: self.dialog.close()),
            title="Details",
        )
        self.sheet = Dialog(Label("Sheet content"), title="Sheet", sheet=True)
        return Column(
            Button("Go", id="go"),
            self.chip,
            SegmentedButtons([("one", "One"), ("two", "Two")], value="one"),
            BottomNavigation([("home", "Home"), ("settings", "Settings")], value="home"),
            Image("assets/logo.png", style=Style(width=32, height=32)),
            Divider(vertical=True, thickness=3, inset=7, color="#80123456"),
            Button("Open dialog", on_press=self.dialog.open),
            Button("Open sheet", on_press=self.sheet.open),
            ScrollView(self.dialog, self.sheet, style=Style(height=60)),
        )


@pytest.fixture
def live(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    PILImage.new("RGB", (4, 3), "red").save(assets / "logo.png")
    bridge = WebBridge(verbose=False)
    app = App("Browser audit", bridge=bridge, theme=Theme("mint", {"PRIMARY": "#007755"}))
    screen = Demo()
    app.run(screen)
    preview = WebPreview(app, port=0, token="audit-test-token", assets=assets)
    bridge.attach(preview)
    server = preview._build_server()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield app, screen, preview, f"http://127.0.0.1:{preview.port}"
    finally:
        preview.stop()
        worker.join(2)
        app.stop()


def test_browser_theme_updates_selection_and_assets(browser, live):
    app, _screen, _preview, base = live
    page = browser.new_page(viewport={"width": 800, "height": 1000})
    errors, requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    try:
        page.goto(base + "/?t=audit-test-token")
        expect(page.get_by_role("button", name="Go", exact=True)).to_have_css(
            "background-color", "rgb(0, 119, 85)"
        )
        expect(page.get_by_role("button", name="Choice", exact=True)).to_have_css(
            "background-color", "rgb(0, 119, 85)"
        )
        expect(page.get_by_role("button", name="One", exact=True)).to_have_css(
            "background-color", "rgb(0, 119, 85)"
        )
        expect(page.get_by_role("button", name="Home", exact=True)).to_have_css(
            "background-color", "rgb(0, 119, 85)"
        )
        page.wait_for_function("document.querySelector('img').naturalWidth === 4")
        assert not any("/assets/" in url for url in requests)
        app.set_theme(
            Theme(
                "dark",
                {
                    "PRIMARY": "#CCDDFF",
                    "SURFACE": "#222222",
                    "BACKGROUND": "#121212",
                    "TEXT": "#FFFFFF",
                },
            )
        )
        expect(page.get_by_role("button", name="Go", exact=True)).to_have_css(
            "background-color", "rgb(204, 221, 255)"
        )
        expect(page.get_by_role("button", name="Go", exact=True)).to_have_css(
            "color", "rgb(0, 0, 0)"
        )
        page.get_by_role("button", name="Choice", exact=True).click()
        expect(page.get_by_role("button", name="Choice", exact=True)).to_have_css(
            "background-color", "rgb(34, 34, 34)"
        )
        page.get_by_role("button", name="Two", exact=True).click()
        expect(page.get_by_role("button", name="Two", exact=True)).to_have_css(
            "background-color", "rgb(204, 221, 255)"
        )
        expect(page.get_by_role("button", name="One", exact=True)).to_have_css(
            "background-color", "rgb(34, 34, 34)"
        )
        expect(page.get_by_role("separator")).to_have_css(
            "background-color", "rgba(18, 52, 86, 0.5)"
        )
        assert not errors
    finally:
        page.close()


def test_browser_dialog_overlays_scroll_container_and_traps_focus(browser, live, tmp_path):
    _app, screen, _preview, base = live
    page = browser.new_page(viewport={"width": 800, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(base + "/?t=audit-test-token")
        page.get_by_role("button", name="Open dialog", exact=True).click()
        dialog = page.get_by_role("dialog", name="Details", exact=True)
        expect(dialog).to_be_visible()
        assert page.locator("#screen").evaluate("el => el.inert")
        assert page.locator("#modals > .modal-overlay").count() == 1
        phone = page.locator(".phone").bounding_box()
        overlay = page.locator("#modals > .modal-overlay").bounding_box()
        assert abs(phone["height"] - overlay["height"]) < 2
        expect(page.get_by_role("button", name="Close", exact=True)).to_be_focused()
        page.keyboard.press("Tab")
        expect(page.get_by_role("button", name="Close", exact=True)).to_be_focused()
        page.screenshot(path=str(tmp_path / "modal-browser.png"))
        page.keyboard.press("Escape")
        expect(dialog).to_have_count(0)
        assert not screen.dialog.visible
        assert not page.locator("#screen").evaluate("el => el.inert")
        page.get_by_role("button", name="Open sheet", exact=True).click()
        sheet = page.get_by_role("dialog", name="Sheet", exact=True)
        expect(sheet).to_be_visible()
        bounds = sheet.bounding_box()
        assert abs(bounds["y"] + bounds["height"] - phone["y"] - phone["height"]) < 2
        page.locator("#modals > .modal-overlay").click(position={"x": 60, "y": 60})
        expect(sheet).to_have_count(0)
        assert not screen.sheet.visible and not errors
    finally:
        page.close()


def test_browser_asset_data_is_not_available_without_auth(browser, live):
    _app, _screen, _preview, base = live
    context = browser.new_context()
    request = context.request
    try:
        assert request.get(base + "/state").status == 403
        response = request.get(base + "/state?t=audit-test-token")
        assert response.status == 200 and "data:image/png;base64," in response.json()["body"]
        assert request.get(base + "/assets/logo.png?t=audit-test-token").status == 404
        assert (
            request.get(
                base + "/state?t=audit-test-token", headers={"Origin": "https://evil.example"}
            ).status
            == 403
        )
    finally:
        context.close()


def test_browser_modal_input_focus_survives_redraw_and_returns_to_trigger(browser, live):
    _app, screen, _preview, base = live
    screen.dialog.add(TextInput(id="modal-first"))
    screen.dialog.add(TextInput(id="modal-second"))
    page = browser.new_page(viewport={"width": 800, "height": 1000})
    try:
        page.goto(base + "/?t=audit-test-token")
        trigger = page.get_by_role("button", name="Open dialog", exact=True)
        trigger.click()
        second = page.locator('input[data-wid$="modal-second"]')
        second.fill("hello")
        expect(second).to_have_value("hello")
        screen.dialog.title = "Updated title"
        expect(page.get_by_role("dialog", name="Updated title", exact=True)).to_be_visible()
        expect(second).to_be_focused()
        expect(second).to_have_value("hello")
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog")).to_have_count(0)
        expect(trigger).to_be_focused()
    finally:
        page.close()
