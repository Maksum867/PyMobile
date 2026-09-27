"""Modal surfaces: dialogs and a bottom sheet.

A dialog is a container the renderers draw as a framed, elevated surface:
a ``LabelFrame`` in the Tk window, a bordered ``<section>`` in the browser
preview, a modal window (``android.app.Dialog``) on Android. ``open()``/``close()``
flip ``visible``, so the usual reactive rules apply — a closed dialog costs
nothing and reopening it keeps its children::

    self.ask = ConfirmDialog(
        "Delete entry?", "This cannot be undone.",
        on_confirm=self.delete, confirm_text="Delete",
    )
    ...
    self.ask.open()          # visible = True, screen redraws
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .components import Button, Label
from .layout import Row
from .widget import Container, Widget

__all__ = ["Dialog", "AlertDialog", "ConfirmDialog", "BottomSheet"]


class Dialog(Container):
    """A framed surface with an optional title, drawn above the content.

    ``sheet=True`` renders it as a bottom sheet instead: anchored to the
    bottom edge with rounded top corners (browser), packed at the bottom
    (Tk) and as a full-width window at the bottom of the screen on the
    device. On a device the back button or a tap outside calls
    :meth:`dismiss`.
    """

    type_name = "Dialog"
    __slots__ = ("_title", "sheet")

    def __init__(
        self, *children: Widget, title: str = "", sheet: bool = False, **kwargs: Any
    ) -> None:
        # Dialogs are modal by nature and were visible on construction,
        # so a freshly built dialog "nobody opened" appeared on screen.
        # Hidden until open() unless the caller passes visible= explicitly.
        kwargs.setdefault("visible", False)
        super().__init__(*children, **kwargs)
        self._title = title
        self.sheet = sheet

    @property
    def title(self) -> str:
        """The dialog's heading; assigning to it schedules a redraw."""
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        if value != self._title:
            self._title = value
            self.invalidate()

    @property
    def shown(self) -> bool:
        """Whether the dialog is currently on screen."""
        return self.visible

    def open(self) -> None:
        """Show the dialog (``visible = True``)."""
        self.visible = True

    def close(self) -> None:
        """Hide the dialog (``visible = False``)."""
        self.visible = False

    def dismiss(self) -> None:
        """The user dismissed the dialog (back button / tap outside on a device).

        Plain dialogs just close; :class:`ConfirmDialog` treats it as "cancel"
        and :class:`AlertDialog` as an acknowledgement.
        """
        self.close()

    def props(self) -> dict[str, Any]:
        return {**super().props(), "title": self._title, "sheet": self.sheet}


class AlertDialog(Dialog):
    """A message plus one acknowledgement button.

    The button closes the dialog first, then calls ``on_acknowledge``, so a
    handler never runs while the dialog still covers the screen.
    """

    type_name = "Dialog"
    __slots__ = ("_message_label", "on_acknowledge", "button_text")

    def __init__(
        self,
        title: str,
        message: str = "",
        *,
        on_acknowledge: Callable[[], None] | None = None,
        button_text: str = "OK",
        **kwargs: Any,
    ) -> None:
        self._message_label = Label(message)
        self.on_acknowledge = on_acknowledge
        self.button_text = button_text
        super().__init__(
            self._message_label,
            Button(button_text, on_press=self._acknowledge),
            title=title,
            **kwargs,
        )

    @property
    def message(self) -> str:
        return self._message_label.text

    @message.setter
    def message(self, value: str) -> None:
        self._message_label.text = value

    def _acknowledge(self) -> None:
        self.close()
        if self.on_acknowledge is not None:
            self.on_acknowledge()

    def dismiss(self) -> None:
        self._acknowledge()


class ConfirmDialog(Dialog):
    """A yes/no question: two buttons, one outcome callback each.

    Whichever button the user taps closes the dialog first and then fires
    ``on_confirm`` or ``on_cancel``. :meth:`confirm` and :meth:`cancel` do
    exactly what the renderer does on a tap, so tests drive the dialog
    directly::

        self.ask = ConfirmDialog("Delete entry?", on_confirm=self.delete)
        ...
        self.ask.confirm()   # closes the dialog, then fires on_confirm
    """

    type_name = "Dialog"
    __slots__ = ("_message_label", "on_confirm", "on_cancel", "confirm_text", "cancel_text")

    def __init__(
        self,
        title: str,
        message: str = "",
        *,
        on_confirm: Callable[[], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        confirm_text: str = "OK",
        cancel_text: str = "Cancel",
        **kwargs: Any,
    ) -> None:
        self._message_label = Label(message)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        super().__init__(
            self._message_label,
            Row(
                Button(cancel_text, on_press=self.cancel),
                Button(confirm_text, on_press=self.confirm),
                spacing=8,
            ),
            title=title,
            **kwargs,
        )

    @property
    def message(self) -> str:
        return self._message_label.text

    @message.setter
    def message(self, value: str) -> None:
        self._message_label.text = value

    def confirm(self) -> None:
        """Simulate a tap on the confirm button: close, then fire ``on_confirm``.

        The public twin of the confirm button's ``on_press`` — the same call a
        renderer makes, so tests need no bridge or event loop.
        """
        self.close()
        if self.on_confirm is not None:
            self.on_confirm()

    def cancel(self) -> None:
        """Simulate a tap on the cancel button: close, then fire ``on_cancel``."""
        self.close()
        if self.on_cancel is not None:
            self.on_cancel()

    def dismiss(self) -> None:
        self.cancel()


class BottomSheet(Dialog):
    """A sheet anchored to the bottom edge, for actions and pickers."""

    type_name = "Dialog"
    __slots__ = ()

    def __init__(self, *children: Widget, title: str = "", **kwargs: Any) -> None:
        super().__init__(*children, title=title, sheet=True, **kwargs)
