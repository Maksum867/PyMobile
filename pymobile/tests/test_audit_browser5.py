"""Real Chromium verification for Avatar photos, fallback and intrinsic sizes."""

import io
import re
import threading
from pathlib import Path

import pytest
from PIL import Image as PILImage

from pymobile import App, Avatar, Badge, Column
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
    PILImage.new("RGB", (80, 20), "red").save(tmp_path / "face.png")
    bridge = WebBridge(verbose=False)
    app = App("Identity audit", bridge=bridge)
    avatar = Avatar(text="abcdef", image="assets/face.png", size=48, id="avatar")
    preview, worker = None, None
    try:
        app.run(Column(avatar, Badge("99+", id="badge")))
        preview = WebPreview(app, port=0, token="block5-test", assets=tmp_path)
        bridge.attach(preview)
        server = preview._build_server()
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        with sync_playwright() as playwright:
            browser = launch_chromium(playwright)
            try:
                page = browser.new_page(viewport={"width": 700, "height": 900})
                page.goto(f"http://127.0.0.1:{preview.port}/?t=block5-test")
                yield app, avatar, page
            finally:
                browser.close()
    finally:
        if preview:
            preview.stop()
        if worker:
            worker.join(2)
        app.stop()


def test_browser_avatar_uses_circular_local_photo_and_badge_does_not_stretch(live):
    _, _, page = live
    avatar = page.locator('.avatar[data-wid$=":avatar"]')
    photo = avatar.locator("img")
    expect(photo).to_have_attribute("src", re.compile("^data:image/png;base64,"))
    page.wait_for_function("document.querySelector('.avatar img').naturalWidth === 80")
    bounds = avatar.bounding_box()
    assert bounds["width"] == bounds["height"] == 48
    shot = PILImage.open(io.BytesIO(avatar.screenshot())).convert("RGB")
    assert shot.getpixel((24, 24)) == (255, 0, 0)
    assert shot.getpixel((0, 0)) == (255, 255, 255)
    badge = page.locator('[data-wid$=":badge"]')
    assert 15 < badge.bounding_box()["width"] < 100
    assert float(badge.evaluate("e => parseFloat(getComputedStyle(e).borderRadius)")) >= 20


def test_browser_avatar_failed_source_and_text_updates_keep_initials(live):
    app, model, page = live
    avatar = page.locator('.avatar[data-wid$=":avatar"]')
    expect(avatar.locator("img")).to_have_count(1)
    model.source = "assets/not-there.png"
    model.text = "xyz"
    app.render()
    expect(avatar.locator("img")).to_have_count(0)
    expect(avatar).to_have_text("XY")
    model.source = "assets/face.png"
    app.render()
    expect(avatar.locator("img")).to_have_count(1)
    page.wait_for_function("document.querySelector('.avatar img').naturalWidth === 80")
    model.source = ""
    model.text = "ukraine"
    app.render()
    expect(avatar.locator("img")).to_have_count(0)
    expect(avatar).to_have_text("UK")
