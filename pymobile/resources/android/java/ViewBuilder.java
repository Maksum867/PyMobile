package org.pymobile.app;

import android.animation.ValueAnimator;
import android.app.Dialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.view.Gravity;
import android.view.VelocityTracker;
import android.view.ViewParent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;


import java.util.HashMap;
import java.util.WeakHashMap;

/**
 * Turns a serialised PyMobile widget tree into a native Android view hierarchy.
 *
 * The mapping is intentionally direct — one widget type to one Android view —
 * so behaviour is predictable and new widgets are cheap to add.
 */
final class ViewBuilder {

    /** Density scale, used to convert the framework's dp values to pixels. */
    final float density;
    final Context context;

    private final ViewContainers containers = new ViewContainers(this);
    private final ViewUpdates updates = new ViewUpdates(this);
    private final ViewControls controls = new ViewControls(this);
    private final ViewInputs inputs = new ViewInputs(this);
    private final ViewStyles styles = new ViewStyles(this);
    private final ViewOverlays overlays = new ViewOverlays(this);
    private final ViewLists listRenderer = new ViewLists(this);

    ViewBuilder(Context context) {
        this.context = context;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    // -- theme --------------------------------------------------------------
    // The palette arrives with every tree (root "theme" object, sent by the
    // Python App). It used to be hard-coded here, so a dark theme only
    // changed what the author styled by hand.

    static final int DEFAULT_PRIMARY = Color.parseColor("#3F51B5");
    int colorPrimary = DEFAULT_PRIMARY;
    int colorOnPrimary = Color.WHITE;
    int colorBackground = Color.WHITE;
    int colorSurface = Color.WHITE;
    int colorText = Color.parseColor("#212121");
    int colorTextMuted = Color.parseColor("#757575");
    /** Destructive actions (ButtonVariant.DANGER). */
    int colorError = Color.parseColor("#C62828");
    String themeSignature = null;
    boolean darkTheme = false;

    /** Apply the palette sent with a tree; returns true when it changed. */
    boolean setTheme(JSONObject theme) {
        String signature = theme == null ? "" : theme.toString();
        if (signature.equals(themeSignature)) {
            return false;
        }
        themeSignature = signature;
        JSONObject t = theme == null ? new JSONObject() : theme;
        colorPrimary = parseColor(t.optString("PRIMARY", ""), DEFAULT_PRIMARY);
        colorBackground = parseColor(t.optString("BACKGROUND", ""), Color.WHITE);
        // SURFACE is the palette's card colour; the light preset's #F5F5F5
        // looks washed out as a dialog, so the light default stays white.
        colorSurface = t.optBoolean("dark", false)
                ? parseColor(t.optString("SURFACE", ""), Color.parseColor("#1E1E1E"))
                : Color.WHITE;
        colorText = parseColor(t.optString("TEXT", ""), Color.parseColor("#212121"));
        colorTextMuted = parseColor(t.optString("TEXT_MUTED", ""), Color.parseColor("#757575"));
        // The destructive button follows the theme's ERROR colour.
        colorError = parseColor(t.optString("ERROR", ""), Color.parseColor("#C62828"));
        colorOnPrimary = Color.WHITE;
        darkTheme = t.optBoolean("dark", false);
        return true;
    }

    /** Window background for the current theme. */
    int backgroundColor() {
        return colorBackground;
    }

    // -- per-view state ------------------------------------------------------
    // View tags hold the widget id, so extra state lives in weak side tables
    // that disappear together with the views.

    /** A Dialog renders into its own window; the anchor stays in the layout. */
    static final class DialogHost {
        final Dialog window;
        final LinearLayout box;
        final TextView title;
        boolean wanted;

        DialogHost(Dialog window, LinearLayout box, TextView title) {
            this.window = window;
            this.box = box;
            this.title = title;
        }
    }

    final WeakHashMap<View, DialogHost> dialogs = new WeakHashMap<>();
    final WeakHashMap<View, String> imageSources = new WeakHashMap<>();
    final WeakHashMap<View, Integer> inputRevisions = new WeakHashMap<>();
    final WeakHashMap<View, double[]> sliderScales = new WeakHashMap<>();
    /** Last requested visibility for nodes opting into a native visibility animation. */
    final WeakHashMap<View, Boolean> animatedVisibilityTargets = new WeakHashMap<>();

    /** Paging state of a List: more rows exist / the row count last requested. */
    static final class ListState {
        boolean hasMore;
        int requestedAt = -1;
        /** Pull-to-refresh spinner row (child 0), or null for a plain list. */
        FrameLayout header;
        /** Whether the spinner is showing (pulled and released, or set by Python). */
        boolean refreshing;
        /** A running height animation of the header. */
        ValueAnimator headerAnimator;
    }

    final WeakHashMap<View, ListState> lists = new WeakHashMap<>();

    /** scroll_serial last applied per List id; survives rebuilds of the screen. */
    final HashMap<String, Integer> scrollSerials = new HashMap<>();

    /** Swipe configuration and drag state of a ListTile row. */
    static final class SwipeState {
        String id = "";
        boolean left;
        boolean right;
        int leftColor = Color.parseColor("#E53935");
        int rightColor = Color.parseColor("#43A047");
        boolean dragging;
        boolean committing;
        /** The row's own background while it is tinted by a drag. */
        Drawable saved;
        boolean hasSaved;
        float downX;
        float downY;
        VelocityTracker velocity;
    }

    final WeakHashMap<View, SwipeState> swipes = new WeakHashMap<>();

    /** Progress bars use a fixed fine scale so float values/maxima survive. */
    static final int PROGRESS_SCALE = 1000;

    int dp(int value) {
        return Math.round(value * density);
    }

    /** Build a child defensively: a failure must not hide the whole screen. */
    View buildChild(JSONObject node) {
        return containers.buildChild(node);
    }

    /**
     * Make a layout view tappable (C-3).
     *
     * The container reports the same events as a Button, with the container's
     * own id, so Python's press()/long_press() run and the whole surface
     * (a Card, a Row, a Stack overlay) becomes the target. A foreground
     * selectable item is what gives the ripple its shape and tells
     * accessibility this is a control, not a group.
     */
    void bindContainerTap(final View view, final String id, JSONObject props,
                                  boolean enabled) {
        containers.bindContainerTap(view, id, props, enabled);
    }

    /** Build the view for one node, recursing into children. */
    View build(JSONObject node) throws JSONException {
        String type = node.optString("type", "Label");
        JSONObject props = node.optJSONObject("props");
        if (props == null) {
            props = new JSONObject();
        }
        JSONObject style = node.optJSONObject("style");
        String id = node.optString("id", "");
        boolean enabled = node.optBoolean("enabled", true);
        boolean visible = node.optBoolean("visible", true);

        View view;
        switch (type) {
            case "Column":
            case "Container":
                // C-2: an explicit Container is a Column on the device —
                // without this case it fell through to CustomWidgetRegistry and
                // drew "[Container: no native renderer]".
                view = buildLinear(node, props, LinearLayout.VERTICAL);
                break;
            case "Row":
                view = buildLinear(node, props, LinearLayout.HORIZONTAL);
                break;
            case "ScrollView":
                view = buildScroll(node, props);
                break;
            case "Stack":
                view = buildStack(node, props);
                break;
            case "Grid":
                view = buildGrid(node, props);
                break;
            case "Wrap":
                view = buildWrap(node, props);
                break;
            case "SafeArea":
                view = buildSafeArea(node, props);
                break;
            case "Expanded":
            case "Flexible":
                view = buildFlex(node);
                break;
            case "Divider":
                view = buildDivider(props);
                break;
            case "Button":
                view = buildButton(id, props);
                break;
            case "TextInput":
                view = buildTextInput(id, props);
                break;
            case "Switch":
                view = buildSwitch(id, props);
                break;
            case "Checkbox":
                view = buildCheckbox(id, props);
                break;
            case "Slider":
                view = buildSlider(id, props);
                break;
            case "RatingBar":
                view = buildRatingBar(id, props);
                break;
            case "Dropdown":
                view = buildDropdown(id, props);
                break;
            case "Chip":
                view = buildChip(id, props);
                break;
            case "Badge":
                view = buildBadge(props);
                break;
            case "SearchBar":
                view = buildTextInput(id, props);
                break;
            case "Stepper":
                view = buildStepper(id, props);
                break;
            case "RadioButton":
                view = buildRadioButton(id, props);
                break;
            case "RadioGroup":
                view = buildRadioGroup(node, id, props);
                break;
            case "SegmentedButtons":
                view = buildSegmented(id, props);
                break;
            case "Link":
                view = buildLink(id, props);
                break;
            case "ProgressText":
                view = buildProgressText(id, props);
                break;
            case "DataTable":
                view = buildDataTable(node, props);
                break;
            case "Avatar":
                view = buildAvatar(props);
                break;
            case "ProgressBar":
                view = buildProgress(props);
                break;
            case "Image":
                view = buildImage(props);
                break;
            case "Spacer":
                view = buildSpacer(props);
                break;
            case "List":
                view = buildList(node, props);
                break;
            case "ListTile":
                view = buildListTile(id, props);
                break;
            case "BottomNavigation":
                view = buildBottomNavigation(id, props);
                break;
            case "Dialog":
                view = buildDialog(node, props);
                break;
            case "DatePicker":
                view = buildDatePicker(id, props);
                break;
            case "TimePicker":
                view = buildTimePicker(id, props);
                break;
            case "Icon":
            case "IconButton":
            case "Chart":
                view = buildVector(type, id, props);
                break;
            case "Canvas":
                view = buildCanvas(id, props);
                break;
            case "AutoComplete":
                view = buildAutoComplete(id, props);
                break;
            case "RangeSlider":
                view = buildRange(id, props);
                break;
            case "PageView":
                view = buildPageView(node, props);
                break;
            case "Label":
                view = buildLabel(props);
                break;
            default:
                // Project-local implementations follow the WidgetRenderer
                // contract and are found automatically from java/. The generic
                // registry keeps custom Java out of the installed package.
                view = CustomWidgetRegistry.build(context, type, id, props);
                if (view == null) {
                    // Keep a visible diagnostic rather than silently dropping
                    // an unknown widget on the floor.
                    view = buildUnknown(type, props);
                }
                break;
        }

        view.setEnabled(enabled);
        // C-3: a container with on_press is one native touch target, so a card
        // of labels becomes a tap without being rebuilt as a ListTile. The
        // children keep their own listeners; Android gives the tap to the
        // innermost clickable view, which is the behaviour a user expects.
        if (props.optString("on_press", "").length() > 0
                || props.optString("on_long_press", "").length() > 0) {
            bindContainerTap(view, id, props, enabled);
        }
        view.setVisibility(visible ? View.VISIBLE : View.GONE);
        JSONObject animation = node.optJSONObject("animation");
        if (animation != null && "fade_scale".equals(animation.optString("visibility", ""))) {
            animatedVisibilityTargets.put(view, visible);
        }
        applyStyle(view, style);
        String accessibilityLabel = props.optString("accessibility_label", "").trim();
        if (!accessibilityLabel.isEmpty()) {
            view.setContentDescription(accessibilityLabel);
        } else if (props.optBoolean("decorative", false)) {
            view.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_NO);
        }
        if ("Dialog".equals(type)) {
            // The anchor never takes space: the dialog shows in its own window.
            DialogHost host = dialogs.get(view);
            if (host != null) {
                host.wanted = visible;
                applyStyle(host.box, style);
            }
            view.setVisibility(View.GONE);
        }
        // Remember the widget id so a later tree can patch this view in place
        // instead of rebuilding the screen (which loses scroll and focus).
        view.setTag(id);
        return view;
    }

    /** Fade and slightly scale a view when an opted-in widget changes visibility. */
    void animateVisibility(final View view, boolean visible, int duration) {
        updates.animateVisibility(view, visible, duration);
    }

    /**
     * A declarative drawing. It shares {@link AdvancedViews.VectorView} with
     * Chart and Icon: the shape list is identical, only the intent differs, so
     * the Android side gained a branch rather than a renderer.
     */
    View buildCanvas(String id, JSONObject props) {
        return controls.buildCanvas(id, props);
    }

    View buildVector(String kind, String id, JSONObject props) {
        return controls.buildVector(kind, id, props);
    }

    View buildAutoComplete(String id, JSONObject props) {
        return inputs.buildAutoComplete(id, props);
    }

    View buildRange(String id, JSONObject props) {
        return inputs.buildRange(id, props);
    }

    View buildPageView(JSONObject node, JSONObject props) throws JSONException {
        return containers.buildPageView(node, props);
    }

    // -- containers -------------------------------------------------------

    View buildLinear(JSONObject node, JSONObject props, int orientation)
            throws JSONException {
        return containers.buildLinear(node, props, orientation);
    }

    /**
     * Layout params for one child of a Row/Column.
     *
     * Three things are decided here, all of which used to be impossible to
     * express from Python: the flex share claimed by Expanded/Flexible, the
     * explicit size of a Spacer, and how the child is sized across the axis.
     */
    LinearLayout.LayoutParams childParams(
            JSONObject childNode, int orientation, String crossAlign) {
        return styles.childParams(childNode, orientation, crossAlign);
    }

    /**
     * Apply the parts of a Style that live on the layout params, not the view.
     *
     * margin, width and height cannot be set in applyStyle(): that runs while
     * the view is still detached, so getLayoutParams() is null and the values
     * were silently dropped. They belong here, where the params are created.
     */
    void applyBoxStyle(ViewGroup.MarginLayoutParams params, JSONObject style) {
        styles.applyBoxStyle(params, style);
    }

    /** Add a Style's margin to layout params that may already carry spacing. */
    void applyMargin(ViewGroup.MarginLayoutParams params, JSONObject style) {
        styles.applyMargin(params, style);
    }

    /**
     * Read a Style dimension.
     *
     * Accepts a number in dp or one of the names the framework documents:
     * "match"/"fill" for MATCH_PARENT and "wrap" for WRAP_CONTENT. Returns
     * Integer.MIN_VALUE when the key is absent, which no real size can be.
     */
    int dimension(JSONObject style, String key) {
        return styles.dimension(style, key);
    }

    /** Position a single child across the container's axis. */
    void applyCrossGravity(
            LinearLayout.LayoutParams params, int orientation, String crossAlign) {
        styles.applyCrossGravity(params, orientation, crossAlign);
    }

    /** Combine main-axis and cross-axis alignment into a container gravity. */
    int gravityFor(int orientation, String align, String crossAlign) {
        return styles.gravityFor(orientation, align, crossAlign);
    }

    /** Gravity along the container's own axis. */
    int mainGravity(String align, boolean vertical) {
        return styles.mainGravity(align, vertical);
    }

    int horizontalGravity(String align) {
        return styles.horizontalGravity(align);
    }

    View buildScroll(JSONObject node, JSONObject props) throws JSONException {
        return containers.buildScroll(node, props);
    }

    View buildStack(JSONObject node, JSONObject props) throws JSONException {
        return containers.buildStack(node, props);
    }

    /**
     * A grid of equal-width columns.
     *
     * Implemented as a column of weighted rows rather than GridLayout: weights
     * guarantee identical column widths whatever the cell contents, which is
     * the property GridLayout only offers from API 21 with extra flags, and
     * every cell keeps working with the in-place update path.
     */
    View buildGrid(JSONObject node, JSONObject props) throws JSONException {
        return containers.buildGrid(node, props);
    }

    /**
     * A flow layout: children side by side, a new line when the row is full.
     *
     * Each child keeps its natural size; its style still contributes margins and an
     * explicit width or height. {@code spacing} separates children on a line and
     * {@code run_spacing} the lines (it defaults to {@code spacing}).
     */
    View buildWrap(JSONObject node, JSONObject props) throws JSONException {
        return containers.buildWrap(node, props);
    }

    /**
     * Insets its content by the real window insets.
     *
     * The values are read from the view tree at attach time, so the content
     * clears the notch, status bar and gesture bar on any device instead of a
     * hard-coded guess. Below API 20 there are no insets to speak of, and the
     * optional `minimum` padding still applies.
     */
    View buildSafeArea(JSONObject node, final JSONObject props) throws JSONException {
        return containers.buildSafeArea(node, props);
    }

    /**
     * Expanded / Flexible: a transparent wrapper.
     *
     * The interesting part — the weight — is applied by the parent in
     * {@link #childParams}; here we only need a container that passes its own
     * size straight through to the single child.
     */
    View buildFlex(JSONObject node) throws JSONException {
        return containers.buildFlex(node);
    }

    View buildDivider(JSONObject props) {
        return containers.buildDivider(props);
    }

    // -- leaves -----------------------------------------------------------

    View buildBottomNavigation(final String id, JSONObject props) throws JSONException {
        return controls.buildBottomNavigation(id, props);
    }

    void styleTab(Button tab, boolean selected) {
        controls.styleTab(tab, selected);
    }

    /**
     * A Dialog is a real modal window (android.app.Dialog), not a card in the
     * layout flow. The view returned here is an invisible zero-size anchor
     * that keeps the widget's place in the tree; the dialog is shown while
     * the anchor is attached and the node is visible, and dismissed when the
     * anchor leaves the window (screen change, rebuild).
     *
     * The title TextView always exists (GONE when empty) so the number of
     * native children is stable: children + 1.
     */
    View buildDialog(JSONObject node, JSONObject props) throws JSONException {
        return overlays.buildDialog(node, props);
    }

    void applyDialogProps(DialogHost host, JSONObject props) {
        overlays.applyDialogProps(host, props);
    }

    /** Show or dismiss the dialog window to match the node and the anchor. */
    void syncDialog(View anchor) {
        overlays.syncDialog(anchor);
    }

    /** A dialog nested in a hidden container must stay hidden too. */
    static boolean ancestorsVisible(View view) {
        android.view.ViewParent parent = view.getParent();
        while (parent instanceof View) {
            if (((View) parent).getVisibility() != View.VISIBLE) {
                return false;
            }
            parent = parent.getParent();
        }
        return true;
    }

    View buildDatePicker(final String id, JSONObject props) {
        return inputs.buildDatePicker(id, props);
    }

    View buildTimePicker(final String id, JSONObject props) {
        return inputs.buildTimePicker(id, props);
    }

    View buildLabel(JSONObject props) {
        return controls.buildLabel(props);
    }

    /**
     * A visible stand-in for a widget type this class has no branch for.
     *
     * The alternative — what used to happen — is an empty Label: the widget
     * disappears, nothing is logged, and the bug is only visible on a phone.
     * Keep the placeholder text short; it is diagnostic, not a fallback that
     * applications should design around.
     */
    View buildUnknown(String type, JSONObject props) {
        return controls.buildUnknown(type, props);
    }

    /**
     * A button in one of the five roles of {@code ButtonVariant}.
     *
     * The platform widget ignores a background set from a Style, which is why
     * every button used to look the same grey. The variants are drawn here as
     * a GradientDrawable + a text colour, so primary/tonal/outlined/text/danger
     * are visually distinct on the device as well as in the previews.
     */
    View buildButton(final String id, JSONObject props) {
        return controls.buildButton(id, props);
    }

    /** Mix two colours; {@code amount} is how much of {@code over} to keep. */
    static int blend(int base, int over, float amount) {
        float keep = Math.max(0f, Math.min(1f, amount));
        int r = Math.round(Color.red(base) * keep + Color.red(over) * (1 - keep));
        int g = Math.round(Color.green(base) * keep + Color.green(over) * (1 - keep));
        int b = Math.round(Color.blue(base) * keep + Color.blue(over) * (1 - keep));
        return Color.rgb(r, g, b);
    }

    /** Black or white, whichever is legible on {@code background}. */
    static int readableOn(int background) {
        double luminance = (0.299 * Color.red(background)
                + 0.587 * Color.green(background)
                + 0.114 * Color.blue(background)) / 255.0;
        return luminance > 0.6 ? Color.BLACK : Color.WHITE;
    }

    View buildTextInput(final String id, JSONObject props) {
        return inputs.buildTextInput(id, props);
    }

    View buildSwitch(final String id, JSONObject props) {
        return inputs.buildSwitch(id, props);
    }

    View buildCheckbox(final String id, JSONObject props) {
        return inputs.buildCheckbox(id, props);
    }

    View buildSlider(final String id, JSONObject props) {
        return inputs.buildSlider(id, props);
    }

    /**
     * Map [minimum, maximum] with ``step`` onto SeekBar's integer progress.
     *
     * The old mapping used one position per whole unit, so Slider(0, 1,
     * step=0.1) had two positions. Without a step, ranges of 100+ keep whole
     * units and smaller ranges get 100 positions.
     */
    void applySliderScale(SeekBar seek, JSONObject props) {
        inputs.applySliderScale(seek, props);
    }

    View buildRatingBar(final String id, JSONObject props) {
        return inputs.buildRatingBar(id, props);
    }

    View buildDropdown(final String id, JSONObject props) {
        return inputs.buildDropdown(id, props);
    }

    View buildChip(final String id, JSONObject props) {
        return controls.buildChip(id, props);
    }

    View buildBadge(JSONObject props) {
        return controls.buildBadge(props);
    }

    View buildStepper(final String id, JSONObject props) {
        return controls.buildStepper(id, props);
    }

    View buildRadioButton(final String id, JSONObject props) {
        return inputs.buildRadioButton(id, props);
    }

    View buildRadioGroup(JSONObject node, final String id, JSONObject props)
            throws JSONException {
        return inputs.buildRadioGroup(node, id, props);
    }

    /**
     * A bar of mutually exclusive segments.
     *
     * The segments share one line, each as wide as its label. In a plain LinearLayout
     * a bar that did not fit squeezed the last segments until their labels wrapped
     * letter by letter ("Б/і/о/л/о/г/і/я"), so the row now sits in a
     * HorizontalScrollView and scrolls instead. With {@code wrap} the segments flow
     * onto further lines, so all of them stay in view. Either way the selected
     * segment is scrolled to, and {@link #segmentRow} finds the buttons again.
     */
    View buildSegmented(final String id, JSONObject props) {
        return controls.buildSegmented(id, props);
    }

    /** The view that holds the segment buttons of a SegmentedButtons view. */
    static ViewGroup segmentRow(View view) {
        if (view instanceof HorizontalScrollView
                && ((ViewGroup) view).getChildCount() == 1
                && ((ViewGroup) view).getChildAt(0) instanceof ViewGroup) {
            return (ViewGroup) ((ViewGroup) view).getChildAt(0);
        }
        return (ViewGroup) view;
    }

    View buildLink(final String id, JSONObject props) {
        return controls.buildLink(id, props);
    }

    /** A horizontal bar with its formatted label ("Downloading 42%") below. */
    View buildProgressText(final String id, JSONObject props) {
        return controls.buildProgressText(id, props);
    }

    /** Fractional value/maximum → fixed integer scale (float maxima used to truncate). */
    static void setScaledProgress(ProgressBar bar, JSONObject props) {
        double maximum = props.optDouble("maximum", 100);
        if (!(maximum > 0)) {
            maximum = 1;
        }
        double fraction = props.optDouble("value", 0) / maximum;
        fraction = Math.max(0, Math.min(1, Double.isNaN(fraction) ? 0 : fraction));
        if (bar.getMax() != PROGRESS_SCALE) {
            bar.setMax(PROGRESS_SCALE);
        }
        int progress = (int) Math.round(fraction * PROGRESS_SCALE);
        if (bar.getProgress() != progress) {
            bar.setProgress(progress);
        }
    }

    /** Set a secondary text and hide the view when it is empty. */
    static void setTextAndVisibility(TextView view, String text, boolean keepWhenEmpty) {
        if (!text.contentEquals(view.getText())) {
            view.setText(text);
        }
        view.setVisibility(text.isEmpty() && !keepWhenEmpty ? View.GONE : View.VISIBLE);
    }

    View buildDataTable(JSONObject node, JSONObject props) throws JSONException {
        return controls.buildDataTable(node, props);
    }

    /** Patch a DataTable in place so row/header edits show without a rebuild. */
    boolean updateDataTable(ViewGroup table, JSONObject props) {
        return controls.updateDataTable(table, props);
    }

    View buildAvatar(JSONObject props) {
        return controls.buildAvatar(props);
    }

    /**
     * List is already virtualised on the Python side (only a window of rows is
     * serialised), so here it is a plain vertical stack of its children.
     */
    View buildList(JSONObject node, JSONObject props) throws JSONException {
        return listRenderer.buildList(node, props);
    }

    /** Rows before the first data row: the pull-to-refresh header, if any. */
    int rowOffset(LinearLayout list) {
        return listRenderer.rowOffset(list);
    }

    /** The spinner row a refreshable List keeps (collapsed) above its rows. */
    FrameLayout buildRefreshHeader() {
        return listRenderer.buildRefreshHeader();
    }

    void setHeaderHeight(ListState state, int height) {
        listRenderer.setHeaderHeight(state, height);
    }

    void animateHeader(final ListState state, int target) {
        listRenderer.animateHeader(state, target);
    }

    /** Show or hide the spinner as Python asks (refreshing = True/False). */
    void applyRefreshing(ListState state, boolean refreshing, boolean animate) {
        listRenderer.applyRefreshing(state, refreshing, animate);
    }

    /**
     * A List that can be pulled down from its top to refresh.
     *
     * There is no androidx here (no Gradle), so SwipeRefreshLayout is not an
     * option. The gesture is read in dispatchTouchEvent, which sees every
     * touch even when a row handles it: at the top of the enclosing
     * ScrollView a downward drag grows the header row, and releasing it past
     * the threshold sends "refresh". Any other drag is handed back to the
     * ScrollView (or to a row being swiped) untouched.
     */


    /** Scroll to the row a List.scroll_to() asked for, once per request. */
    void applyScrollRequest(String id, final LinearLayout list, JSONObject props) {
        listRenderer.applyScrollRequest(id, list, props);
    }

    void scrollToRow(LinearLayout list, int index, boolean animated) {
        listRenderer.scrollToRow(list, index, animated);
    }

    /**
     * Sends "load_more" when the last built row of a List becomes visible.
     *
     * The List used to show its first page (visible_count rows) and nothing
     * else: List(10000, ...) was a list of 20. Scroll and layout changes are
     * observed on the window, so this works whichever ScrollView the list
     * sits in, and also when the first page does not fill the screen.
     */
    void watchList(final String id, final LinearLayout list, JSONObject props) {
        listRenderer.watchList(id, list, props);
    }

    void maybeLoadMore(String id, LinearLayout list) {
        listRenderer.maybeLoadMore(id, list);
    }

    /** Patch a List in place: update built rows, append/remove the difference. */
    boolean updateList(LinearLayout list, JSONObject node, JSONObject props)
            throws JSONException {
        return listRenderer.updateList(list, node, props);
    }

    /** A tappable list row: title + subtitle + trailing, dispatching "press". */
    View buildListTile(final String id, JSONObject props) {
        return listRenderer.buildListTile(id, props);
    }

    /** Fill (or re-fill) every part of a ListTile row. */
    boolean applyListTile(ViewGroup row, String id, JSONObject props) {
        return listRenderer.applyListTile(row, id, props);
    }

    /** Configure (or remove) the sideways swipe of a ListTile row. */
    void setSwipe(View row, String id, JSONObject props) {
        listRenderer.setSwipe(row, id, props);
    }

    void resetSwipe(View row, SwipeState state, boolean animate) {
        listRenderer.resetSwipe(row, state, animate);
    }

    static int withAlpha(int color, float alpha) {
        int a = Math.max(0, Math.min(255, Math.round(255 * alpha)));
        return (color & 0x00FFFFFF) | (a << 24);
    }

    /**
     * One listener for every swipeable row; the per-row configuration lives
     * in the swipes table. A drag only starts once the finger moved sideways
     * clearly more than vertically, so scrolling the list is unaffected, and
     * the row's tap and long-press are cancelled when it does.
     */
    final View.OnTouchListener swipeListener = new ViewGestures.SwipeListener(this);

    void setLongPress(View row, final String id, boolean enabled) {
        listRenderer.setLongPress(row, id, enabled);
    }

    View buildProgress(JSONObject props) {
        return controls.buildProgress(props);
    }

    View buildImage(JSONObject props) {
        return controls.buildImage(props);
    }

    /** Load the image source and fit; used for the first build and for patches. */
    void applyImage(ImageView image, JSONObject props) {
        controls.applyImage(image, props);
    }

    View buildSpacer(JSONObject props) {
        return controls.buildSpacer(props);
    }

    /**
     * Apply a new tree to an existing hierarchy without recreating views.
     *
     * Rebuilding on every render reset the scroll position and closed the
     * keyboard after each keystroke. Updating in place keeps both, and is also
     * far cheaper. Returns false when the structure changed and a full rebuild
     * is required.
     */
    boolean update(View view, JSONObject node) {
        try {
            return updateNode(view, node);
        } catch (JSONException error) {
            return false;
        }
    }

    boolean updateNode(View view, JSONObject node) throws JSONException {
        return updates.updateNode(view, node);
    }

    /** Patch a grid in place, mapping the flat child list onto its rows. */
    boolean updateGrid(ViewGroup grid, JSONObject props, JSONArray children)
            throws JSONException {
        return updates.updateGrid(grid, props, children);
    }

    // -- styling ----------------------------------------------------------

    void applyStyle(View view, JSONObject style) {
        styles.applyStyle(view, style);
    }

    /**
     * Size constraints: min/max width and height, plus aspect ratio.
     *
     * Android has no single API for this, so each piece uses the mechanism
     * that actually works on a plain View: minimums are view properties,
     * maximums and the ratio are enforced by a layout listener that clamps the
     * measured size once the parent has laid the view out.
     */
    void applyConstraints(final View view, final JSONObject style) {
        styles.applyConstraints(view, style);
    }

    /** Parse #RGB / #RRGGBB / #AARRGGBB, falling back on anything unexpected. */
    int parseColor(String value, int fallback) {
        return styles.parseColor(value, fallback);
    }

    // -- snackbar -----------------------------------------------------------
    // Not a widget of the tree: App.snackbar() sends it next to the tree as
    // root["snackbar"], and it floats over the screen at the bottom.

    static final String SNACKBAR_ID = "__snackbar__";
    LinearLayout snackbar;
    TextView snackbarText;
    TextView snackbarAction;
    int snackbarToken = -1;
    boolean snackbarHiding;

    /** Whether a view is the snackbar (MainActivity keeps it when rebuilding). */
    boolean isSnackbar(View view) {
        return overlays.isSnackbar(view);
    }

    /** Show, update or hide the snackbar described by root["snackbar"]. */
    void syncSnackbar(FrameLayout container, JSONObject data) {
        overlays.syncSnackbar(container, data);
    }

    void hideSnackbar() {
        overlays.hideSnackbar();
    }

    void createSnackbar() {
        overlays.createSnackbar();
    }

    /** Inverse colours: a dark bar on a light theme and a light bar on a dark one. */
    void styleSnackbar() {
        overlays.styleSnackbar();
    }
}
