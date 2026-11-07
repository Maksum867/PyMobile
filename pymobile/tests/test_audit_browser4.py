"""Live Chromium checks for fit modes and ON_PRIMARY changes."""

import io
import threading
from pathlib import Path

import pytest
from PIL import Image as PILImage

from pymobile import App, Button, Column, Image, Style, Theme
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


@pytest.fixture
def live(tmp_path):
    PILImage.new("RGB", (20, 10), "red").save(tmp_path / "image.png")
    bridge = WebBridge(verbose=False)
    app = App("Contrast and fitting", bridge=bridge, theme=Theme("pastel", {"PRIMARY": "#CCDDFF"}))
    app.run(
        Column(
            Button("Primary", id="primary"),
            *(
                Image("image.png", fit=fit, id=fit, style=Style(width=80, height=80))
                for fit in ["contain", "cover", "fill", "none"]
            ),
        )
    )
    preview = WebPreview(app, port=0, token="block4-test", assets=tmp_path)
    bridge.attach(preview)
    server = preview._build_server()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright)
            try:
                page = browser.new_page(viewport={"width": 700, "height": 1000})
                page.goto(f"http://127.0.0.1:{preview.port}/?t=block4-test")
                yield app, page
            finally:
                browser.close()
    finally:
        preview.stop()
        worker.join(2)
        app.stop()


def test_browser_all_image_fit_modes_have_bounded_pixels(live):
    _app, page = live
    for fit in ["contain", "cover", "fill", "none"]:
        image = page.locator(f'img[data-wid$=":{fit}"]')
        expect(image).to_have_css("object-fit", fit)
        page.wait_for_function(
            "[...document.images].every(i => i.complete && i.naturalWidth === 20)"
        )
        shot = PILImage.open(io.BytesIO(image.screenshot())).convert("RGB")
        box = image.bounding_box()
        assert box["width"] == pytest.approx(80)
        assert box["height"] == pytest.approx(80)
        # Fractional CSS y coordinates round outward in screenshot crops.
        assert shot.width == 80 and shot.height in (80, 81)
        assert shot.getpixel((40, 40)) == (255, 0, 0)
        assert shot.getpixel((5, 5)) == (
            (255, 0, 0) if fit in ["fill", "cover"] else (255, 255, 255)
        )
        if fit == "none":
            assert shot.getpixel((29, 40)) == (255, 255, 255)
            assert shot.getpixel((30, 40)) == (255, 0, 0)


def test_browser_semantic_foreground_changes_and_keeps_explicit_override(live):
    app, page = live
    button = page.get_by_role("button", name="Primary", exact=True)
    expect(button).to_have_css("color", "rgb(0, 0, 0)")
    app.set_theme(Theme("deep", {"PRIMARY": "#101040"}))
    expect(button).to_have_css("color", "rgb(255, 255, 255)")
    app.set_theme(Theme("explicit", {"PRIMARY": "#CCDDFF", "ON_PRIMARY": "#123456"}))
    expect(button).to_have_css("color", "rgb(18, 52, 86)")
