"""Checks for SignalStrength, BatteryLevel, Radar, ConnectionStatus and the
Bluetooth permissions. Kept in the review folder, like the other widget checks.
"""

from __future__ import annotations

import pytest

from pymobile import (
    BatteryLevel,
    Column,
    ConnectionStatus,
    Permission,
    Radar,
    SignalStrength,
)
from pymobile.core.ui.bluetooth import _distance_m
from pymobile.core.ui.registry import unknown_types

# -- Permissions -------------------------------------------------------------


def test_bluetooth_permissions_are_declared() -> None:
    assert Permission.BLUETOOTH.value == "android.permission.BLUETOOTH"
    assert Permission.BLUETOOTH_ADMIN.value == "android.permission.BLUETOOTH_ADMIN"
    assert Permission.BLUETOOTH_SCAN.value == "android.permission.BLUETOOTH_SCAN"
    assert Permission.BLUETOOTH_CONNECT.value == "android.permission.BLUETOOTH_CONNECT"


def test_android12_bluetooth_permissions_are_runtime() -> None:
    assert Permission.BLUETOOTH_SCAN.runtime
    assert Permission.BLUETOOTH_CONNECT.runtime
    assert not Permission.BLUETOOTH.runtime
    assert not Permission.BLUETOOTH_ADMIN.runtime


def test_bluetooth_permission_short_names() -> None:
    assert Permission.BLUETOOTH_SCAN.short == "BLUETOOTH_SCAN"


def test_permission_manager_requests_bluetooth_by_name() -> None:
    from pymobile.core.api.permissions import PermissionManager

    class Bridge:
        def __init__(self) -> None:
            self.granted: set[str] = set()
            self.asked: list[str] = []

        def has_permission(self, name: str) -> bool:
            return name in self.granted

        def request_permissions(self, names, *args, **kwargs):
            self.asked.extend(names)
            return {name: True for name in names}

    bridge = Bridge()
    manager = PermissionManager(bridge)
    assert manager.missing([Permission.BLUETOOTH_SCAN, "BLUETOOTH_CONNECT"]) == [
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.BLUETOOTH_CONNECT",
    ]


# -- SignalStrength ----------------------------------------------------------


@pytest.mark.parametrize(
    ("rssi", "lit"),
    [(-40, 4), (-60, 4), (-61, 3), (-70, 3), (-80, 2), (-90, 1), (-91, 0), (-120, 0)],
)
def test_signal_strength_bar_thresholds(rssi: int, lit: int) -> None:
    from pymobile.core.ui.bluetooth import _signal_level

    assert _signal_level(rssi) == lit


def test_signal_strength_draws_four_bars_and_a_label() -> None:
    widget = SignalStrength(-67)
    assert widget.rssi == -67
    assert len(widget.elements) == 5  # four bars and the text


def test_signal_strength_updates_in_place() -> None:
    widget = SignalStrength(-50)
    before = list(widget.elements)
    widget.set_rssi(None)
    assert widget.rssi is None
    assert widget.elements != before
    assert len(widget.elements) == 5


@pytest.mark.parametrize("bad", ["-67", 3.5, True, -200, 25])
def test_signal_strength_rejects_bad_values(bad) -> None:
    with pytest.raises(ValueError):
        SignalStrength(bad)
    with pytest.raises(ValueError):
        SignalStrength(-50).set_rssi(bad)


# -- BatteryLevel ------------------------------------------------------------


def test_battery_level_keeps_its_state() -> None:
    widget = BatteryLevel(72, charging=True)
    assert widget.percent == 72
    assert widget.charging is True
    widget.set_level(15)
    assert widget.percent == 15
    assert widget.charging is False


def test_battery_level_unknown_has_no_fill() -> None:
    widget = BatteryLevel(None)
    # body, nub and the text: no fill rectangle
    assert len(widget.elements) == 3


def test_battery_level_zero_has_no_fill() -> None:
    assert len(BatteryLevel(0).elements) == 3
    assert len(BatteryLevel(50).elements) == 4


@pytest.mark.parametrize("bad", [-1, 101, 50.0, True, "80"])
def test_battery_level_rejects_bad_percent(bad) -> None:
    with pytest.raises(ValueError):
        BatteryLevel(bad)
    with pytest.raises(ValueError):
        BatteryLevel(10).set_level(bad)


# -- Radar -------------------------------------------------------------------


def test_radar_distance_grows_as_signal_weakens() -> None:
    assert _distance_m(-59) == pytest.approx(1.0)
    assert _distance_m(-79) == pytest.approx(10.0)
    assert _distance_m(-40) < _distance_m(-70) < _distance_m(-90)


def test_radar_draws_rings_centre_and_devices() -> None:
    widget = Radar([("Sensor", -55), ("Lamp", -80)])
    # three rings, the centre dot, and a dot plus a label per device
    assert len(widget.elements) == 3 + 1 + 2 * 2


def test_radar_set_devices_replaces_the_drawing() -> None:
    widget = Radar([("A", -50)])
    widget.set_devices([])
    assert widget.devices == []
    assert len(widget.elements) == 4


def test_radar_caps_the_number_of_devices() -> None:
    with pytest.raises(ValueError, match="at most 12"):
        Radar([(f"d{i}", -70) for i in range(13)])


@pytest.mark.parametrize(
    "bad",
    [[("", -50)], [(None, -50)], [("A", "loud")], [("A",)], ["A"], [("A", 25)]],
)
def test_radar_rejects_bad_devices(bad) -> None:
    with pytest.raises(ValueError):
        Radar(bad)


def test_radar_rejects_bad_range_and_size() -> None:
    with pytest.raises(ValueError):
        Radar(range_m=0)
    with pytest.raises(ValueError):
        Radar(size=50)


def test_radar_far_devices_are_clamped_to_the_outer_ring() -> None:
    far = Radar([("Far", -127)], range_m=5.0)
    centre = far.size / 2
    dot = next(
        e for e in far.elements if getattr(e, "kind", "") == "circle" and e._geometry["r"] == 7
    )
    radius = ((dot._geometry["x"] - centre) ** 2 + (dot._geometry["y"] - centre) ** 2) ** 0.5
    outer = centre - 22
    assert radius == pytest.approx(outer, abs=1e-6)


# -- ConnectionStatus --------------------------------------------------------


def test_connection_status_default_and_labels() -> None:
    status = ConnectionStatus()
    assert status.state == "disconnected"
    assert status._label.text == "Disconnected"


@pytest.mark.parametrize(
    ("state", "label"),
    [
        ("connecting", "Connecting…"),
        ("connected", "Connected"),
        ("error", "Connection failed"),
        ("disconnected", "Disconnected"),
    ],
)
def test_connection_status_states(state: str, label: str) -> None:
    status = ConnectionStatus(state)
    assert status._label.text == label


def test_connection_status_set_state_updates_dot_and_label() -> None:
    status = ConnectionStatus("connecting")
    status.set_state("connected")
    assert status.state == "connected"
    assert status._label.text == "Connected"
    assert len(status._dot.elements) == 1


def test_connection_status_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="state must be one of"):
        ConnectionStatus("paired")
    with pytest.raises(ValueError):
        ConnectionStatus().set_state("bonded")


# -- The tree ----------------------------------------------------------------


def test_bluetooth_widgets_need_no_native_renderer_beyond_canvas_and_row() -> None:
    tree = Column(
        SignalStrength(-70),
        BatteryLevel(40, charging=False),
        Radar([("A", -60)]),
        ConnectionStatus("connected"),
    )
    assert unknown_types(tree.to_dict(), renderer="android") == frozenset()
