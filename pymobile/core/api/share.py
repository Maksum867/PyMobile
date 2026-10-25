"""The system share sheet and the clipboard.

Android has one sheet for "send this somewhere" and a global clipboard, and
neither had a framework call: an app that wanted to share a link had to
``open_url`` it (which is not the same thing) and a "Copy" button could only
say "copied" in a snackbar without copying anything.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..bridge import Bridge, get_bridge

__all__ = ["Share", "looks_like_url"]

#: Characters that may appear in a URL unescaped. Anything else — a space, a
#: Cyrillic path segment, ``?`` and ``&`` are fine — is percent-encoded so the
#: receiving app does not truncate the link at the first space.
_URL_SAFE = "-._~:/?#[]@!$&'()*+,;=%"
_SPACE = re.compile(r"\s+")


def looks_like_url(value: str) -> bool:
    """Whether ``value`` is an ``http(s)://`` address (not just any text)."""
    text = value.strip().lower()
    return text.startswith("http://") or text.startswith("https://")


class Share:
    """Share text, a link or a message, and read/write the clipboard."""

    __slots__ = ("_bridge",)

    def __init__(self, bridge: Bridge | None = None) -> None:
        self._bridge = bridge or get_bridge()

    def sheet(self, text: str = "", url: str = "", *, subject: str = "") -> bool:
        """Open the system share sheet.

        ``url`` is normalised (spaces and non-ASCII escaped) so a link with a
        space in it stays one link, and when only a URL is given the shared
        text *is* the URL — some targets (SMS, QR scanners) read the body, not
        the extra stream. Returns ``False`` when the platform cannot share.
        """
        if not isinstance(text, str) or not isinstance(url, str):
            raise TypeError("text and url must be strings")
        body = text.strip()
        link = url.strip()
        if link:
            if not looks_like_url(link):
                raise ValueError(
                    f"url must start with http:// or https://, got {url!r}; "
                    "share plain text with share(text=...)"
                )
            link = quote(link, safe=_URL_SAFE)
            if not body:
                body = link
        if not body:
            raise ValueError("nothing to share: pass text=, url= or both")
        return bool(self._bridge.share(body, link, subject.strip()))

    def text(self, text: str, *, subject: str = "") -> bool:
        """Share plain text (no link preview)."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        body = text.strip()
        if not body:
            raise ValueError("nothing to share: text is empty")
        return bool(self._bridge.share(body, "", subject.strip()))

    def copy(self, text: str) -> bool:
        """Put ``text`` on the system clipboard; ``True`` when it stuck."""
        if not isinstance(text, str):
            raise TypeError(
                f"text must be a string, got {type(text).__name__!r}; "
                "pass str(value) for anything else"
            )
        return bool(self._bridge.copy_to_clipboard(text))

    def clipboard(self) -> str:
        """What the clipboard currently holds (empty when unreadable)."""
        return self._bridge.clipboard_text() or ""

    # -- convenience aliases, because these are the words on screen --------
    def share(self, text: str = "", url: str = "", *, subject: str = "") -> bool:
        """Alias of :meth:`sheet`."""
        return self.sheet(text, url, subject=subject)

    def copy_to_clipboard(self, text: str) -> bool:
        """Alias of :meth:`copy`."""
        return self.copy(text)
