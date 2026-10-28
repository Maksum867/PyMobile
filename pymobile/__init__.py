"""PyMobile — a Python framework for building Android applications.

Quick start::

    from pymobile import App, Column, Label, Button, Screen

    class Home(Screen):
        def build(self):
            return Column(
                Label("Hello, Android!"),
                Button("Tap", on_press=lambda: print("tapped")),
            )

    App("Demo").run(Home())

Build it with ``pymobile build``.
"""

from __future__ import annotations

__version__ = "0.10.1"
__author__ = "MAKSYM KHLYSTUN"
__license__ = "MIT"

from .core.api import (
    ModelMigration,
    ModelStore,
    Notifications,
    Permission,
    PermissionManager,
    Share,
    Storage,
    Vibration,
    default_storage_path,
)
from .core.api.audio import AudioPlayer, AudioRecorder
from .core.app import App
from .core.bridge import PickedFile
from .core.config import ProjectConfig, load_config
from .core.events import Event, EventBus
from .core.i18n import (
    device_language,
    flatten_catalogue,
    format_currency,
    format_date,
    format_datetime,
    format_number,
    format_percent,
    format_time,
    plural_category,
    t,
    translations,
)
from .core.jobs import JobHandle, JobManager
from .core.net import HttpCache, HttpClient, HttpFuture, HttpSecurityPolicy, Response
from .core.platform import Platform, current_platform, is_android, is_desktop
from .core.plugins import Plugin, PluginRegistry, plugins
from .core.scheduler import Scheduler, TimerHandle
from .core.ui import (
    ICON_NAMES,
    Accordion,
    AlertDialog,
    Align,
    AutoComplete,
    Avatar,
    Badge,
    BarChart,
    BottomNavigation,
    BottomSheet,
    Button,
    ButtonVariant,
    Calendar,
    Canvas,
    Card,
    Carousel,
    Checkbox,
    Chip,
    Circle,
    Color,
    Column,
    ConfirmDialog,
    Container,
    DataTable,
    DatePicker,
    DateRangePicker,
    Dialog,
    Divider,
    Dropdown,
    EdgeInsets,
    EmptyState,
    Expanded,
    ExpansionPanel,
    Flexible,
    FloatingActionButton,
    Form,
    FormField,
    Grid,
    Icon,
    IconButton,
    Image,
    Label,
    Line,
    LineChart,
    Link,
    List,
    ListTile,
    MultiSelect,
    Navigator,
    PageView,
    Path,
    PieChart,
    Polygon,
    ProgressBar,
    ProgressText,
    RadioButton,
    RadioGroup,
    RangeSlider,
    RatingBar,
    Rect,
    Row,
    SafeArea,
    Screen,
    ScrollView,
    SearchBar,
    SegmentedButtons,
    Shape,
    Skeleton,
    Slider,
    Snackbar,
    Space,
    Spacer,
    Stack,
    Stepper,
    Style,
    Switch,
    Tabs,
    TabView,
    TextInput,
    TextScale,
    Theme,
    TimePicker,
    UiIssue,
    Widget,
    Wrap,
    audit_ui,
)
from .core.ui.registry import (
    preview_renderer,
    preview_renderers,
    register_preview_renderer,
    register_widget_type,
    unknown_types,
    unregister_preview_renderer,
    unregister_widget_type,
)
from .core.validation import ValidationError, Validator
from .deprecation import PyMobileDeprecationWarning
from .errors import (
    BridgeError,
    ConfigError,
    ModelStoreError,
    NetworkError,
    PermissionDeniedError,
    PermissionError_,
    PlatformError,
    PyMobileError,
    ResourceError,
    WidgetNotFoundError,
    WidgetParentError,
    WidgetTypeError,
)
from .log import get_diagnostics

__all__ = [
    "ButtonVariant",
    "ICON_NAMES",
    "Space",
    "TextScale",
    "Icon",
    "IconButton",
    "FloatingActionButton",
    "AutoComplete",
    "RangeSlider",
    "PageView",
    "Card",
    "FormField",
    "Form",
    "Tabs",
    "TabView",
    "ExpansionPanel",
    "Accordion",
    "EmptyState",
    "Skeleton",
    "MultiSelect",
    "Calendar",
    "DateRangePicker",
    "Carousel",
    "BarChart",
    "LineChart",
    "PieChart",
    "Canvas",
    "Circle",
    "Line",
    "Path",
    "Polygon",
    "Rect",
    "Shape",

    "__version__",
    # application
    "App",
    "ProjectConfig",
    "load_config",
    "Event",
    "EventBus",
    "Scheduler",
    "TimerHandle",
    # platform
    "Platform",
    "current_platform",
    "is_android",
    "is_desktop",
    # android apis
    "Notifications",
    "Vibration",
    "Share",
    "AudioPlayer",
    "AudioRecorder",
    "PickedFile",
    "Permission",
    "PermissionManager",
    "Storage",
    "ModelStore",
    "ModelMigration",
    "default_storage_path",
    # networking
    "HttpClient",
    "HttpCache",
    "HttpFuture",
    "HttpSecurityPolicy",
    "Response",
    # i18n
    "device_language",
    "flatten_catalogue",
    "plural_category",
    "format_number",
    "format_percent",
    "format_currency",
    "format_date",
    "format_time",
    "format_datetime",
    "t",
    "translations",
    # widget registry (custom widgets)
    "register_widget_type",
    "unregister_widget_type",
    "unknown_types",
    "register_preview_renderer",
    "unregister_preview_renderer",
    "preview_renderer",
    "preview_renderers",
    # ui
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
    "Snackbar",
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
    "Column",
    "Row",
    "ScrollView",
    "Stack",
    "Screen",
    "Navigator",
    "Style",
    "Color",
    "Align",
    "EdgeInsets",
    "Divider",
    "Expanded",
    "Flexible",
    "Grid",
    "Wrap",
    "SafeArea",
    "Theme",
    # validation
    "Validator",
    "ValidationError",
    "UiIssue",
    "audit_ui",
    # jobs & plugins
    "JobManager",
    "JobHandle",
    "Plugin",
    "PluginRegistry",
    "plugins",
    # diagnostics
    "get_diagnostics",
    # errors
    "PyMobileError",
    "ConfigError",
    "BridgeError",
    "PlatformError",
    "PermissionDeniedError",
    "PermissionError_",
    "NetworkError",
    "ResourceError",
    "ModelStoreError",
    "WidgetNotFoundError",
    "WidgetParentError",
    "WidgetTypeError",
    "PyMobileDeprecationWarning",
]

# The logging helpers used to live in ``pymobile/logging.py``. A module named
# like the standard library's shadows it for any script started from inside
# the package directory, which broke every stdlib module that imports
# ``logging``. It is ``pymobile.log`` now; the old import path keeps working.
import sys as _sys

from . import log as _log_module

_sys.modules.setdefault(f"{__name__}.logging", _log_module)
logging = _log_module
