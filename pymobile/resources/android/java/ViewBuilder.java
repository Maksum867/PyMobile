package org.pymobile.app;

import android.animation.ValueAnimator;
import android.app.DatePickerDialog;
import android.app.Dialog;
import android.content.DialogInterface;
import android.app.TimePickerDialog;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.ViewConfiguration;
import android.view.ViewParent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.view.Window;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.RatingBar;
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
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
    private final float density;
    private final Context context;

    ViewBuilder(Context context) {
        this.context = context;
        this.density = context.getResources().getDisplayMetrics().density;
    }

    // -- theme --------------------------------------------------------------
    // The palette arrives with every tree (root "theme" object, sent by the
    // Python App). It used to be hard-coded here, so a dark theme only
    // changed what the author styled by hand.

    private static final int DEFAULT_PRIMARY = Color.parseColor("#3F51B5");
    private int colorPrimary = DEFAULT_PRIMARY;
    private int colorOnPrimary = Color.WHITE;
    private int colorBackground = Color.WHITE;
    private int colorSurface = Color.WHITE;
    private int colorText = Color.parseColor("#212121");
    private int colorTextMuted = Color.parseColor("#757575");
    private String themeSignature = null;
    private boolean darkTheme = false;

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
    private static final class DialogHost {
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

    private final WeakHashMap<View, DialogHost> dialogs = new WeakHashMap<>();
    private final WeakHashMap<View, String> imageSources = new WeakHashMap<>();
    private final WeakHashMap<View, Integer> inputRevisions = new WeakHashMap<>();
    private final WeakHashMap<View, double[]> sliderScales = new WeakHashMap<>();

    /** Paging state of a List: more rows exist / the row count last requested. */
    private static final class ListState {
        boolean hasMore;
        int requestedAt = -1;
        /** Pull-to-refresh spinner row (child 0), or null for a plain list. */
        FrameLayout header;
        /** Whether the spinner is showing (pulled and released, or set by Python). */
        boolean refreshing;
        /** A running height animation of the header. */
        ValueAnimator headerAnimator;
    }

    private final WeakHashMap<View, ListState> lists = new WeakHashMap<>();

    /** scroll_serial last applied per List id; survives rebuilds of the screen. */
    private final HashMap<String, Integer> scrollSerials = new HashMap<>();

    /** Swipe configuration and drag state of a ListTile row. */
    private static final class SwipeState {
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

    private final WeakHashMap<View, SwipeState> swipes = new WeakHashMap<>();

    /** Progress bars use a fixed fine scale so float values/maxima survive. */
    private static final int PROGRESS_SCALE = 1000;

    private int dp(int value) {
        return Math.round(value * density);
    }

    /** Build a child defensively: a failure must not hide the whole screen. */
    private View buildChild(JSONObject node) {
        try {
            return build(node);
        } catch (Exception error) {
            android.util.Log.e("pymobile", "widget failed: " + node.optString("type"), error);
            TextView fallback = new TextView(context);
            fallback.setText("[" + node.optString("type") + ": " + error + "]");
            fallback.setTextColor(Color.parseColor("#C62828"));
            return fallback;
        }
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
                view = buildLinear(node, props, LinearLayout.VERTICAL);
                break;
            case "Row":
                view = buildLinear(node, props, LinearLayout.HORIZONTAL);
                break;
            case "ScrollView":
                view = buildScroll(node, props);
                break;
            case "Stack":
                view = buildStack(node);
                break;
            case "Grid":
                view = buildGrid(node, props);
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
            case "Label":
                view = buildLabel(props);
                break;
            default:
                // A type without a branch above has no native renderer. This
                // used to build an empty Label, so the widget vanished from the
                // phone with no error to explain it (and the Python previews,
                // which print "<BarChart>", looked fine). Draw a labelled
                // placeholder instead and log the type, so a missing case here
                // is visible on the device as well.
                view = buildUnknown(type, props);
                break;
        }

        view.setEnabled(enabled);
        view.setVisibility(visible ? View.VISIBLE : View.GONE);
        applyStyle(view, style);
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

    // -- containers -------------------------------------------------------

    private View buildLinear(JSONObject node, JSONObject props, int orientation)
            throws JSONException {
        LinearLayout layout = new LinearLayout(context);
        layout.setOrientation(orientation);

        String align = props.optString("align", "start");
        // cross_align is optional: without it the historical defaults apply
        // (children stretch in a Column, sit centred in a Row).
        String crossAlign = props.optString("cross_align", "");
        layout.setGravity(gravityFor(orientation, align, crossAlign));

        int spacing = dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                View child = buildChild(childNode);
                LinearLayout.LayoutParams params =
                        childParams(childNode, orientation, crossAlign);
                if (spacing > 0 && i > 0) {
                    if (orientation == LinearLayout.VERTICAL) {
                        params.topMargin += spacing;
                    } else {
                        params.leftMargin += spacing;
                    }
                }
                layout.addView(child, params);
            }
        }
        return layout;
    }

    /**
     * Layout params for one child of a Row/Column.
     *
     * Three things are decided here, all of which used to be impossible to
     * express from Python: the flex share claimed by Expanded/Flexible, the
     * explicit size of a Spacer, and how the child is sized across the axis.
     */
    private LinearLayout.LayoutParams childParams(
            JSONObject childNode, int orientation, String crossAlign) {
        String type = childNode.optString("type");
        JSONObject childProps = childNode.optJSONObject("props");
        if (childProps == null) {
            childProps = new JSONObject();
        }
        boolean vertical = orientation == LinearLayout.VERTICAL;

        boolean stretch = crossAlign.isEmpty() ? vertical : "stretch".equals(crossAlign);
        int across = stretch
                ? ViewGroup.LayoutParams.MATCH_PARENT
                : ViewGroup.LayoutParams.WRAP_CONTENT;
        int width = vertical ? across : ViewGroup.LayoutParams.WRAP_CONTENT;
        int height = vertical ? ViewGroup.LayoutParams.WRAP_CONTENT : across;

        // Expanded/Flexible: hand the child a weighted share of the free space.
        // A tight fit needs a zero base size, otherwise the child's own
        // measurement is added on top of its share and the split is uneven.
        if ("Expanded".equals(type) || "Flexible".equals(type)) {
            int flex = Math.max(1, childProps.optInt("flex", 1));
            boolean tight = !"loose".equals(childProps.optString("fit", "tight"));
            if (vertical) {
                height = tight ? 0 : ViewGroup.LayoutParams.WRAP_CONTENT;
            } else {
                width = tight ? 0 : ViewGroup.LayoutParams.WRAP_CONTENT;
            }
            LinearLayout.LayoutParams flexParams =
                    new LinearLayout.LayoutParams(width, height, flex);
            applyCrossGravity(flexParams, orientation, crossAlign);
            return flexParams;
        }

        if ("Spacer".equals(type)) {
            int size = dp(childProps.optInt("size", 8));
            if (vertical) {
                height = size;
            } else {
                width = size;
            }
        }

        if ("Divider".equals(type)) {
            // A divider spans the container across the axis and is exactly as
            // thick as it says on the main axis.
            int thickness = dp(Math.max(1, childProps.optInt("thickness", 1)));
            if (childProps.optBoolean("vertical", false)) {
                width = thickness;
                height = vertical ? dp(24) : ViewGroup.LayoutParams.MATCH_PARENT;
            } else {
                width = vertical ? ViewGroup.LayoutParams.MATCH_PARENT : dp(24);
                height = thickness;
            }
            int inset = dp(childProps.optInt("inset", 0));
            LinearLayout.LayoutParams dividerParams =
                    new LinearLayout.LayoutParams(width, height);
            if (childProps.optBoolean("vertical", false)) {
                dividerParams.topMargin = inset;
                dividerParams.bottomMargin = inset;
            } else {
                dividerParams.leftMargin = inset;
                dividerParams.rightMargin = inset;
            }
            return dividerParams;
        }

        // An explicit weight in the style keeps working exactly as before.
        JSONObject style = childNode.optJSONObject("style");
        float weight = style == null ? 0f : (float) style.optDouble("weight", 0);
        LinearLayout.LayoutParams params = weight > 0
                ? new LinearLayout.LayoutParams(
                        vertical ? width : 0, vertical ? 0 : height, weight)
                : new LinearLayout.LayoutParams(width, height);
        applyCrossGravity(params, orientation, crossAlign);
        applyBoxStyle(params, style);
        return params;
    }

    /**
     * Apply the parts of a Style that live on the layout params, not the view.
     *
     * margin, width and height cannot be set in applyStyle(): that runs while
     * the view is still detached, so getLayoutParams() is null and the values
     * were silently dropped. They belong here, where the params are created.
     */
    private void applyBoxStyle(ViewGroup.MarginLayoutParams params, JSONObject style) {
        if (style == null) {
            return;
        }
        applyMargin(params, style);

        int width = dimension(style, "width");
        if (width != Integer.MIN_VALUE) {
            params.width = width;
        }
        int height = dimension(style, "height");
        if (height != Integer.MIN_VALUE) {
            params.height = height;
        }
    }

    /** Add a Style's margin to layout params that may already carry spacing. */
    private void applyMargin(ViewGroup.MarginLayoutParams params, JSONObject style) {
        if (style == null) {
            return;
        }
        JSONArray margin = style.optJSONArray("margin");
        if (margin == null || margin.length() != 4) {
            return;
        }
        // Add rather than assign: a container's spacing and a Divider's inset
        // have already been written into these fields.
        params.leftMargin += dp(margin.optInt(0));
        params.topMargin += dp(margin.optInt(1));
        params.rightMargin += dp(margin.optInt(2));
        params.bottomMargin += dp(margin.optInt(3));
    }

    /**
     * Read a Style dimension.
     *
     * Accepts a number in dp or one of the names the framework documents:
     * "match"/"fill" for MATCH_PARENT and "wrap" for WRAP_CONTENT. Returns
     * Integer.MIN_VALUE when the key is absent, which no real size can be.
     */
    private int dimension(JSONObject style, String key) {
        if (!style.has(key)) {
            return Integer.MIN_VALUE;
        }
        Object raw = style.opt(key);
        if (raw instanceof Number) {
            return dp(((Number) raw).intValue());
        }
        String name = String.valueOf(raw).trim().toLowerCase(java.util.Locale.ROOT);
        if ("match".equals(name) || "fill".equals(name) || "match_parent".equals(name)) {
            return ViewGroup.LayoutParams.MATCH_PARENT;
        }
        if ("wrap".equals(name) || "wrap_content".equals(name)) {
            return ViewGroup.LayoutParams.WRAP_CONTENT;
        }
        return Integer.MIN_VALUE;
    }

    /** Position a single child across the container's axis. */
    private void applyCrossGravity(
            LinearLayout.LayoutParams params, int orientation, String crossAlign) {
        if (crossAlign.isEmpty() || "stretch".equals(crossAlign)) {
            return;
        }
        boolean vertical = orientation == LinearLayout.VERTICAL;
        if (vertical) {
            params.gravity = "center".equals(crossAlign)
                    ? Gravity.CENTER_HORIZONTAL
                    : ("end".equals(crossAlign) ? Gravity.END : Gravity.START);
        } else {
            params.gravity = "center".equals(crossAlign)
                    ? Gravity.CENTER_VERTICAL
                    : ("end".equals(crossAlign) ? Gravity.BOTTOM : Gravity.TOP);
        }
    }

    /** Combine main-axis and cross-axis alignment into a container gravity. */
    private int gravityFor(int orientation, String align, String crossAlign) {
        boolean vertical = orientation == LinearLayout.VERTICAL;
        int main = mainGravity(align, vertical);
        int cross;
        if (crossAlign.isEmpty()) {
            cross = vertical ? 0 : Gravity.CENTER_VERTICAL;
        } else if ("center".equals(crossAlign)) {
            cross = vertical ? Gravity.CENTER_HORIZONTAL : Gravity.CENTER_VERTICAL;
        } else if ("end".equals(crossAlign)) {
            cross = vertical ? Gravity.END : Gravity.BOTTOM;
        } else if ("start".equals(crossAlign)) {
            cross = vertical ? Gravity.START : Gravity.TOP;
        } else {
            cross = 0;  // stretch is expressed through the child's params
        }
        return main | cross;
    }

    /** Gravity along the container's own axis. */
    private int mainGravity(String align, boolean vertical) {
        if ("center".equals(align)) {
            return vertical ? Gravity.CENTER_VERTICAL : Gravity.CENTER_HORIZONTAL;
        }
        if ("end".equals(align)) {
            return vertical ? Gravity.BOTTOM : Gravity.END;
        }
        return vertical ? Gravity.TOP : Gravity.START;
    }

    private int horizontalGravity(String align) {
        if ("center".equals(align)) {
            return Gravity.CENTER_HORIZONTAL;
        }
        if ("end".equals(align)) {
            return Gravity.END;
        }
        return Gravity.START;
    }

    private View buildScroll(JSONObject node, JSONObject props) throws JSONException {
        boolean horizontal = props.optBoolean("horizontal", false);
        // setFillViewport lives on each concrete class, not on ViewGroup.
        ViewGroup scroller;
        if (horizontal) {
            HorizontalScrollView view = new HorizontalScrollView(context);
            view.setFillViewport(true);
            scroller = view;
        } else {
            ScrollView view = new ScrollView(context);
            view.setFillViewport(true);
            scroller = view;
        }

        LinearLayout content = new LinearLayout(context);
        int orientation = horizontal ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL;
        content.setOrientation(orientation);
        int spacing = dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                // Reuse the Row/Column sizing rules so a scrolled child gets
                // the same margins, flex shares and spacer sizes it would in
                // a plain Column.
                LinearLayout.LayoutParams params = childParams(childNode, orientation, "");
                if (spacing > 0 && i > 0) {
                    if (horizontal) {
                        params.leftMargin += spacing;
                    } else {
                        params.topMargin += spacing;
                    }
                }
                content.addView(buildChild(childNode), params);
            }
        }
        scroller.addView(content, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroller;
    }

    private View buildStack(JSONObject node) throws JSONException {
        android.widget.FrameLayout frame = new android.widget.FrameLayout(context);
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                frame.addView(buildChild(children.getJSONObject(i)));
            }
        }
        return frame;
    }

    /**
     * A grid of equal-width columns.
     *
     * Implemented as a column of weighted rows rather than GridLayout: weights
     * guarantee identical column widths whatever the cell contents, which is
     * the property GridLayout only offers from API 21 with extra flags, and
     * every cell keeps working with the in-place update path.
     */
    private View buildGrid(JSONObject node, JSONObject props) throws JSONException {
        int columns = Math.max(1, props.optInt("columns", 2));
        int rowSpacing = dp(props.optInt("row_spacing", 0));
        int columnSpacing = dp(props.optInt("column_spacing", 0));

        LinearLayout grid = new LinearLayout(context);
        grid.setOrientation(LinearLayout.VERTICAL);

        JSONArray children = node.optJSONArray("children");
        int count = children == null ? 0 : children.length();
        for (int start = 0; start < count; start += columns) {
            LinearLayout row = new LinearLayout(context);
            row.setOrientation(LinearLayout.HORIZONTAL);
            for (int column = 0; column < columns; column++) {
                int index = start + column;
                LinearLayout.LayoutParams params =
                        new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
                if (column > 0) {
                    params.leftMargin = columnSpacing;
                }
                // Pad the final row with empty cells so the columns of a
                // partially filled row keep the width of the ones above.
                View cell;
                if (index < count) {
                    JSONObject childNode = children.getJSONObject(index);
                    cell = buildChild(childNode);
                    // A cell keeps its own margin; its width is owned by the
                    // grid, so only the margins are taken from the style.
                    applyMargin(params, childNode.optJSONObject("style"));
                } else {
                    cell = new View(context);
                }
                row.addView(cell, params);
            }
            LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            if (start > 0) {
                rowParams.topMargin = rowSpacing;
            }
            grid.addView(row, rowParams);
        }
        return grid;
    }

    /**
     * Insets its content by the real window insets.
     *
     * The values are read from the view tree at attach time, so the content
     * clears the notch, status bar and gesture bar on any device instead of a
     * hard-coded guess. Below API 20 there are no insets to speak of, and the
     * optional `minimum` padding still applies.
     */
    private View buildSafeArea(JSONObject node, final JSONObject props) throws JSONException {
        final LinearLayout frame = new LinearLayout(context);
        frame.setOrientation(LinearLayout.VERTICAL);

        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                frame.addView(buildChild(children.getJSONObject(i)),
                        new LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.WRAP_CONTENT));
            }
        }

        final int minimum = dp(props.optInt("minimum", 0));
        frame.setPadding(minimum, minimum, minimum, minimum);
        if (android.os.Build.VERSION.SDK_INT >= 20) {
            frame.setOnApplyWindowInsetsListener(new View.OnApplyWindowInsetsListener() {
                @Override
                public android.view.WindowInsets onApplyWindowInsets(
                        View view, android.view.WindowInsets insets) {
                    view.setPadding(
                            Math.max(minimum, props.optBoolean("left", true)
                                    ? insets.getSystemWindowInsetLeft() : 0),
                            Math.max(minimum, props.optBoolean("top", true)
                                    ? insets.getSystemWindowInsetTop() : 0),
                            Math.max(minimum, props.optBoolean("right", true)
                                    ? insets.getSystemWindowInsetRight() : 0),
                            Math.max(minimum, props.optBoolean("bottom", true)
                                    ? insets.getSystemWindowInsetBottom() : 0));
                    return insets;
                }
            });
            frame.requestApplyInsets();
        }
        return frame;
    }

    /**
     * Expanded / Flexible: a transparent wrapper.
     *
     * The interesting part — the weight — is applied by the parent in
     * {@link #childParams}; here we only need a container that passes its own
     * size straight through to the single child.
     */
    private View buildFlex(JSONObject node) throws JSONException {
        LinearLayout holder = new LinearLayout(context);
        holder.setOrientation(LinearLayout.VERTICAL);
        JSONArray children = node.optJSONArray("children");
        if (children != null && children.length() > 0) {
            holder.addView(buildChild(children.getJSONObject(0)),
                    new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT));
        }
        return holder;
    }

    private View buildDivider(JSONObject props) {
        // Like Spacer, this leaf never sets its own layout params: the parent
        // container owns them and a mismatched type throws at runtime.
        View line = new View(context);
        line.setBackgroundColor(parseColor(props.optString("color", "#1F000000"),
                Color.parseColor("#1F000000")));
        int thickness = dp(Math.max(1, props.optInt("thickness", 1)));
        if (props.optBoolean("vertical", false)) {
            line.setMinimumWidth(thickness);
        } else {
            line.setMinimumHeight(thickness);
        }
        return line;
    }

    // -- leaves -----------------------------------------------------------

    private View buildBottomNavigation(final String id, JSONObject props) throws JSONException {
        LinearLayout bar = new LinearLayout(context);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        JSONArray options = props.optJSONArray("options");
        final String value = props.optString("value", "");
        if (options != null) {
            for (int i = 0; i < options.length(); i++) {
                final String label = options.optString(i, "");
                Button tab = new Button(context);
                tab.setText(label);
                tab.setAllCaps(false);
                tab.setLayoutParams(new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                styleTab(tab, label.equals(value));
                tab.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        // Read the label at click time: a patch may rename tabs.
                        Native.dispatchEvent(id, "change", ((Button) v).getText().toString());
                    }
                });
                bar.addView(tab);
            }
        }
        return bar;
    }

    private void styleTab(Button tab, boolean selected) {
        tab.setBackgroundColor(selected ? colorPrimary : colorSurface);
        tab.setTextColor(selected ? colorOnPrimary : colorText);
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
    private View buildDialog(JSONObject node, JSONObject props) throws JSONException {
        final String id = node.optString("id", "");
        final FrameLayout anchor = new FrameLayout(context);

        LinearLayout box = new LinearLayout(context);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = dp(16);
        box.setPadding(pad, pad, pad, pad);
        TextView head = new TextView(context);
        head.setTypeface(null, Typeface.BOLD);
        head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        head.setPadding(0, 0, 0, dp(8));
        box.addView(head, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                box.addView(buildChild(children.getJSONObject(i)), new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            }
        }
        ScrollView scroll = new ScrollView(context);
        scroll.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Dialog window = new Dialog(context);
        window.requestWindowFeature(Window.FEATURE_NO_TITLE);
        window.setContentView(scroll);
        window.setCanceledOnTouchOutside(true);
        window.setOnCancelListener(new DialogInterface.OnCancelListener() {
            @Override
            public void onCancel(DialogInterface dialog) {
                // Back / tap outside: let Python close it (and run on_cancel).
                Native.dispatchEvent(id, "dismiss", "");
            }
        });
        DialogHost host = new DialogHost(window, box, head);
        dialogs.put(anchor, host);
        applyDialogProps(host, props);

        anchor.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
            @Override
            public void onViewAttachedToWindow(View v) {
                syncDialog(v);
            }

            @Override
            public void onViewDetachedFromWindow(View v) {
                DialogHost h = dialogs.get(v);
                if (h != null && h.window.isShowing()) {
                    h.window.dismiss();
                }
            }
        });
        return anchor;
    }

    private void applyDialogProps(DialogHost host, JSONObject props) {
        String title = props.optString("title", "");
        if (!title.contentEquals(host.title.getText())) {
            host.title.setText(title);
        }
        host.title.setVisibility(title.isEmpty() ? View.GONE : View.VISIBLE);
        host.title.setTextColor(colorText);
        boolean sheet = props.optBoolean("sheet", false);
        GradientDrawable surface = new GradientDrawable();
        surface.setColor(colorSurface);
        float r = dp(sheet ? 16 : 12);
        surface.setCornerRadii(sheet
                ? new float[]{r, r, r, r, 0, 0, 0, 0}
                : new float[]{r, r, r, r, r, r, r, r});
        host.box.setBackground(surface);
        Window w = host.window.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setGravity(sheet ? Gravity.BOTTOM : Gravity.CENTER);
            int screen = context.getResources().getDisplayMetrics().widthPixels;
            w.setLayout(sheet ? ViewGroup.LayoutParams.MATCH_PARENT : Math.round(screen * 0.88f),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }
    }

    /** Show or dismiss the dialog window to match the node and the anchor. */
    private void syncDialog(View anchor) {
        DialogHost host = dialogs.get(anchor);
        if (host == null) {
            return;
        }
        boolean show = host.wanted && anchor.getWindowToken() != null && ancestorsVisible(anchor);
        try {
            if (show && !host.window.isShowing()) {
                host.window.show();
            } else if (!show && host.window.isShowing()) {
                host.window.dismiss();
            }
        } catch (RuntimeException error) {
            // e.g. BadTokenException while the activity is finishing.
            android.util.Log.w("pymobile", "dialog window failed", error);
        }
    }

    /** A dialog nested in a hidden container must stay hidden too. */
    private static boolean ancestorsVisible(View view) {
        android.view.ViewParent parent = view.getParent();
        while (parent instanceof View) {
            if (((View) parent).getVisibility() != View.VISIBLE) {
                return false;
            }
            parent = parent.getParent();
        }
        return true;
    }

    private View buildDatePicker(final String id, JSONObject props) {
        final String current = props.optString("value", "");
        final Button button = new Button(context);
        button.setAllCaps(false);
        button.setText(current.isEmpty() ? "Pick date" : current);
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                // Parse the value shown now, not the one captured at build
                // time: Python may have changed it through a patch since.
                String shown = button.getText().toString();
                int y = 2000, m = 0, d = 1;
                if (shown.matches("\\d{4}-\\d{2}-\\d{2}")) {
                    y = Integer.parseInt(shown.substring(0, 4));
                    m = Integer.parseInt(shown.substring(5, 7)) - 1;
                    d = Integer.parseInt(shown.substring(8, 10));
                }
                DatePickerDialog dialog = new DatePickerDialog(context,
                        new DatePickerDialog.OnDateSetListener() {
                            @Override
                            public void onDateSet(android.widget.DatePicker view,
                                                  int year, int month, int day) {
                                String iso = String.format(java.util.Locale.US,
                                        "%04d-%02d-%02d", year, month + 1, day);
                                button.setText(iso);
                                Native.dispatchEvent(id, "change", iso);
                            }
                        }, y, m, d);
                dialog.show();
            }
        });
        return button;
    }

    private View buildTimePicker(final String id, JSONObject props) {
        final String current = props.optString("value", "");
        final Button button = new Button(context);
        button.setAllCaps(false);
        button.setText(current.isEmpty() ? "Pick time" : current);
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String shown = button.getText().toString();
                int h = 12, min = 0;
                if (shown.matches("\\d{2}:\\d{2}")) {
                    h = Integer.parseInt(shown.substring(0, 2));
                    min = Integer.parseInt(shown.substring(3, 5));
                }
                TimePickerDialog dialog = new TimePickerDialog(context,
                        new TimePickerDialog.OnTimeSetListener() {
                            @Override
                            public void onTimeSet(android.widget.TimePicker view,
                                                  int hour, int minute) {
                                String iso = String.format(java.util.Locale.US,
                                        "%02d:%02d", hour, minute);
                                button.setText(iso);
                                Native.dispatchEvent(id, "change", iso);
                            }
                        }, h, min, true);
                dialog.show();
            }
        });
        return button;
    }

    private View buildLabel(JSONObject props) {
        TextView label = new TextView(context);
        label.setText(props.optString("text", ""));
        label.setTextColor(colorText);
        label.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        return label;
    }

    /**
     * A visible stand-in for a widget type this class has no branch for.
     *
     * The alternative — what used to happen — is an empty Label: the widget
     * disappears, nothing is logged, and the bug is only visible on a phone.
     * Keep the placeholder text short; it is diagnostic, not a fallback that
     * applications should design around.
     */
    private View buildUnknown(String type, JSONObject props) {
        android.util.Log.w("pymobile", "no native renderer for widget type \"" + type + "\"");
        String text = props.optString("text", "");
        TextView placeholder = new TextView(context);
        // The widget's own text still shows, so a screen built from a custom
        // type before its Java branch exists is not blank on the device.
        placeholder.setText(text.isEmpty()
                ? "[" + type + ": no native renderer]"
                : "[" + type + ": no native renderer] " + text);
        placeholder.setTextColor(Color.parseColor("#C62828"));
        placeholder.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        placeholder.setPadding(dp(4), dp(4), dp(4), dp(4));
        return placeholder;
    }

    private View buildButton(final String id, JSONObject props) {
        Button button = new Button(context);
        button.setText(props.optString("text", ""));
        button.setAllCaps(false);
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return button;
    }

    private View buildTextInput(final String id, JSONObject props) {
        EditText input = new EditText(context);
        input.setText(props.optString("value", ""));
        input.setHint(props.optString("placeholder", ""));
        input.setTextColor(colorText);
        input.setHintTextColor(colorTextMuted);
        inputRevisions.put(input, props.optInt("revision", 0));
        if (props.optBoolean("multiline", false)) {
            input.setInputType(InputType.TYPE_CLASS_TEXT
                    | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
            input.setMinLines(3);
        } else if (props.optBoolean("password", false)) {
            input.setInputType(InputType.TYPE_CLASS_TEXT
                    | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        } else {
            input.setInputType(InputType.TYPE_CLASS_TEXT);
            input.setSingleLine(true);
        }
        input.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void afterTextChanged(Editable editable) {
                Native.dispatchEvent(id, "change", editable.toString());
            }
        });
        return input;
    }

    private View buildSwitch(final String id, JSONObject props) {
        Switch toggle = new Switch(context);
        toggle.setChecked(props.optBoolean("checked", false));
        toggle.setTextColor(colorText);
        toggle.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton view, boolean checked) {
                Native.dispatchEvent(id, "toggle", checked ? "true" : "false");
            }
        });
        return toggle;
    }

    private View buildCheckbox(final String id, JSONObject props) {
        CheckBox checkbox = new CheckBox(context);
        checkbox.setChecked(props.optBoolean("checked", false));
        checkbox.setTextColor(colorText);
        checkbox.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton view, boolean checked) {
                Native.dispatchEvent(id, "toggle", checked ? "true" : "false");
            }
        });
        return checkbox;
    }

    private View buildSlider(final String id, JSONObject props) {
        SeekBar seek = new SeekBar(context);
        applySliderScale(seek, props);
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                if (fromUser) {
                    double[] scale = sliderScales.get(bar);
                    if (scale == null) {
                        return;
                    }
                    double value = progress >= bar.getMax()
                            ? scale[1]
                            : scale[0] + progress * scale[2];
                    // Trim binary noise (0.30000000000000004) before it
                    // reaches Python.
                    value = Math.round(value * 1e9) / 1e9;
                    Native.dispatchEvent(id, "change", String.valueOf(value));
                }
            }

            @Override
            public void onStartTrackingTouch(SeekBar bar) {
            }

            @Override
            public void onStopTrackingTouch(SeekBar bar) {
            }
        });
        return seek;
    }

    /**
     * Map [minimum, maximum] with ``step`` onto SeekBar's integer progress.
     *
     * The old mapping used one position per whole unit, so Slider(0, 1,
     * step=0.1) had two positions. Without a step, ranges of 100+ keep whole
     * units and smaller ranges get 100 positions.
     */
    private void applySliderScale(SeekBar seek, JSONObject props) {
        double minimum = props.optDouble("minimum", 0);
        double maximum = props.optDouble("maximum", 100);
        double range = Math.max(0, maximum - minimum);
        double step = props.isNull("step") ? Double.NaN : props.optDouble("step", Double.NaN);
        if (Double.isNaN(step) || step <= 0) {
            step = range >= 100 ? 1 : (range > 0 ? range / 100.0 : 1);
        }
        if (range / step > 10000) {
            step = range / 10000.0;
        }
        int steps = Math.max(1, (int) Math.round(range / step));
        sliderScales.put(seek, new double[]{minimum, maximum, step});
        if (seek.getMax() != steps) {
            seek.setMax(steps);
        }
        double value = props.optDouble("value", minimum);
        int progress = (int) Math.round((value - minimum) / step);
        progress = Math.max(0, Math.min(steps, progress));
        if (seek.getProgress() != progress) {
            seek.setProgress(progress);
        }
    }

    private View buildRatingBar(final String id, JSONObject props) {
        RatingBar rating = new RatingBar(context);
        int maximum = Math.max(1, props.optInt("maximum", 5));
        rating.setNumStars(maximum);
        rating.setMax(maximum);
        rating.setStepSize(1f);
        rating.setRating((float) props.optDouble("rating", 0));
        rating.setOnRatingBarChangeListener(new RatingBar.OnRatingBarChangeListener() {
            @Override
            public void onRatingChanged(RatingBar bar, float value, boolean fromUser) {
                if (fromUser) {
                    Native.dispatchEvent(id, "change", String.valueOf(value));
                }
            }
        });
        return rating;
    }

    private View buildDropdown(final String id, JSONObject props) {
        Spinner spinner = new Spinner(context);
        JSONArray options = props.optJSONArray("options");
        String[] entries = new String[options == null ? 0 : options.length()];
        for (int i = 0; i < entries.length; i++) {
            entries[i] = options.optString(i, "");
        }
        android.widget.ArrayAdapter<String> adapter = new android.widget.ArrayAdapter<>(
                context, android.R.layout.simple_spinner_item, entries);
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        spinner.setAdapter(adapter);
        String selected = props.optString("value", entries.length > 0 ? entries[0] : "");
        for (int i = 0; i < entries.length; i++) {
            if (entries[i].equals(selected)) {
                spinner.setSelection(i);
                break;
            }
        }
        spinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(
                    android.widget.AdapterView<?> parent, View view, int position, long itemId) {
                Native.dispatchEvent(id, "change",
                        String.valueOf(entries[position]));
            }

            @Override
            public void onNothingSelected(android.widget.AdapterView<?> parent) {
            }
        });
        return spinner;
    }

    private View buildChip(final String id, JSONObject props) {
        Button chip = new Button(context);
        chip.setText(props.optString("text", ""));
        chip.setAllCaps(false);
        if (props.optBoolean("selected", false)) {
            chip.setTextColor(Color.parseColor("#FFFFFF"));
            chip.setBackgroundColor(parseColor(props.optString("selectedColor", ""),
                    colorPrimary));
        }
        chip.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return chip;
    }

    private View buildBadge(JSONObject props) {
        TextView badge = new TextView(context);
        badge.setText(props.optString("text", ""));
        badge.setTextColor(parseColor(props.optString("color", "#FFFFFF"),
                Color.parseColor("#FFFFFF")));
        badge.setBackgroundColor(parseColor(props.optString("background", ""), colorPrimary));
        badge.setGravity(Gravity.CENTER);
        int pad = dp(6);
        badge.setPadding(pad, dp(2), pad, dp(2));
        return badge;
    }

    private View buildStepper(final String id, JSONObject props) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);

        Button minus = new Button(context);
        minus.setText("−");
        minus.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "decrement", "");
            }
        });
        TextView value = new TextView(context);
        value.setText(String.valueOf(props.optInt("value", 0)));
        value.setGravity(Gravity.CENTER);
        value.setPadding(dp(12), 0, dp(12), 0);
        value.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        value.setTextColor(colorText);
        Button plus = new Button(context);
        plus.setText("+");
        plus.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "increment", "");
            }
        });

        row.addView(minus);
        row.addView(value);
        row.addView(plus);
        row.setTag(id);  // the outer row carries the widget id
        return row;
    }

    private View buildRadioButton(final String id, JSONObject props) {
        RadioButton radio = new RadioButton(context);
        radio.setText(props.optString("text", ""));
        radio.setTextColor(colorText);
        radio.setChecked(props.optBoolean("selected", false));
        radio.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return radio;
    }

    private View buildRadioGroup(JSONObject node, final String id, JSONObject props)
            throws JSONException {
        RadioGroup group = new RadioGroup(context);
        group.setOrientation(LinearLayout.VERTICAL);
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                // buildRadioButton already applies the child's `selected`
                // state directly. Do not call group.check(...) here: children
                // never carry a View id (nothing calls setId), so the old
                // expression evaluated to check(NO_ID) and cleared the group.
                group.addView(buildChild(childNode));
            }
        }
        // The outer group carries the widget id.
        group.setTag(id);
        return group;
    }

    private View buildSegmented(final String id, JSONObject props) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        JSONArray options = props.optJSONArray("options");
        String selected = props.optString("value", "");
        if (options != null) {
            for (int i = 0; i < options.length(); i++) {
                final String label = options.optString(i, "");
                Button segment = new Button(context);
                segment.setText(label);
                segment.setAllCaps(false);
                if (label.equals(selected)) {
                    segment.setTextColor(colorOnPrimary);
                    segment.setBackgroundColor(colorPrimary);
                } else {
                    segment.setTextColor(colorText);
                }
                segment.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        Native.dispatchEvent(id, "change", label);
                    }
                });
                row.addView(segment);
            }
        }
        row.setTag(id);
        return row;
    }

    private View buildLink(final String id, JSONObject props) {
        TextView link = new TextView(context);
        link.setText(props.optString("text", ""));
        link.setTextColor(colorPrimary);
        link.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        final String url = props.optString("url", "");
        link.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                // Open the URL in the system browser directly from Java (no JNI
                // round-trip), so links work even with an older libpymobile.so.
                if (url != null && !url.isEmpty()) {
                    DeviceServices.openUrl(context, url);
                } else {
                    Native.dispatchEvent(id, "press", "");
                }
            }
        });
        return link;
    }

    /** A horizontal bar with its formatted label ("Downloading 42%") below. */
    private View buildProgressText(final String id, JSONObject props) {
        LinearLayout box = new LinearLayout(context);
        box.setOrientation(LinearLayout.VERTICAL);
        ProgressBar bar = new ProgressBar(context, null,
                android.R.attr.progressBarStyleHorizontal);
        setScaledProgress(bar, props);
        box.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView label = new TextView(context);
        label.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        label.setTextColor(colorTextMuted);
        setTextAndVisibility(label, props.optString("text", ""), false);
        box.addView(label);
        return box;
    }

    /** Fractional value/maximum → fixed integer scale (float maxima used to truncate). */
    private static void setScaledProgress(ProgressBar bar, JSONObject props) {
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
    private static void setTextAndVisibility(TextView view, String text, boolean keepWhenEmpty) {
        if (!text.contentEquals(view.getText())) {
            view.setText(text);
        }
        view.setVisibility(text.isEmpty() && !keepWhenEmpty ? View.GONE : View.VISIBLE);
    }

    private View buildDataTable(JSONObject node, JSONObject props) throws JSONException {
        LinearLayout table = new LinearLayout(context);
        table.setOrientation(LinearLayout.VERTICAL);
        JSONArray headers = props.optJSONArray("headers");
        JSONArray rows = props.optJSONArray("rows");
        if (headers != null) {
            LinearLayout head = new LinearLayout(context);
            head.setOrientation(LinearLayout.HORIZONTAL);
            for (int i = 0; i < headers.length(); i++) {
                TextView cell = new TextView(context);
                cell.setText(headers.optString(i, ""));
                cell.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                cell.setTextColor(colorText);
                cell.setPadding(dp(8), dp(4), dp(8), dp(4));
                head.addView(cell);
            }
            table.addView(head);
        }
        if (rows != null) {
            for (int r = 0; r < rows.length(); r++) {
                LinearLayout row = new LinearLayout(context);
                row.setOrientation(LinearLayout.HORIZONTAL);
                JSONArray cells = rows.optJSONArray(r);
                for (int c = 0; c < (cells == null ? 0 : cells.length()); c++) {
                    TextView cell = new TextView(context);
                    cell.setText(cells.optString(c, ""));
                    cell.setTextColor(colorText);
                    cell.setPadding(dp(8), dp(4), dp(8), dp(4));
                    row.addView(cell);
                }
                table.addView(row);
            }
        }
        return table;
    }

    /** Patch a DataTable in place so row/header edits show without a rebuild. */
    private boolean updateDataTable(ViewGroup table, JSONObject props) {
        JSONArray headers = props.optJSONArray("headers");
        JSONArray rows = props.optJSONArray("rows");
        int headerRows = headers != null ? 1 : 0;
        int expected = headerRows + (rows == null ? 0 : rows.length());
        if (table.getChildCount() != expected) {
            return false;  // structure changed -> let the caller rebuild
        }
        int index = 0;
        if (headers != null) {
            View head = table.getChildAt(index++);
            if (!(head instanceof ViewGroup)) {
                return false;
            }
            for (int c = 0; c < headers.length(); c++) {
                View cell = ((ViewGroup) head).getChildAt(c);
                if (cell instanceof TextView) {
                    ((TextView) cell).setText(headers.optString(c, ""));
                }
            }
        }
        if (rows != null) {
            for (int r = 0; r < rows.length(); r++) {
                View rowView = table.getChildAt(index++);
                if (!(rowView instanceof ViewGroup)) {
                    return false;
                }
                JSONArray cells = rows.optJSONArray(r);
                int columns = cells == null ? 0 : cells.length();
                if (((ViewGroup) rowView).getChildCount() != columns) {
                    return false;
                }
                for (int c = 0; c < columns; c++) {
                    View cell = ((ViewGroup) rowView).getChildAt(c);
                    if (cell instanceof TextView) {
                        ((TextView) cell).setText(cells.optString(c, ""));
                    }
                }
            }
        }
        return true;
    }

    private View buildAvatar(JSONObject props) {
        TextView avatar = new TextView(context);
        String text = props.optString("text", "");
        if (text.length() > 2) {
            text = text.substring(0, 2);
        }
        avatar.setText(text.toUpperCase());
        avatar.setTextColor(parseColor(props.optString("color", "#FFFFFF"),
                Color.parseColor("#FFFFFF")));
        avatar.setGravity(Gravity.CENTER);
        int size = dp(props.optInt("size", 48));
        avatar.setBackgroundColor(parseColor(props.optString("background", ""), colorPrimary));
        avatar.setMinimumWidth(size);
        avatar.setMinimumHeight(size);
        return avatar;
    }

    /**
     * List is already virtualised on the Python side (only a window of rows is
     * serialised), so here it is a plain vertical stack of its children.
     */
    private View buildList(JSONObject node, JSONObject props) throws JSONException {
        String id = node.optString("id", "");
        PullList list = new PullList(id);
        list.setOrientation(LinearLayout.VERTICAL);
        ListState state = new ListState();
        state.hasMore = props.optBoolean("has_more", false);
        lists.put(list, state);
        if (props.optBoolean("refreshable", false)) {
            state.header = buildRefreshHeader();
            list.addView(state.header, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, 0));
            applyRefreshing(state, props.optBoolean("refreshing", false), false);
        }
        int spacing = dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT);
                if (spacing > 0 && i > 0) {
                    params.topMargin = spacing;
                }
                list.addView(buildChild(children.getJSONObject(i)), params);
            }
        }
        watchList(id, list, props);
        applyScrollRequest(id, list, props);
        return list;
    }

    /** Rows before the first data row: the pull-to-refresh header, if any. */
    private int rowOffset(LinearLayout list) {
        ListState state = lists.get(list);
        return state != null && state.header != null ? 1 : 0;
    }

    /** The spinner row a refreshable List keeps (collapsed) above its rows. */
    private FrameLayout buildRefreshHeader() {
        FrameLayout header = new FrameLayout(context);
        ProgressBar spinner = new ProgressBar(context);
        spinner.setIndeterminate(true);
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(dp(32), dp(32));
        params.gravity = Gravity.CENTER;
        header.addView(spinner, params);
        header.setClipChildren(true);
        return header;
    }

    private void setHeaderHeight(ListState state, int height) {
        if (state.header == null) {
            return;
        }
        ViewGroup.LayoutParams params = state.header.getLayoutParams();
        if (params == null || params.height == height) {
            return;
        }
        params.height = height;
        state.header.setLayoutParams(params);
        View spinner = state.header.getChildCount() > 0 ? state.header.getChildAt(0) : null;
        if (spinner != null) {
            spinner.setAlpha(Math.min(1f, height / (float) dp(56)));
        }
    }

    private void animateHeader(final ListState state, int target) {
        if (state.header == null) {
            return;
        }
        if (state.headerAnimator != null) {
            state.headerAnimator.cancel();
        }
        ViewGroup.LayoutParams params = state.header.getLayoutParams();
        int from = params == null ? 0 : Math.max(0, params.height);
        if (from == target) {
            return;
        }
        ValueAnimator animator = ValueAnimator.ofInt(from, target);
        animator.setDuration(180);
        animator.addUpdateListener(new ValueAnimator.AnimatorUpdateListener() {
            @Override
            public void onAnimationUpdate(ValueAnimator animation) {
                setHeaderHeight(state, (Integer) animation.getAnimatedValue());
            }
        });
        state.headerAnimator = animator;
        animator.start();
    }

    /** Show or hide the spinner as Python asks (refreshing = True/False). */
    private void applyRefreshing(ListState state, boolean refreshing, boolean animate) {
        state.refreshing = refreshing;
        int target = refreshing ? dp(56) : 0;
        if (animate) {
            animateHeader(state, target);
        } else {
            setHeaderHeight(state, target);
        }
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
    private final class PullList extends LinearLayout {
        private final String listId;
        private final int touchSlop;
        private float downX;
        private float downY;
        private boolean armed;
        private boolean pulling;

        PullList(String listId) {
            super(context);
            this.listId = listId;
            this.touchSlop = ViewConfiguration.get(context).getScaledTouchSlop();
        }

        @Override
        public boolean dispatchTouchEvent(MotionEvent event) {
            ListState state = lists.get(this);
            if (state == null || state.header == null || !isEnabled()) {
                return super.dispatchTouchEvent(event);
            }
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downX = event.getRawX();
                    downY = event.getRawY();
                    pulling = false;
                    armed = !state.refreshing && atTop();
                    if (armed && getParent() != null) {
                        // Keep the ScrollView from taking a downward drag at
                        // the top (it would only draw its overscroll glow).
                        getParent().requestDisallowInterceptTouchEvent(true);
                    }
                    break;
                case MotionEvent.ACTION_MOVE: {
                    float dy = event.getRawY() - downY;
                    float dx = event.getRawX() - downX;
                    if (armed && !pulling) {
                        if (dy > touchSlop && dy > Math.abs(dx)) {
                            pulling = true;
                            // The row under the finger must not see a tap.
                            MotionEvent cancel = MotionEvent.obtain(event);
                            cancel.setAction(MotionEvent.ACTION_CANCEL);
                            super.dispatchTouchEvent(cancel);
                            cancel.recycle();
                        } else if (dy < -touchSlop) {
                            // Scrolling the content up: give it back.
                            armed = false;
                            if (getParent() != null) {
                                getParent().requestDisallowInterceptTouchEvent(false);
                            }
                        } else if (Math.abs(dx) > touchSlop) {
                            armed = false;  // a sideways swipe of a row
                        }
                    }
                    if (pulling) {
                        int height = (int) Math.max(0, Math.min(dy * 0.5f, dp(120)));
                        if (state.headerAnimator != null) {
                            state.headerAnimator.cancel();
                        }
                        setHeaderHeight(state, height);
                        return true;
                    }
                    break;
                }
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (pulling) {
                        pulling = false;
                        armed = false;
                        ViewGroup.LayoutParams params = state.header.getLayoutParams();
                        boolean release = event.getActionMasked() == MotionEvent.ACTION_UP
                                && params != null && params.height >= dp(64);
                        if (release) {
                            state.refreshing = true;
                            animateHeader(state, dp(56));
                            performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                            Native.dispatchEvent(listId, "refresh", "");
                        } else {
                            animateHeader(state, 0);
                        }
                        return true;
                    }
                    armed = false;
                    break;
                default:
                    break;
            }
            return super.dispatchTouchEvent(event);
        }

        /** Whether the enclosing ScrollView (if any) is scrolled to the top. */
        private boolean atTop() {
            ViewParent parent = getParent();
            while (parent instanceof View) {
                if (parent instanceof ScrollView) {
                    return !((View) parent).canScrollVertically(-1);
                }
                parent = parent.getParent();
            }
            return true;
        }
    }

    /** Scroll to the row a List.scroll_to() asked for, once per request. */
    private void applyScrollRequest(String id, final LinearLayout list, JSONObject props) {
        int serial = props.optInt("scroll_serial", 0);
        final int index = props.optInt("scroll_to", -1);
        if (serial <= 0 || index < 0) {
            return;
        }
        Integer seen = scrollSerials.get(id);
        if (seen != null && seen == serial) {
            return;
        }
        scrollSerials.put(id, serial);
        final boolean animated = props.optBoolean("scroll_animated", true);
        // Posted: rows appended in this frame are laid out first.
        list.post(new Runnable() {
            @Override
            public void run() {
                scrollToRow(list, index, animated);
            }
        });
    }

    private void scrollToRow(LinearLayout list, int index, boolean animated) {
        int child = index + rowOffset(list);
        if (child < 0 || child >= list.getChildCount()) {
            return;
        }
        View node = list.getChildAt(child);
        int y = 0;
        ViewParent parent = node.getParent();
        while (parent instanceof View && !(parent instanceof ScrollView)) {
            y += node.getTop();
            node = (View) parent;
            parent = node.getParent();
        }
        if (!(parent instanceof ScrollView)) {
            return;  // not inside a ScrollView: nothing to scroll
        }
        y += node.getTop();
        ScrollView scroll = (ScrollView) parent;
        if (animated) {
            scroll.smoothScrollTo(0, y);
        } else {
            scroll.scrollTo(0, y);
        }
    }

    /**
     * Sends "load_more" when the last built row of a List becomes visible.
     *
     * The List used to show its first page (visible_count rows) and nothing
     * else: List(10000, ...) was a list of 20. Scroll and layout changes are
     * observed on the window, so this works whichever ScrollView the list
     * sits in, and also when the first page does not fill the screen.
     */
    private void watchList(final String id, final LinearLayout list, JSONObject props) {
        final ViewTreeObserver.OnScrollChangedListener onScroll =
                new ViewTreeObserver.OnScrollChangedListener() {
                    @Override
                    public void onScrollChanged() {
                        maybeLoadMore(id, list);
                    }
                };
        final ViewTreeObserver.OnGlobalLayoutListener onLayout =
                new ViewTreeObserver.OnGlobalLayoutListener() {
                    @Override
                    public void onGlobalLayout() {
                        maybeLoadMore(id, list);
                    }
                };
        list.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
            @Override
            public void onViewAttachedToWindow(View v) {
                ViewTreeObserver observer = v.getViewTreeObserver();
                observer.addOnScrollChangedListener(onScroll);
                observer.addOnGlobalLayoutListener(onLayout);
            }

            @Override
            public void onViewDetachedFromWindow(View v) {
                ViewTreeObserver observer = v.getViewTreeObserver();
                observer.removeOnScrollChangedListener(onScroll);
                observer.removeOnGlobalLayoutListener(onLayout);
            }
        });
    }

    private void maybeLoadMore(String id, LinearLayout list) {
        ListState state = lists.get(list);
        if (state == null || !state.hasMore || !list.isShown()) {
            return;
        }
        int offset = state.header != null ? 1 : 0;
        int count = list.getChildCount() - offset;
        // One request per page: the count only changes when Python appended rows.
        if (count <= 0 || state.requestedAt == count) {
            return;
        }
        Rect visible = new Rect();
        if (list.getChildAt(offset + count - 1).getGlobalVisibleRect(visible)) {
            state.requestedAt = count;
            Native.dispatchEvent(id, "load_more", String.valueOf(count));
        }
    }

    /** Patch a List in place: update built rows, append/remove the difference. */
    private boolean updateList(LinearLayout list, JSONObject node, JSONObject props)
            throws JSONException {
        ListState state = lists.get(list);
        if (state == null) {
            return false;
        }
        if ((state.header != null) != props.optBoolean("refreshable", false)) {
            return false;  // on_refresh added or removed: rebuild with/without header
        }
        int offset = state.header != null ? 1 : 0;
        JSONArray children = node.optJSONArray("children");
        int count = children == null ? 0 : children.length();
        int existing = list.getChildCount() - offset;
        if (count < existing) {
            list.removeViews(offset + count, existing - count);
            existing = count;
        }
        for (int i = 0; i < existing; i++) {
            if (!updateNode(list.getChildAt(offset + i), children.getJSONObject(i))) {
                return false;
            }
        }
        int spacing = dp(props.optInt("spacing", 0));
        for (int i = existing; i < count; i++) {
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            if (spacing > 0 && i > 0) {
                params.topMargin = spacing;
            }
            list.addView(buildChild(children.getJSONObject(i)), params);
        }
        state.hasMore = props.optBoolean("has_more", false);
        if (state.header != null) {
            boolean refreshing = props.optBoolean("refreshing", false);
            ViewGroup.LayoutParams params = state.header.getLayoutParams();
            int shown = params == null ? 0 : params.height;
            boolean settled = shown == (refreshing ? dp(56) : 0);
            if (refreshing != state.refreshing || !settled) {
                applyRefreshing(state, refreshing, true);
            }
        }
        applyScrollRequest(node.optString("id", ""), list, props);
        return true;
    }

    /** A tappable list row: title + subtitle + trailing, dispatching "press". */
    private View buildListTile(final String id, JSONObject props) {
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(dp(12), dp(10), dp(12), dp(10));
        // A subtle ripple requires a background; use a selectable borderless item.
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            android.content.res.TypedArray a = context.obtainStyledAttributes(
                    new int[]{android.R.attr.selectableItemBackground});
            row.setBackgroundResource(a.getResourceId(0, 0));
            a.recycle();
        }

        LinearLayout texts = new LinearLayout(context);
        texts.setOrientation(LinearLayout.VERTICAL);
        // The subtitle and trailing views always exist (GONE when empty): the
        // row's structure never changes, so every field can be patched.
        TextView title = new TextView(context);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        texts.addView(title);
        TextView sub = new TextView(context);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        texts.addView(sub);

        LinearLayout.LayoutParams textsParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        row.addView(texts, textsParams);

        TextView trailingView = new TextView(context);
        trailingView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        row.addView(trailingView);

        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        applyListTile(row, id, props);
        return row;
    }

    /** Fill (or re-fill) every part of a ListTile row. */
    private boolean applyListTile(ViewGroup row, String id, JSONObject props) {
        if (row.getChildCount() != 2
                || !(row.getChildAt(0) instanceof ViewGroup)
                || !(row.getChildAt(1) instanceof TextView)) {
            return false;
        }
        ViewGroup texts = (ViewGroup) row.getChildAt(0);
        if (texts.getChildCount() != 2
                || !(texts.getChildAt(0) instanceof TextView)
                || !(texts.getChildAt(1) instanceof TextView)) {
            return false;
        }
        TextView title = (TextView) texts.getChildAt(0);
        TextView sub = (TextView) texts.getChildAt(1);
        TextView trailing = (TextView) row.getChildAt(1);
        setTextAndVisibility(title, props.optString("title", ""), true);
        setTextAndVisibility(sub, props.optString("subtitle", ""), false);
        setTextAndVisibility(trailing, props.optString("trailing", ""), false);
        title.setTextColor(colorText);
        sub.setTextColor(colorTextMuted);
        trailing.setTextColor(colorTextMuted);
        setLongPress(row, id, props.optBoolean("long_pressable", false));
        setSwipe(row, id, props);
        return true;
    }

    /** Configure (or remove) the sideways swipe of a ListTile row. */
    private void setSwipe(View row, String id, JSONObject props) {
        boolean left = props.optBoolean("swipe_left", false);
        boolean right = props.optBoolean("swipe_right", false);
        SwipeState state = swipes.get(row);
        if (!left && !right) {
            if (state != null) {
                resetSwipe(row, state, false);
                swipes.remove(row);
            }
            row.setOnTouchListener(null);
            return;
        }
        if (state == null) {
            state = new SwipeState();
            swipes.put(row, state);
            row.setOnTouchListener(swipeListener);
        }
        state.id = id;
        state.left = left;
        state.right = right;
        state.leftColor = parseColor(props.optString("swipe_left_color", ""), state.leftColor);
        state.rightColor = parseColor(props.optString("swipe_right_color", ""), state.rightColor);
        // A redraw after the handler ran (the row now shows the next item, or
        // the same one if nothing was deleted) puts the row back in place.
        if (!state.dragging && !state.committing) {
            resetSwipe(row, state, false);
        }
    }

    private void resetSwipe(View row, SwipeState state, boolean animate) {
        row.animate().cancel();
        if (animate && row.getTranslationX() != 0f) {
            row.animate().translationX(0f).alpha(1f).setDuration(150).start();
        } else {
            row.setTranslationX(0f);
            row.setAlpha(1f);
        }
        if (state.hasSaved) {
            row.setBackground(state.saved);
            state.saved = null;
            state.hasSaved = false;
        }
    }

    private static int withAlpha(int color, float alpha) {
        int a = Math.max(0, Math.min(255, Math.round(255 * alpha)));
        return (color & 0x00FFFFFF) | (a << 24);
    }

    /**
     * One listener for every swipeable row; the per-row configuration lives
     * in the swipes table. A drag only starts once the finger moved sideways
     * clearly more than vertically, so scrolling the list is unaffected, and
     * the row's tap and long-press are cancelled when it does.
     */
    private final View.OnTouchListener swipeListener = new View.OnTouchListener() {
        @Override
        public boolean onTouch(final View row, MotionEvent event) {
            final SwipeState state = swipes.get(row);
            if (state == null || !row.isEnabled() || state.committing) {
                return false;
            }
            int slop = ViewConfiguration.get(context).getScaledTouchSlop();
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    state.downX = event.getRawX();
                    state.downY = event.getRawY();
                    state.dragging = false;
                    if (state.velocity != null) {
                        state.velocity.recycle();
                    }
                    state.velocity = VelocityTracker.obtain();
                    state.velocity.addMovement(event);
                    return false;
                case MotionEvent.ACTION_MOVE: {
                    if (state.velocity != null) {
                        state.velocity.addMovement(event);
                    }
                    float dx = event.getRawX() - state.downX;
                    float dy = event.getRawY() - state.downY;
                    if (!state.dragging) {
                        boolean allowed = (dx < 0 && state.left) || (dx > 0 && state.right);
                        if (!allowed || Math.abs(dx) <= slop || Math.abs(dx) < Math.abs(dy) * 1.5f) {
                            return false;
                        }
                        state.dragging = true;
                        if (row.getParent() != null) {
                            row.getParent().requestDisallowInterceptTouchEvent(true);
                        }
                        row.cancelLongPress();
                        MotionEvent cancel = MotionEvent.obtain(event);
                        cancel.setAction(MotionEvent.ACTION_CANCEL);
                        row.onTouchEvent(cancel);
                        cancel.recycle();
                        state.saved = row.getBackground();
                        state.hasSaved = true;
                    }
                    float tx = dx;
                    if ((tx < 0 && !state.left) || (tx > 0 && !state.right)) {
                        tx = 0;
                    }
                    row.setTranslationX(tx);
                    float third = Math.max(1f, row.getWidth() / 3f);
                    int color = tx < 0 ? state.leftColor : state.rightColor;
                    row.setBackgroundColor(withAlpha(color, Math.min(1f, Math.abs(tx) / third) * 0.85f));
                    return true;
                }
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL: {
                    if (!state.dragging) {
                        return false;
                    }
                    state.dragging = false;
                    float tx = row.getTranslationX();
                    float vx = 0f;
                    if (state.velocity != null) {
                        state.velocity.addMovement(event);
                        state.velocity.computeCurrentVelocity(1000);
                        vx = state.velocity.getXVelocity();
                        state.velocity.recycle();
                        state.velocity = null;
                    }
                    int minFling = ViewConfiguration.get(context).getScaledMinimumFlingVelocity() * 8;
                    boolean flung = Math.abs(vx) > minFling && Math.signum(vx) == Math.signum(tx)
                            && Math.abs(tx) > slop * 2;
                    boolean commit = event.getActionMasked() == MotionEvent.ACTION_UP
                            && tx != 0f && (Math.abs(tx) > row.getWidth() / 3f || flung);
                    if (!commit) {
                        resetSwipe(row, state, true);
                        return true;
                    }
                    final String direction = tx < 0 ? "left" : "right";
                    final String rowId = state.id;
                    state.committing = true;
                    row.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                    row.animate()
                            .translationX(Math.signum(tx) * row.getWidth())
                            .alpha(0.4f)
                            .setDuration(160)
                            .withEndAction(new Runnable() {
                                @Override
                                public void run() {
                                    state.committing = false;
                                    Native.dispatchEvent(rowId, "swipe", direction);
                                    // If the handler left the item in place (no
                                    // redraw), slide the row back after a moment.
                                    row.postDelayed(new Runnable() {
                                        @Override
                                        public void run() {
                                            if (!state.dragging && !state.committing
                                                    && row.getTranslationX() != 0f) {
                                                resetSwipe(row, state, true);
                                            }
                                        }
                                    }, 700);
                                }
                            })
                            .start();
                    return true;
                }
                default:
                    return false;
            }
        }
    };

    private void setLongPress(View row, final String id, boolean enabled) {
        if (enabled) {
            row.setOnLongClickListener(new View.OnLongClickListener() {
                @Override
                public boolean onLongClick(View v) {
                    v.performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
                    Native.dispatchEvent(id, "long_press", "");
                    return true;
                }
            });
        } else {
            row.setOnLongClickListener(null);
            row.setLongClickable(false);
        }
    }

    private View buildProgress(JSONObject props) {
        boolean indeterminate = props.optBoolean("indeterminate", false);
        ProgressBar bar = new ProgressBar(
                context, null,
                indeterminate ? android.R.attr.progressBarStyle
                              : android.R.attr.progressBarStyleHorizontal);
        bar.setIndeterminate(indeterminate);
        if (!indeterminate) {
            setScaledProgress(bar, props);
        }
        return bar;
    }

    private View buildImage(JSONObject props) {
        ImageView image = new ImageView(context);
        applyImage(image, props);
        return image;
    }

    /** Load the image source and fit; used for the first build and for patches. */
    private void applyImage(ImageView image, JSONObject props) {
        image.setScaleType("cover".equals(props.optString("fit", "contain"))
                ? ImageView.ScaleType.CENTER_CROP
                : ImageView.ScaleType.FIT_CENTER);
        String source = props.optString("source", "");
        if (source.equals(imageSources.get(image))) {
            return;
        }
        imageSources.put(image, source);
        image.setImageDrawable(null);
        try {
            java.io.File file = new java.io.File(source);
            if (!file.isAbsolute()) {
                file = new java.io.File(context.getFilesDir(), "pymobile/app/" + source);
            }
            if (file.exists()) {
                android.graphics.Bitmap bitmap =
                        android.graphics.BitmapFactory.decodeFile(file.getAbsolutePath());
                if (bitmap == null) {
                    android.util.Log.w("pymobile", "Image decode failed: " + source);
                } else {
                    image.setImageBitmap(bitmap);
                }
            } else if (source.startsWith("http://") || source.startsWith("https://")
                    || source.startsWith("data:")) {
                // The Python-side validator accepts http(s)/data sources, but
                // this renderer only draws local files. Surface that instead of
                // silently showing an empty view.
                android.util.Log.w("pymobile", "Image source not supported on "
                        + "device (only local files): "
                        + source.substring(0, Math.min(source.length(), 64)));
            } else {
                android.util.Log.w("pymobile", "Image file not found: " + source);
            }
        } catch (RuntimeException error) {
            // A broken image must not take the whole screen down.
            android.util.Log.w("pymobile", "Image failed to load: " + source, error);
        }
    }

    private View buildSpacer(JSONObject props) {
        // No setLayoutParams here: the parent container assigns params of its
        // own type. A bare ViewGroup.LayoutParams would make LinearLayout throw
        // ClassCastException and silently drop the rest of the tree.
        View spacer = new View(context);
        spacer.setMinimumWidth(dp(props.optInt("size", 8)));
        spacer.setMinimumHeight(dp(props.optInt("size", 8)));
        return spacer;
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

    private boolean updateNode(View view, JSONObject node) throws JSONException {
        String type = node.optString("type", "Label");
        String id = node.optString("id", "");
        if (view == null || !id.equals(view.getTag())) {
            return false;
        }

        JSONObject props = node.optJSONObject("props");
        if (props == null) {
            props = new JSONObject();
        }

        view.setEnabled(node.optBoolean("enabled", true));
        view.setVisibility(node.optBoolean("visible", true) ? View.VISIBLE : View.GONE);

        // Re-apply style (background, padding, elevation, ...) so theme switches
        // and styling changes are reflected without a full rebuild.
        applyStyle(view, node.optJSONObject("style"));

        // Composite views built from props (they serialise as leaves, or have
        // extra native children) must be handled before the generic ViewGroup
        // walk below: it compares native and tree child counts, and a mismatch
        // makes the caller rebuild the ENTIRE screen — closing the keyboard,
        // resetting scroll and losing widget state on every render.

        if ("Dialog".equals(type)) {
            DialogHost host = dialogs.get(view);
            if (host == null) {
                return false;
            }
            host.wanted = node.optBoolean("visible", true);
            view.setVisibility(View.GONE);
            applyDialogProps(host, props);
            applyStyle(host.box, node.optJSONObject("style"));
            JSONArray children = node.optJSONArray("children");
            int count = children == null ? 0 : children.length();
            // +1: the title TextView is always the first child of the box.
            if (host.box.getChildCount() != count + 1) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (!updateNode(host.box.getChildAt(i + 1), children.getJSONObject(i))) {
                    return false;
                }
            }
            syncDialog(view);
            return true;
        }

        if ("List".equals(type) && view instanceof LinearLayout) {
            // A new page appends rows; the generic walk below would see a
            // different child count and rebuild the screen (scroll back to top).
            return updateList((LinearLayout) view, node, props);
        }

        if ("ListTile".equals(type) && view instanceof ViewGroup) {
            // Title, subtitle, trailing and long-press are all patched: row ids
            // are positional, so after a deletion a row shows another item.
            return applyListTile((ViewGroup) view, id, props);
        }

        if ("BottomNavigation".equals(type) && view instanceof ViewGroup) {
            ViewGroup bar = (ViewGroup) view;
            JSONArray options = props.optJSONArray("options");
            int count = options == null ? 0 : options.length();
            if (bar.getChildCount() != count) {
                return false;  // the set of tabs changed
            }
            String value = props.optString("value", "");
            for (int i = 0; i < count; i++) {
                View child = bar.getChildAt(i);
                if (!(child instanceof Button)) {
                    return false;
                }
                Button tab = (Button) child;
                String label = options.optString(i, "");
                if (!label.contentEquals(tab.getText())) {
                    tab.setText(label);
                }
                styleTab(tab, label.equals(value));
            }
            return true;
        }

        if ("Stepper".equals(type) && view instanceof ViewGroup) {
            // Layout: [minus button][value text][plus button]. Only the middle
            // view shows the value — the buttons are TextViews too, and
            // writing to every TextView turned "− 10 +" into "10 10 10".
            ViewGroup group = (ViewGroup) view;
            if (group.getChildCount() != 3 || !(group.getChildAt(1) instanceof TextView)) {
                return false;
            }
            TextView value = (TextView) group.getChildAt(1);
            String text = String.valueOf(props.optInt("value", 0));
            if (!text.contentEquals(value.getText())) {
                value.setText(text);
            }
            return true;
        }

        if ("ProgressText".equals(type) && view instanceof ViewGroup) {
            ViewGroup box = (ViewGroup) view;
            if (box.getChildCount() != 2
                    || !(box.getChildAt(0) instanceof ProgressBar)
                    || !(box.getChildAt(1) instanceof TextView)) {
                return false;
            }
            setScaledProgress((ProgressBar) box.getChildAt(0), props);
            setTextAndVisibility((TextView) box.getChildAt(1), props.optString("text", ""), false);
            return true;
        }

        if (view instanceof Spinner) {
            // A Spinner is an AdapterView, i.e. a ViewGroup holding its item
            // view; the generic walk saw one native child against zero in the
            // tree and forced a full rebuild. Sync the selection instead.
            Spinner spinner = (Spinner) view;
            JSONArray options = props.optJSONArray("options");
            int count = options == null ? 0 : options.length();
            android.widget.SpinnerAdapter adapter = spinner.getAdapter();
            if (adapter == null || adapter.getCount() != count) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (!options.optString(i, "").equals(String.valueOf(adapter.getItem(i)))) {
                    return false;  // options changed: rebuild with the new list
                }
            }
            String selected = props.optString("value", "");
            for (int i = 0; i < count; i++) {
                if (options.optString(i, "").equals(selected)) {
                    if (spinner.getSelectedItemPosition() != i) {
                        spinner.setSelection(i);
                    }
                    break;
                }
            }
            return true;
        }

        if ("SegmentedButtons".equals(type) && view instanceof ViewGroup) {
            ViewGroup group = (ViewGroup) view;
            String selected = props.optString("value", "");
            for (int i = 0; i < group.getChildCount(); i++) {
                View child = group.getChildAt(i);
                if (child instanceof TextView) {
                    boolean isSelected = ((TextView) child).getText().toString()
                            .equals(selected);
                    if (isSelected) {
                        child.setBackgroundColor(colorPrimary);
                        ((TextView) child).setTextColor(colorOnPrimary);
                    } else {
                        child.setBackgroundColor(Color.TRANSPARENT);
                        ((TextView) child).setTextColor(colorText);
                    }
                }
            }
            return true;
        }
        if ("DataTable".equals(type) && view instanceof ViewGroup) {
            // The Python side mutates rows/headers in place and the table
            // serialises as a leaf, so patch it live — otherwise add_row()
            // and cell edits never reach the screen.
            return updateDataTable((ViewGroup) view, props);
        }

        if (view instanceof ViewGroup) {
            JSONArray children = node.optJSONArray("children");
            ViewGroup group = (ViewGroup) view;

            // A Grid nests its cells in one LinearLayout per row, so the flat
            // list of children in the tree has to be walked row by row.
            if ("Grid".equals(type)) {
                return updateGrid(group, props, children);
            }

            ViewGroup target = group;
            // ScrollView wraps its content in a LinearLayout.
            if ((group instanceof ScrollView || group instanceof HorizontalScrollView)
                    && group.getChildCount() == 1
                    && group.getChildAt(0) instanceof ViewGroup) {
                target = (ViewGroup) group.getChildAt(0);
            }
            int count = children == null ? 0 : children.length();
            if (target.getChildCount() != count) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (!updateNode(target.getChildAt(i), children.getJSONObject(i))) {
                    return false;
                }
            }
            return true;
        }

        if (view instanceof RadioGroup) {
            // Each radio is patched from its own node (its `selected` and
            // `text`). Matching the group's `value` against the labels checked
            // every radio that shared the selected text.
            RadioGroup group = (RadioGroup) view;
            JSONArray radios = node.optJSONArray("children");
            int count = radios == null ? 0 : radios.length();
            if (group.getChildCount() != count) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (!updateNode(group.getChildAt(i), radios.getJSONObject(i))) {
                    return false;
                }
            }
            return true;
        }

        if (view instanceof RadioButton) {
            RadioButton radio = (RadioButton) view;
            boolean selected = props.optBoolean("selected", false);
            if (radio.isChecked() != selected) {
                radio.setChecked(selected);
            }
            String text = props.optString("text", "");
            if (!String.valueOf(radio.getText()).equals(text)) {
                radio.setText(text);
            }
            return true;
        }

        if (view instanceof Switch) {
            // Switch extends CompoundButton extends Button: this must run
            // before the Button branch, which used to swallow it (setText("")
            // and `checked` never synced — the old Switch branch was dead code).
            Switch toggle = (Switch) view;
            boolean checked = props.optBoolean("checked", false);
            if (toggle.isChecked() != checked) {
                toggle.setChecked(checked);
            }
            return true;
        }

        if (view instanceof CheckBox) {
            // CheckBox extends CompoundButton extends Button, so this must come
            // before the Button branch. CompoundButton has no public getter for
            // its listener, so we cannot save/restore it; setChecked here is a
            // programmatic sync and the Python side guards against echo loops.
            CheckBox checkbox = (CheckBox) view;
            boolean checked = props.optBoolean("checked", false);
            if (checkbox.isChecked() != checked) {
                checkbox.setChecked(checked);
            }
            return true;
        }
        if (view instanceof SeekBar) {
            applySliderScale((SeekBar) view, props);
            return true;
        }
        if (view instanceof RatingBar) {
            RatingBar rating = (RatingBar) view;
            float value = (float) props.optDouble("rating", 0);
            if (Math.abs(rating.getRating() - value) > 1e-3f) {
                rating.setOnRatingBarChangeListener(null);
                rating.setRating(value);
                // Re-attach is intentionally skipped: the build path owns the
                // listener and a partial tree rebuild re-creates the view.
            }
            return true;
        }
        if (("DatePicker".equals(type) || "TimePicker".equals(type)) && view instanceof Button) {
            // Pickers are Buttons without a "text" prop: the Button branch
            // below blanked them on every patch.
            String value = props.optString("value", "");
            String shown = !value.isEmpty() ? value
                    : ("DatePicker".equals(type) ? "Pick date" : "Pick time");
            if (!shown.contentEquals(((Button) view).getText())) {
                ((Button) view).setText(shown);
            }
            return true;
        }
        if (view instanceof Button) {
            ((Button) view).setText(props.optString("text", ""));
            return true;
        }
        if (view instanceof View && "Divider".equals(type)) {
            view.setBackgroundColor(parseColor(props.optString("color", "#1F000000"),
                    Color.parseColor("#1F000000")));
            return true;
        }
        if (view instanceof EditText) {
            // A focused field is not overwritten with an echo of what the user
            // typed a moment ago (that moved the caret mid-typing). But when
            // Python changed the value itself — clear() after "Send" — the
            // widget's revision is bumped and the new text is applied even
            // while the field has focus.
            EditText input = (EditText) view;
            String value = props.optString("value", "");
            int revision = props.optInt("revision", 0);
            Integer known = inputRevisions.get(input);
            boolean programmatic = known == null || known != revision;
            inputRevisions.put(input, revision);
            if (!value.contentEquals(input.getText()) && (programmatic || !input.hasFocus())) {
                input.setText(value);
                if (input.hasFocus()) {
                    input.setSelection(input.getText().length());
                }
            }
            String hint = props.optString("placeholder", "");
            CharSequence currentHint = input.getHint();
            if (!hint.contentEquals(currentHint == null ? "" : currentHint)) {
                input.setHint(hint);
            }
            return true;
        }
        if (view instanceof ProgressBar) {
            ProgressBar bar = (ProgressBar) view;
            if (bar.isIndeterminate() != props.optBoolean("indeterminate", false)) {
                return false;  // a different kind of bar: rebuild
            }
            if (!bar.isIndeterminate()) {
                setScaledProgress(bar, props);
            }
            return true;
        }
        if (view instanceof ImageView) {
            applyImage((ImageView) view, props);
            return true;
        }
        if (view instanceof TextView) {
            ((TextView) view).setText(props.optString("text", ""));
            return true;
        }
        return true;
    }

    /** Patch a grid in place, mapping the flat child list onto its rows. */
    private boolean updateGrid(ViewGroup grid, JSONObject props, JSONArray children)
            throws JSONException {
        int columns = Math.max(1, props.optInt("columns", 2));
        int count = children == null ? 0 : children.length();
        int expectedRows = (count + columns - 1) / columns;
        if (grid.getChildCount() != expectedRows) {
            return false;
        }
        for (int rowIndex = 0; rowIndex < expectedRows; rowIndex++) {
            View row = grid.getChildAt(rowIndex);
            if (!(row instanceof ViewGroup)) {
                return false;
            }
            ViewGroup cells = (ViewGroup) row;
            if (cells.getChildCount() != columns) {
                return false;
            }
            for (int column = 0; column < columns; column++) {
                int index = rowIndex * columns + column;
                if (index >= count) {
                    break;  // the padding cells of the last row carry no state
                }
                if (!updateNode(cells.getChildAt(column), children.getJSONObject(index))) {
                    return false;
                }
            }
        }
        return true;
    }

    // -- styling ----------------------------------------------------------

    private void applyStyle(View view, JSONObject style) {
        if (view instanceof TextView) {
            // Runs even without a style so that removing bold/italic resets
            // the typeface. setTypeface(tf, NORMAL) keeps a bold tf bold, so
            // the variant is created explicitly.
            TextView text = (TextView) view;
            boolean bold = style != null && style.optBoolean("bold", false);
            boolean italic = style != null && style.optBoolean("italic", false);
            int flags = (bold ? Typeface.BOLD : 0) | (italic ? Typeface.ITALIC : 0);
            Typeface current = text.getTypeface();
            int currentFlags = current == null ? Typeface.NORMAL : current.getStyle();
            if (currentFlags != flags) {
                text.setTypeface(Typeface.create(current, flags));
            }
        }
        if (style == null) {
            return;
        }
        if (view instanceof TextView) {
            TextView text = (TextView) view;
            if (style.has("color")) {
                text.setTextColor(parseColor(style.optString("color"), colorText));
            }
            if (style.has("font_size")) {
                text.setTextSize(TypedValue.COMPLEX_UNIT_SP, (float) style.optDouble("font_size"));
            }
            String align = style.optString("align", "");
            if ("center".equals(align)) {
                text.setGravity(Gravity.CENTER);
            } else if ("end".equals(align) || "right".equals(align)) {
                text.setGravity(Gravity.END | Gravity.CENTER_VERTICAL);
            } else if ("start".equals(align) || "left".equals(align)) {
                text.setGravity(Gravity.START | Gravity.CENTER_VERTICAL);
            }
        }

        JSONArray padding = style.optJSONArray("padding");
        if (padding != null && padding.length() == 4) {
            view.setPadding(dp(padding.optInt(0)), dp(padding.optInt(1)),
                    dp(padding.optInt(2)), dp(padding.optInt(3)));
        }

        if (style.has("background") || style.has("corner_radius")) {
            GradientDrawable shape = new GradientDrawable();
            shape.setColor(parseColor(style.optString("background", "#00000000"),
                    Color.TRANSPARENT));
            shape.setCornerRadius(dp(style.optInt("corner_radius", 0)));
            view.setBackground(shape);
        }

        // margin, width and height are applied in applyBoxStyle() instead:
        // this method runs before the view has a parent, so its layout params
        // do not exist yet and anything written here would be discarded.

        if (style.has("elevation") && android.os.Build.VERSION.SDK_INT >= 21) {
            float elevation = dp(style.optInt("elevation"));
            view.setElevation(elevation);
            // A shadow needs something opaque to fall from; a view with no
            // background casts none, which looks like elevation being ignored.
            if (!style.has("background")) {
                view.setBackgroundColor(colorSurface);
            }
        }

        applyConstraints(view, style);
    }

    /**
     * Size constraints: min/max width and height, plus aspect ratio.
     *
     * Android has no single API for this, so each piece uses the mechanism
     * that actually works on a plain View: minimums are view properties,
     * maximums and the ratio are enforced by a layout listener that clamps the
     * measured size once the parent has laid the view out.
     */
    private void applyConstraints(final View view, final JSONObject style) {
        boolean hasMin = style.has("min_width") || style.has("min_height");
        boolean hasMax = style.has("max_width") || style.has("max_height");
        boolean hasRatio = style.has("aspect_ratio");
        if (!hasMin && !hasMax && !hasRatio) {
            return;
        }

        if (style.has("min_width")) {
            view.setMinimumWidth(dp(style.optInt("min_width")));
        }
        if (style.has("min_height")) {
            view.setMinimumHeight(dp(style.optInt("min_height")));
        }
        if (!hasMax && !hasRatio) {
            return;
        }

        final int maxWidth = style.has("max_width") ? dp(style.optInt("max_width")) : 0;
        final int maxHeight = style.has("max_height") ? dp(style.optInt("max_height")) : 0;
        final double ratio = style.optDouble("aspect_ratio", 0);

        view.addOnLayoutChangeListener(new View.OnLayoutChangeListener() {
            @Override
            public void onLayoutChange(View v, int left, int top, int right, int bottom,
                    int oldLeft, int oldTop, int oldRight, int oldBottom) {
                ViewGroup.LayoutParams params = v.getLayoutParams();
                if (params == null) {
                    return;
                }
                int width = right - left;
                int height = bottom - top;
                int wantWidth = width;
                int wantHeight = height;

                if (maxWidth > 0 && wantWidth > maxWidth) {
                    wantWidth = maxWidth;
                }
                if (maxHeight > 0 && wantHeight > maxHeight) {
                    wantHeight = maxHeight;
                }
                if (ratio > 0 && wantWidth > 0) {
                    wantHeight = (int) Math.round(wantWidth / ratio);
                }
                if (wantWidth == width && wantHeight == height) {
                    return;
                }
                params.width = wantWidth;
                params.height = wantHeight;
                // requestLayout from inside a layout pass is dropped; post it.
                v.post(new Runnable() {
                    @Override
                    public void run() {
                        v.requestLayout();
                    }
                });
            }
        });
    }

    /** Parse #RGB / #RRGGBB / #AARRGGBB, falling back on anything unexpected. */
    private int parseColor(String value, int fallback) {
        if (value == null || value.isEmpty()) {
            return fallback;
        }
        try {
            return Color.parseColor(value);
        } catch (IllegalArgumentException error) {
            return fallback;
        }
    }

    // -- snackbar -----------------------------------------------------------
    // Not a widget of the tree: App.snackbar() sends it next to the tree as
    // root["snackbar"], and it floats over the screen at the bottom.

    private static final String SNACKBAR_ID = "__snackbar__";
    private LinearLayout snackbar;
    private TextView snackbarText;
    private TextView snackbarAction;
    private int snackbarToken = -1;
    private boolean snackbarHiding;

    /** Whether a view is the snackbar (MainActivity keeps it when rebuilding). */
    boolean isSnackbar(View view) {
        return view != null && view == snackbar;
    }

    /** Show, update or hide the snackbar described by root["snackbar"]. */
    void syncSnackbar(FrameLayout container, JSONObject data) {
        if (data == null) {
            hideSnackbar();
            return;
        }
        if (snackbar == null) {
            createSnackbar();
        }
        if (snackbar.getParent() != container) {
            if (snackbar.getParent() instanceof ViewGroup) {
                ((ViewGroup) snackbar.getParent()).removeView(snackbar);
            }
            FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.BOTTOM);
            int margin = dp(12);
            params.setMargins(margin, margin, margin, margin);
            container.addView(snackbar, params);
        } else if (container.indexOfChild(snackbar) != container.getChildCount() - 1) {
            snackbar.bringToFront();
        }
        styleSnackbar();
        final int token = data.optInt("token", 0);
        snackbarText.setText(data.optString("message", ""));
        String action = data.optString("action", "");
        snackbarAction.setText(action);
        snackbarAction.setVisibility(action.isEmpty() ? View.GONE : View.VISIBLE);
        snackbarAction.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(SNACKBAR_ID, "press", String.valueOf(token));
            }
        });
        if (token != snackbarToken || snackbar.getVisibility() != View.VISIBLE || snackbarHiding) {
            snackbarToken = token;
            snackbarHiding = false;
            snackbar.animate().cancel();
            snackbar.setVisibility(View.VISIBLE);
            snackbar.setTranslationX(0f);
            snackbar.setAlpha(1f);
            snackbar.setTranslationY(dp(96));
            snackbar.animate().translationY(0f).setDuration(200).start();
        }
    }

    private void hideSnackbar() {
        if (snackbar == null || snackbar.getVisibility() != View.VISIBLE || snackbarHiding) {
            return;
        }
        snackbarHiding = true;
        snackbarToken = -1;
        snackbar.animate().cancel();
        snackbar.animate().translationY(dp(96)).alpha(0f).setDuration(180)
                .withEndAction(new Runnable() {
                    @Override
                    public void run() {
                        if (snackbarHiding) {
                            snackbar.setVisibility(View.GONE);
                            snackbarHiding = false;
                        }
                    }
                })
                .start();
    }

    private void createSnackbar() {
        snackbar = new LinearLayout(context);
        snackbar.setOrientation(LinearLayout.HORIZONTAL);
        snackbar.setGravity(Gravity.CENTER_VERTICAL);
        snackbar.setPadding(dp(16), dp(6), dp(8), dp(6));
        snackbar.setMinimumHeight(dp(48));
        snackbar.setVisibility(View.GONE);
        snackbar.setClickable(true);  // taps must not fall through to the screen
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            snackbar.setElevation(dp(6));
        }
        snackbarText = new TextView(context);
        snackbarText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        snackbarText.setMaxLines(2);
        snackbar.addView(snackbarText, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        snackbarAction = new TextView(context);
        snackbarAction.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        snackbarAction.setTypeface(Typeface.DEFAULT_BOLD);
        snackbarAction.setPadding(dp(12), dp(10), dp(12), dp(10));
        snackbarAction.setClickable(true);
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            android.content.res.TypedArray a = context.obtainStyledAttributes(
                    new int[]{android.R.attr.selectableItemBackground});
            snackbarAction.setBackgroundResource(a.getResourceId(0, 0));
            a.recycle();
        }
        snackbar.addView(snackbarAction, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        snackbar.setOnTouchListener(new View.OnTouchListener() {
            private float downX;
            private boolean dragging;

            @Override
            public boolean onTouch(View v, MotionEvent event) {
                switch (event.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                        downX = event.getRawX();
                        dragging = false;
                        return true;
                    case MotionEvent.ACTION_MOVE: {
                        float dx = event.getRawX() - downX;
                        if (!dragging && Math.abs(dx) > ViewConfiguration.get(context).getScaledTouchSlop()) {
                            dragging = true;
                        }
                        if (dragging) {
                            v.setTranslationX(dx);
                            v.setAlpha(Math.max(0.2f, 1f - Math.abs(dx) / Math.max(1f, v.getWidth())));
                        }
                        return true;
                    }
                    case MotionEvent.ACTION_UP:
                    case MotionEvent.ACTION_CANCEL: {
                        float dx = v.getTranslationX();
                        if (dragging && Math.abs(dx) > v.getWidth() / 3f) {
                            // Swiped away: gone for good, whatever its timer says.
                            final int token = snackbarToken;
                            snackbarHiding = true;
                            v.animate().translationX(Math.signum(dx) * v.getWidth()).alpha(0f)
                                    .setDuration(150).withEndAction(new Runnable() {
                                        @Override
                                        public void run() {
                                            snackbar.setVisibility(View.GONE);
                                            snackbarHiding = false;
                                            Native.dispatchEvent(SNACKBAR_ID, "dismiss",
                                                    String.valueOf(token));
                                        }
                                    }).start();
                        } else {
                            v.animate().translationX(0f).alpha(1f).setDuration(150).start();
                        }
                        dragging = false;
                        return true;
                    }
                    default:
                        return false;
                }
            }
        });
    }

    /** Inverse colours: a dark bar on a light theme and a light bar on a dark one. */
    private void styleSnackbar() {
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(dp(4));
        shape.setColor(darkTheme ? Color.parseColor("#E6E6E6") : Color.parseColor("#323232"));
        snackbar.setBackground(shape);
        snackbarText.setTextColor(darkTheme ? Color.parseColor("#212121") : Color.WHITE);
        int action = colorPrimary;
        if (!darkTheme) {
            // Lighten the primary colour so the action reads on the dark bar.
            int r = Color.red(action) + (255 - Color.red(action)) * 45 / 100;
            int g = Color.green(action) + (255 - Color.green(action)) * 45 / 100;
            int b = Color.blue(action) + (255 - Color.blue(action)) * 45 / 100;
            action = Color.rgb(r, g, b);
        }
        snackbarAction.setTextColor(action);
    }
}
