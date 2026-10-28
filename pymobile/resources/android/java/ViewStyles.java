package org.pymobile.app;

import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;





/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewStyles {
    private final ViewBuilder owner;

    ViewStyles(ViewBuilder owner) {
        this.owner = owner;
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
            owner.applyCrossGravity(flexParams, orientation, crossAlign);
            return flexParams;
        }

        if ("Spacer".equals(type)) {
            int size = owner.dp(childProps.optInt("size", 8));
            if (vertical) {
                height = size;
            } else {
                width = size;
            }
        }

        if ("Divider".equals(type)) {
            // A divider spans the container across the axis and is exactly as
            // thick as it says on the main axis.
            int thickness = owner.dp(Math.max(1, childProps.optInt("thickness", 1)));
            if (childProps.optBoolean("vertical", false)) {
                width = thickness;
                height = vertical ? owner.dp(24) : ViewGroup.LayoutParams.MATCH_PARENT;
            } else {
                width = vertical ? ViewGroup.LayoutParams.MATCH_PARENT : owner.dp(24);
                height = thickness;
            }
            int inset = owner.dp(childProps.optInt("inset", 0));
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

        // Stars have a natural width: stretching the view across a Column
        // only leaves a dead touch area to the right of the last star.
        if ("RatingBar".equals(type) && vertical) {
            width = ViewGroup.LayoutParams.WRAP_CONTENT;
        }

        // An explicit weight in the style keeps working exactly as before.
        JSONObject style = childNode.optJSONObject("style");
        float weight = style == null ? 0f : (float) style.optDouble("weight", 0);
        LinearLayout.LayoutParams params = weight > 0
                ? new LinearLayout.LayoutParams(
                        vertical ? width : 0, vertical ? 0 : height, weight)
                : new LinearLayout.LayoutParams(width, height);
        owner.applyCrossGravity(params, orientation, crossAlign);
        owner.applyBoxStyle(params, style);
        return params;
    }

    /**
     * Apply the parts of a Style that live on the layout params, not the view.
     *
     * margin, width and height cannot be set in applyStyle(): that runs while
     * the view is still detached, so getLayoutParams() is null and the values
     * were silently dropped. They belong here, where the params are created.
     */
    void applyBoxStyle(ViewGroup.MarginLayoutParams params, JSONObject style) {
        if (style == null) {
            return;
        }
        owner.applyMargin(params, style);

        int width = owner.dimension(style, "width");
        if (width != Integer.MIN_VALUE) {
            params.width = width;
        }
        int height = owner.dimension(style, "height");
        if (height != Integer.MIN_VALUE) {
            params.height = height;
        }
    }

    /** Add a Style's margin to layout params that may already carry spacing. */
    void applyMargin(ViewGroup.MarginLayoutParams params, JSONObject style) {
        if (style == null) {
            return;
        }
        JSONArray margin = style.optJSONArray("margin");
        if (margin == null || margin.length() != 4) {
            return;
        }
        // Add rather than assign: a container's spacing and a Divider's inset
        // have already been written into these fields.
        params.leftMargin += owner.dp(margin.optInt(0));
        params.topMargin += owner.dp(margin.optInt(1));
        params.rightMargin += owner.dp(margin.optInt(2));
        params.bottomMargin += owner.dp(margin.optInt(3));
    }

    /**
     * Read a Style dimension.
     *
     * Accepts a number in dp or one of the names the framework documents:
     * "match"/"fill" for MATCH_PARENT and "wrap" for WRAP_CONTENT. Returns
     * Integer.MIN_VALUE when the key is absent, which no real size can be.
     */
    int dimension(JSONObject style, String key) {
        if (!style.has(key)) {
            return Integer.MIN_VALUE;
        }
        Object raw = style.opt(key);
        if (raw instanceof Number) {
            return owner.dp(((Number) raw).intValue());
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
    void applyCrossGravity(
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
    int gravityFor(int orientation, String align, String crossAlign) {
        boolean vertical = orientation == LinearLayout.VERTICAL;
        int main = owner.mainGravity(align, vertical);
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
    int mainGravity(String align, boolean vertical) {
        if ("center".equals(align)) {
            return vertical ? Gravity.CENTER_VERTICAL : Gravity.CENTER_HORIZONTAL;
        }
        if ("end".equals(align)) {
            return vertical ? Gravity.BOTTOM : Gravity.END;
        }
        return vertical ? Gravity.TOP : Gravity.START;
    }

    int horizontalGravity(String align) {
        if ("center".equals(align)) {
            return Gravity.CENTER_HORIZONTAL;
        }
        if ("end".equals(align)) {
            return Gravity.END;
        }
        return Gravity.START;
    }

    // -- styling ----------------------------------------------------------

    void applyStyle(View view, JSONObject style) {
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
                text.setTextColor(owner.parseColor(style.optString("color"), owner.colorText));
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
            view.setPadding(owner.dp(padding.optInt(0)), owner.dp(padding.optInt(1)),
                    owner.dp(padding.optInt(2)), owner.dp(padding.optInt(3)));
        }

        if (style.has("background") || style.has("corner_radius")) {
            GradientDrawable shape = new GradientDrawable();
            shape.setColor(owner.parseColor(style.optString("background", "#00000000"),
                    Color.TRANSPARENT));
            shape.setCornerRadius(owner.dp(style.optInt("corner_radius", 0)));
            view.setBackground(shape);
        }

        // margin, width and height are applied in applyBoxStyle() instead:
        // this method runs before the view has a parent, so its layout params
        // do not exist yet and anything written here would be discarded.

        if (style.has("elevation") && android.os.Build.VERSION.SDK_INT >= 21) {
            float elevation = owner.dp(style.optInt("elevation"));
            view.setElevation(elevation);
            // A shadow needs something opaque to fall from; a view with no
            // background casts none, which looks like elevation being ignored.
            if (!style.has("background")) {
                view.setBackgroundColor(owner.colorSurface);
            }
        }

        owner.applyConstraints(view, style);
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
        boolean hasMin = style.has("min_width") || style.has("min_height");
        boolean hasMax = style.has("max_width") || style.has("max_height");
        boolean hasRatio = style.has("aspect_ratio");
        if (!hasMin && !hasMax && !hasRatio) {
            return;
        }

        if (style.has("min_width")) {
            view.setMinimumWidth(owner.dp(style.optInt("min_width")));
        }
        if (style.has("min_height")) {
            view.setMinimumHeight(owner.dp(style.optInt("min_height")));
        }
        if (!hasMax && !hasRatio) {
            return;
        }

        final int maxWidth = style.has("max_width") ? owner.dp(style.optInt("max_width")) : 0;
        final int maxHeight = style.has("max_height") ? owner.dp(style.optInt("max_height")) : 0;
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
    int parseColor(String value, int fallback) {
        if (value == null || value.isEmpty()) {
            return fallback;
        }
        try {
            return Color.parseColor(value);
        } catch (IllegalArgumentException error) {
            return fallback;
        }
    }
}
