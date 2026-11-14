"""The loopback preview answers only to loopback names.

Without this check a page on any domain could point a browser at a developer's
localhost preview and drive the app through the state endpoint.
"""

from __future__ import annotations

from typing import Any

from pymobile import App
from pymobile.core.ui.web import WebPreview
from pymobile.testing import StubBridge


class TestPreviewHostCheck:
    def _preview(self, **kwargs: Any) -> WebPreview:
        return WebPreview(App("Demo", bridge=StubBridge(verbose=False)), port=8765, **kwargs)

    def test_a_loopback_name_is_accepted(self) -> None:
        preview = self._preview()
        for host in ("localhost:8765", "127.0.0.1:8765", "[::1]:8765"):
            assert preview.host_refusal({"Host": host}) is None

    def test_a_dns_name_pointing_here_is_refused(self) -> None:
        preview = self._preview()
        reason = preview.host_refusal({"Host": "evil.example:8765"})
        assert reason is not None and "not a loopback name" in reason

    def test_a_missing_host_header_is_not_a_refusal(self) -> None:
        assert self._preview().host_refusal({}) is None

    def test_allow_hosts_is_the_documented_escape_hatch(self) -> None:
        preview = self._preview(allow_hosts=("preview.corp.example",))
        assert preview.host_refusal({"Host": "preview.corp.example:8765"}) is None

    def test_an_exposed_preview_relies_on_its_token_instead(self) -> None:
        preview = WebPreview(
            App("Demo", bridge=StubBridge(verbose=False)), host="0.0.0.0", port=8765
        )
        assert preview.token
        assert preview.host_refusal({"Host": "anything.example"}) is None

    def test_the_router_refuses_a_rebinding_request(self) -> None:
        preview = self._preview()
        assert preview._authorised("/state", {}, {"Host": "evil.example:8765"}) is False
        assert preview._authorised("/state", {}, {"Host": "localhost:8765"}) is True
