"""UI layer: widgets, layouts, styling and navigation."""

from __future__ import annotations

from .components import (
    Avatar,
    Badge,
    Button,
    Checkbox,
    Chip,
    DataTable,
    Dropdown,
    Image,
    Label,
    Link,
    ProgressBar,
    ProgressText,
    RadioButton,
    RadioGroup,
    RatingBar,
    SearchBar,
    SegmentedButtons,
    Slider,
    Spacer,
    Stepper,
    Switch,
    TextInput,
)
from .dialogs import AlertDialog, BottomSheet, ConfirmDialog, Dialog
from .layout import (
    Column,
    Divider,
    Expanded,
    Flexible,
    Grid,
    Row,
    SafeArea,
    ScrollView,
    Stack,
    Wrap,
)
from .list import List, ListTile
from .navigation import BottomNavigation
from .pickers import DatePicker, TimePicker
from .registry import WidgetCapability, supported_by, widget_types
from .screen import Navigator, Screen
from .snackbar import Snackbar
from .style import Align, Color, EdgeInsets, Style
from .theme import Theme
from .widget import Container, Widget

__all__ = [
    "Widget",
    "Container",
    "Label",
    "Button",
    "TextInput",
    "Image",
    "Switch",
    "ProgressBar",
    "Spacer",
    "Slider",
    "Checkbox",
    "RatingBar",
    "Dropdown",
    "Chip",
    "Badge",
    "Stepper",
    "SearchBar",
    "RadioButton",
    "RadioGroup",
    "SegmentedButtons",
    "ProgressText",
    "Link",
    "DataTable",
    "Avatar",
    "List",
    "ListTile",
    "BottomNavigation",
    "Dialog",
    "AlertDialog",
    "ConfirmDialog",
    "BottomSheet",
    "DatePicker",
    "TimePicker",
    "Divider",
    "Column",
    "Row",
    "Grid",
    "Wrap",
    "Expanded",
    "Flexible",
    "SafeArea",
    "ScrollView",
    "Stack",
    "Screen",
    "Navigator",
    "Snackbar",
    "Style",
    "Color",
    "Align",
    "EdgeInsets",
    "Theme",
    "WidgetCapability",
    "widget_types",
    "supported_by",
]
