"""Link only opens page-like URLs, never schemes that execute something."""

from __future__ import annotations

import pytest

from pymobile import Link


class TestLinkSchemes:
    @pytest.mark.parametrize(
        "url",
        ["https://example.com", "http://example.com", "mailto:a@b.c", "tel:+380", "/help"],
    )
    def test_allowed_schemes_and_relative_paths(self, url: str) -> None:
        assert Link("x", url=url).url == url

    @pytest.mark.parametrize(
        "url", ["javascript:alert(1)", "file:///etc/passwd", "intent://scan", "data:text/html,x"]
    )
    def test_other_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(ValueError, match="refusing to open"):
            Link("x", url=url)
