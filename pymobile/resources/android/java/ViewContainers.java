package org.pymobile.app;

import android.graphics.Color;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import org.pymobile.app.FlowViews.FlowLayout;




/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewContainers {
    private final ViewBuilder owner;

    ViewContainers(ViewBuilder owner) {
        this.owner = owner;
    }


    /** Build a child defensively: a failure must not hide the whole screen. */
    View buildChild(JSONObject node) {
        try {
            return owner.build(node);
        } catch (Exception error) {
            android.util.Log.e("pymobile", "widget failed: " + node.optString("type"), error);
            TextView fallback = new TextView(owner.context);
            fallback.setText("[" + node.optString("type") + ": " + error + "]");
            fallback.setTextColor(Color.parseColor("#C62828"));
            return fallback;
        }
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
        final boolean hasPress = props.optString("on_press", "").length() > 0;
        final boolean hasLongPress = props.optString("on_long_press", "").length() > 0;
        view.setClickable(enabled);
        if (hasPress) {
            view.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    Native.dispatchEvent(id, "press", "");
                }
            });
        }
        if (hasLongPress) {
            view.setOnLongClickListener(new View.OnLongClickListener() {
                @Override
                public boolean onLongClick(View v) {
                    Native.dispatchEvent(id, "long_press", "");
                    return true;
                }
            });
        }
    }

    View buildPageView(JSONObject node, JSONObject props) throws JSONException {
        AdvancedViews.PagerView view = new AdvancedViews.PagerView(owner.context);
        view.bind(node.optString("id", ""), props);
        JSONArray children = node.optJSONArray("children");
        if (children != null && children.length() == 1) {
            view.addView(owner.buildChild(children.getJSONObject(0)), new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        }
        return view;
    }

    // -- containers -------------------------------------------------------

    View buildLinear(JSONObject node, JSONObject props, int orientation)
            throws JSONException {
        LinearLayout layout = new LinearLayout(owner.context);
        layout.setOrientation(orientation);

        String align = props.optString("align", "start");
        // cross_align is optional: without it the historical defaults apply
        // (children stretch in a Column, sit centred in a Row).
        String crossAlign = props.optString("cross_align", "");
        layout.setGravity(owner.gravityFor(orientation, align, crossAlign));

        int spacing = owner.dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                View child = owner.buildChild(childNode);
                LinearLayout.LayoutParams params =
                        owner.childParams(childNode, orientation, crossAlign);
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

    View buildScroll(JSONObject node, JSONObject props) throws JSONException {
        boolean horizontal = props.optBoolean("horizontal", false);
        // setFillViewport lives on each concrete class, not on ViewGroup.
        ViewGroup scroller;
        if (horizontal) {
            HorizontalScrollView view = new HorizontalScrollView(owner.context);
            view.setFillViewport(true);
            scroller = view;
        } else {
            ScrollView view = new ScrollView(owner.context);
            view.setFillViewport(true);
            scroller = view;
        }

        LinearLayout content = new LinearLayout(owner.context);
        int orientation = horizontal ? LinearLayout.HORIZONTAL : LinearLayout.VERTICAL;
        content.setOrientation(orientation);
        int spacing = owner.dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                // Reuse the Row/Column sizing rules so a scrolled child gets
                // the same margins, flex shares and spacer sizes it would in
                // a plain Column.
                LinearLayout.LayoutParams params = owner.childParams(childNode, orientation, "");
                if (spacing > 0 && i > 0) {
                    if (horizontal) {
                        params.leftMargin += spacing;
                    } else {
                        params.topMargin += spacing;
                    }
                }
                content.addView(owner.buildChild(childNode), params);
            }
        }
        scroller.addView(content, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return scroller;
    }

    View buildStack(JSONObject node, JSONObject props) throws JSONException {
        android.widget.FrameLayout frame = new android.widget.FrameLayout(owner.context);
        int spacing = owner.dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                View child = owner.buildChild(children.getJSONObject(i));
                if (spacing > 0 && i > 0) {
                    // C-2: offset each layer so an overlay does not sit exactly
                    // on the content it annotates.
                    android.widget.FrameLayout.LayoutParams params =
                            new android.widget.FrameLayout.LayoutParams(
                                    ViewGroup.LayoutParams.MATCH_PARENT,
                                    ViewGroup.LayoutParams.WRAP_CONTENT);
                    params.topMargin = i * spacing;
                    frame.addView(child, params);
                } else {
                    frame.addView(child);
                }
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
    View buildGrid(JSONObject node, JSONObject props) throws JSONException {
        int columns = Math.max(1, props.optInt("columns", 2));
        int rowSpacing = owner.dp(props.optInt("row_spacing", 0));
        int columnSpacing = owner.dp(props.optInt("column_spacing", 0));

        LinearLayout grid = new LinearLayout(owner.context);
        grid.setOrientation(LinearLayout.VERTICAL);

        JSONArray children = node.optJSONArray("children");
        int count = children == null ? 0 : children.length();
        for (int start = 0; start < count; start += columns) {
            LinearLayout row = new LinearLayout(owner.context);
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
                    cell = owner.buildChild(childNode);
                    // A cell keeps its own margin; its width is owned by the
                    // grid, so only the margins are taken from the style.
                    owner.applyMargin(params, childNode.optJSONObject("style"));
                } else {
                    cell = new View(owner.context);
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
     * A flow layout: children side by side, a new line when the row is full.
     *
     * Each child keeps its natural size; its style still contributes margins and an
     * explicit width or height. {@code spacing} separates children on a line and
     * {@code run_spacing} the lines (it defaults to {@code spacing}).
     */
    View buildWrap(JSONObject node, JSONObject props) throws JSONException {
        int spacing = props.optInt("spacing", 0);
        int alignment = FlowLayout.ALIGN_START;
        String align = props.optString("align", "start");
        if ("center".equals(align)) {
            alignment = FlowLayout.ALIGN_CENTER;
        } else if ("end".equals(align)) {
            alignment = FlowLayout.ALIGN_END;
        }
        FlowLayout flow = new FlowLayout(
                owner.context, owner.dp(spacing), owner.dp(props.optInt("run_spacing", spacing)), alignment);
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                ViewGroup.MarginLayoutParams params = new ViewGroup.MarginLayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                owner.applyBoxStyle(params, childNode.optJSONObject("style"));
                flow.addView(owner.buildChild(childNode), params);
            }
        }
        return flow;
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
        final LinearLayout frame = new LinearLayout(owner.context);
        frame.setOrientation(LinearLayout.VERTICAL);

        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                frame.addView(owner.buildChild(children.getJSONObject(i)),
                        new LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.WRAP_CONTENT));
            }
        }

        final int minimum = owner.dp(props.optInt("minimum", 0));
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
    View buildFlex(JSONObject node) throws JSONException {
        LinearLayout holder = new LinearLayout(owner.context);
        holder.setOrientation(LinearLayout.VERTICAL);
        JSONArray children = node.optJSONArray("children");
        if (children != null && children.length() > 0) {
            holder.addView(owner.buildChild(children.getJSONObject(0)),
                    new LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.MATCH_PARENT,
                            ViewGroup.LayoutParams.MATCH_PARENT));
        }
        return holder;
    }

    View buildDivider(JSONObject props) {
        // Like Spacer, this leaf never sets its own layout params: the parent
        // container owns them and a mismatched type throws at runtime.
        View line = new View(owner.context);
        line.setBackgroundColor(owner.parseColor(props.optString("color", "#1F000000"),
                Color.parseColor("#1F000000")));
        int thickness = owner.dp(Math.max(1, props.optInt("thickness", 1)));
        if (props.optBoolean("vertical", false)) {
            line.setMinimumWidth(thickness);
        } else {
            line.setMinimumHeight(thickness);
        }
        return line;
    }
}
