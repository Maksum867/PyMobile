package org.pymobile.app;

import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.os.Build;
import android.text.TextPaint;
import android.text.TextUtils;
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

    // -- window insets ---------------------------------------------------------
    // getSystemWindowInset*() is deprecated from API 30 and misses the display
    // cutout and the keyboard; these helpers read the typed insets there and
    // keep the old calls only below API 30 (minSdk is 24).
    int insetLeft(android.view.WindowInsets insets) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            return insets.getInsets(android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout()).left;
        }
        return insets.getSystemWindowInsetLeft();
    }

    int insetTop(android.view.WindowInsets insets) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            return insets.getInsets(android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout()).top;
        }
        return insets.getSystemWindowInsetTop();
    }

    int insetRight(android.view.WindowInsets insets) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            return insets.getInsets(android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout()).right;
        }
        return insets.getSystemWindowInsetRight();
    }

    int insetBottom(android.view.WindowInsets insets) {
        if (android.os.Build.VERSION.SDK_INT >= 30) {
            return insets.getInsets(android.view.WindowInsets.Type.systemBars()
                    | android.view.WindowInsets.Type.displayCutout()
                    | android.view.WindowInsets.Type.ime()).bottom;
        }
        return insets.getSystemWindowInsetBottom();
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
        // have already been written into these fields. Start/end resolve to
        // left/right or to right/left depending on the layout direction, so an
        // Arabic or Hebrew screen mirrors instead of keeping Western margins.
        params.setMarginStart(params.getMarginStart() + owner.dp(margin.optInt(0)));
        params.topMargin += owner.dp(margin.optInt(1));
        params.setMarginEnd(params.getMarginEnd() + owner.dp(margin.optInt(2)));
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
        // Opacity is a whole-widget property, not a text one: Android's
        // setAlpha() dims the background, the text and the children together,
        // which is what Style(opacity=…) promises.
        applyOpacity(view, style);
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
            applyTypography(text, style);
        }

        JSONArray padding = style.optJSONArray("padding");
        if (padding != null && padding.length() == 4) {
            view.setPadding(owner.dp(padding.optInt(0)), owner.dp(padding.optInt(1)),
                    owner.dp(padding.optInt(2)), owner.dp(padding.optInt(3)));
        }

        applySurface(view, style);

        // margin, width and height are applied in applyBoxStyle() instead:
        // this method runs before the view has a parent, so its layout params
        // do not exist yet and anything written here would be discarded.

        if (style.has("elevation") && android.os.Build.VERSION.SDK_INT >= 21) {
            float elevation = owner.dp(style.optInt("elevation"));
            view.setElevation(elevation);
            // A shadow needs something opaque to fall from; a view with no
            // background casts none, which looks like elevation being ignored.
            if (!style.has("background") && view.getBackground() == null) {
                view.setBackgroundColor(owner.colorSurface);
            }
        }

        owner.applyConstraints(view, style);
    }

    /** Style(opacity=…) → View.setAlpha(). */
    void applyOpacity(View view, JSONObject style) {
        if (!style.has("opacity")) {
            return;
        }
        float opacity = (float) style.optDouble("opacity", 1);
        view.setAlpha(Math.max(0f, Math.min(1f, opacity)));
    }

    /**
     * Background, corner radius, border and gradient on one drawable.
     *
     * All four live on the same ``GradientDrawable`` instance because Android
     * cannot stack them on a plain View: setting a background twice replaces
     * it, and a border painted as a separate view would not follow the layout.
     * The gradient is a ``GradientDrawable.setColors`` ramp for the two-colour
     * case and a custom drawable for more stops (or a direction other than the
     * four ``setOrientation`` offers).
     */
    void applySurface(View view, JSONObject style) {
        boolean hasGradient = style.has("gradient");
        JSONObject border = style.optJSONObject("border");
        boolean hasBorder = border != null && border.optInt("width", 0) > 0;
        if (!style.has("background") && !style.has("corner_radius")
                && !hasGradient && !hasBorder) {
            return;
        }
        int radius = owner.dp(style.optInt("corner_radius", 0));
        GradientDrawable shape = view.getBackground() instanceof GradientDrawable
                ? (GradientDrawable) view.getBackground().mutate() : new GradientDrawable();
        if (style.has("background")) {
            shape.setColor(owner.parseColor(style.optString("background"), Color.TRANSPARENT));
        } else if (!hasGradient
                && view.getBackground() instanceof android.graphics.drawable.ColorDrawable) {
            shape.setColor(((android.graphics.drawable.ColorDrawable) view.getBackground()).getColor());
        }
        shape.setCornerRadius(radius);
        if (hasBorder) {
            int width = owner.dp(border.optInt("width", 1));
            int colour = owner.parseColor(border.optString("color", "#E0E0E0"), Color.TRANSPARENT);
            shape.setStroke(width, colour);
            // Sides: a border on three sides has no GradientDrawable spelling,
            // so it is drawn by the wrapper below.
            JSONArray sides = border.optJSONArray("sides");
            if (sides != null && sides.length() > 0 && sides.length() < 4) {
                view.setBackground(new SidedBackground(shape, sides, width, colour, radius));
                return;
            }
        }
        if (hasGradient) {
            view.setBackground(gradientDrawable(style.optJSONObject("gradient"), shape));
            return;
        }
        view.setBackground(shape);
    }

    /** A ramp drawable for a serialised ``Gradient``. */
    android.graphics.drawable.Drawable gradientDrawable(
            JSONObject gradient, GradientDrawable shape) {
        JSONArray stops = gradient.optJSONArray("stops");
        if (stops == null || stops.length() < 2) {
            return shape;
        }
        int[] colours = new int[stops.length()];
        for (int i = 0; i < stops.length(); i++) {
            JSONArray stop = stops.optJSONArray(i);
            colours[i] = owner.parseColor(
                    stop == null ? "" : stop.optString(1, ""), Color.TRANSPARENT);
        }
        String direction = gradient.optString("direction", "to_bottom");
        if (colours.length == 2) {
            GradientDrawable.Orientation orientation = orientationFor(direction);
            if (orientation != null) {
                shape.setColors(colours);
                shape.setOrientation(orientation);
                // setColors() on a shape that also has a solid colour keeps the
                // solid one on some OEM builds; clearing it is the fix.
                shape.setColor(Color.TRANSPARENT);
                return shape;
            }
        }
        // Three stops, or a diagonal: GradientDrawable cannot express these,
        // so the corners of its four-corner enum would silently lie.
        return new RampDrawable(colours, stops, direction, shape);
    }

    GradientDrawable.Orientation orientationFor(String direction) {
        if ("to_bottom".equals(direction)) return GradientDrawable.Orientation.TOP_BOTTOM;
        if ("to_top".equals(direction)) return GradientDrawable.Orientation.BOTTOM_TOP;
        if ("to_right".equals(direction)) return GradientDrawable.Orientation.LEFT_RIGHT;
        if ("to_left".equals(direction)) return GradientDrawable.Orientation.RIGHT_LEFT;
        if ("to_top_right".equals(direction)) return GradientDrawable.Orientation.BL_TR;
        if ("to_bottom_left".equals(direction)) return GradientDrawable.Orientation.TR_BL;
        if ("to_bottom_right".equals(direction)) return GradientDrawable.Orientation.TL_BR;
        return null;
    }

    /** ``Style(font_family/line_height/letter_spacing/text_transform/…)``. */
    void applyTypography(TextView text, JSONObject style) {
        if (style.has("font_family")) {
            String family = style.optString("font_family", "");
            if (!family.isEmpty()) {
                // A family list (``Georgia, serif``) is a browser idea; Android
                // takes one name and falls back to its own default when the
                // device does not have it.
                String name = family.split(",")[0].trim();
                Typeface base = text.getTypeface();
                Typeface typed = Typeface.create(name, Typeface.NORMAL);
                if (base != null) {
                    typed = Typeface.create(typed, base.getStyle());
                }
                text.setTypeface(typed);
            }
        }
        if (style.has("line_height")) {
            // dp, not a multiplier: Style documents dp everywhere, and a
            // multiplier of 1.4 would mean "1.4 dp" by that rule.
            int extra = owner.dp((float) style.optDouble("line_height", 0));
            text.setLineSpacing(extra, 1f);
        }
        if (style.has("letter_spacing")) {
            float spacing = (float) style.optDouble("letter_spacing", 0);
            // setLetterSpacing() is in ems, Style documents dp: convert with
            // the sp size, so 0.5 dp of tracking reads the same at every scale.
            float pixels = text.getTextSize();
            text.setLetterSpacing(pixels > 0 ? owner.dp(spacing) / pixels : 0f);
        }
        String transform = style.optString("text_transform", "");
        if ("uppercase".equals(transform)) {
            // A transformation method, not a copy of the string: the app's own
            // `text` prop stays what it wrote, and a later change re-applies.
            text.setTransformationMethod(new UpperCaseMethod());
        } else if ("capitalize".equals(transform)) {
            text.setTransformationMethod(new WordCaseMethod());
        } else if ("lowercase".equals(transform)) {
            text.setTransformationMethod(new LowerCaseMethod());
        }
        if (style.has("max_lines")) {
            text.setMaxLines(style.optInt("max_lines", 1));
            text.setEllipsize("clip".equals(style.optString("text_overflow", ""))
                    ? TextUtils.TruncateAt.END : TextUtils.TruncateAt.END);
        }
        if ("clip".equals(style.optString("text_overflow", ""))) {
            text.setEllipsize(null);
            text.setSingleLine(true);
        }
        if (style.optBoolean("underline", false) || style.optBoolean("strikethrough", false)) {
            int flags = text.getPaintFlags();
            if (style.optBoolean("underline", false)) flags |= Paint.UNDERLINE_TEXT_FLAG;
            if (style.optBoolean("strikethrough", false)) flags |= Paint.STRIKE_THRU_TEXT_FLAG;
            text.setPaintFlags(flags);
        }
    }

    /** ``text_transform="uppercase"``, applied at draw time. */
    static final class UpperCaseMethod extends android.text.method.ReplacementTransformationMethod {
        @Override protected char[] getOriginal() {
            char[] lower = "abcdefghijklmnopqrstuvwxyz".toCharArray();
            char[] both = new char[lower.length + "àéèüäöñç".length()];
            System.arraycopy(lower, 0, both, 0, lower.length);
            char[] extra = "àéèüäöñç".toCharArray();
            System.arraycopy(extra, 0, both, lower.length, extra.length);
            return both;
        }

        @Override protected char[] getReplacement() {
            String upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZÀÉÈÜÄÖÑÇ";
            return upper.toCharArray();
        }

        @Override public CharSequence getTransformation(CharSequence source, android.view.View view) {
            CharSequence result = super.getTransformation(source, view);
            return result == null ? null
                    : result.toString().toUpperCase(java.util.Locale.getDefault());
        }
    }

    /** ``text_transform="lowercase"``, applied at draw time. */
    static final class LowerCaseMethod extends android.text.method.ReplacementTransformationMethod {
        @Override protected char[] getOriginal() {
            return "ABCDEFGHIJKLMNOPQRSTUVWXYZ".toCharArray();
        }

        @Override protected char[] getReplacement() {
            return "abcdefghijklmnopqrstuvwxyz".toCharArray();
        }

        @Override public CharSequence getTransformation(CharSequence source, android.view.View view) {
            CharSequence result = super.getTransformation(source, view);
            return result == null ? null
                    : result.toString().toLowerCase(java.util.Locale.getDefault());
        }
    }

    /** ``text_transform="capitalize"``: the first letter of each word. */
    static final class WordCaseMethod extends android.text.method.ReplacementTransformationMethod {
        @Override protected char[] getOriginal() {
            return "abcdefghijklmnopqrstuvwxyz".toCharArray();
        }

        @Override protected char[] getReplacement() {
            return "ABCDEFGHIJKLMNOPQRSTUVWXYZ".toCharArray();
        }

        @Override public CharSequence getTransformation(CharSequence source, android.view.View view) {
            if (source == null) {
                return null;
            }
            StringBuilder word = new StringBuilder(source.length());
            boolean startOfWord = true;
            for (int i = 0; i < source.length(); i++) {
                char c = source.charAt(i);
                word.append(startOfWord ? Character.toUpperCase(c) : c);
                startOfWord = Character.isWhitespace(c) || c == '-';
            }
            return word.toString();
        }
    }

    /** Draws a stroke on the named sides only, over a background drawable. */
    static final class SidedBackground extends android.graphics.drawable.Drawable {
        private final android.graphics.drawable.Drawable base;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final boolean top, right, bottom, left;
        private final int width;
        private final float radius;

        SidedBackground(android.graphics.drawable.Drawable base, JSONArray sides,
                int width, int colour, float radius) {
            this.base = base;
            this.width = width;
            this.radius = radius;
            this.paint.setColor(colour);
            this.paint.setStyle(Paint.Style.FILL);
            boolean t = false, r = false, b = false, l = false;
            for (int i = 0; i < sides.length(); i++) {
                String side = sides.optString(i, "");
                if ("top".equals(side)) t = true;
                else if ("right".equals(side)) r = true;
                else if ("bottom".equals(side)) b = true;
                else if ("left".equals(side)) l = true;
            }
            this.top = t; this.right = r; this.bottom = b; this.left = l;
        }

        @Override public void draw(Canvas canvas) {
            base.setBounds(getBounds());
            base.draw(canvas);
            Rect bounds = getBounds();
            if (top) canvas.drawRect(bounds.left, bounds.top,
                    bounds.right, bounds.top + width, paint);
            if (bottom) canvas.drawRect(bounds.left, bounds.bottom - width,
                    bounds.right, bounds.bottom, paint);
            if (left) canvas.drawRect(bounds.left, bounds.top,
                    bounds.left + width, bounds.bottom, paint);
            if (right) canvas.drawRect(bounds.right - width, bounds.top,
                    bounds.right, bounds.bottom, paint);
        }

        @Override public void setAlpha(int alpha) { base.setAlpha(alpha); paint.setAlpha(alpha); }

        @Override public void setColorFilter(android.graphics.ColorFilter filter) {
            base.setColorFilter(filter);
        }

        @Override public int getOpacity() { return android.graphics.PixelFormat.TRANSLUCENT; }

        @Override public void setBounds(int left, int top, int right, int bottom) {
            super.setBounds(left, top, right, bottom);
            base.setBounds(left, top, right, bottom);
        }
    }

    /** Multi-stop or diagonal ramp drawn as vertical strips behind the shape. */
    static final class RampDrawable extends android.graphics.drawable.Drawable {
        private final int[] colours;
        private final float[] offsets;
        private final String direction;
        private final android.graphics.drawable.Drawable base;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);

        RampDrawable(int[] colours, JSONArray stops, String direction,
                android.graphics.drawable.Drawable base) {
            this.colours = colours;
            this.offsets = new float[colours.length];
            for (int i = 0; i < colours.length; i++) {
                JSONArray stop = stops.optJSONArray(i);
                this.offsets[i] = stop == null ? 0f : (float) stop.optDouble(0, 0);
            }
            this.direction = direction;
            this.base = base;
        }

        @Override public void draw(Canvas canvas) {
            Rect bounds = getBounds();
            boolean horizontal = "to_right".equals(direction) || "to_left".equals(direction);
            int steps = Math.max(2, horizontal ? bounds.width() : bounds.height());
            for (int i = 0; i < steps; i++) {
                float t = (float) i / (steps - 1);
                if ("to_left".equals(direction) || "to_top".equals(direction)) {
                    t = 1f - t;
                }
                paint.setColor(colourAt(t));
                if (horizontal) {
                    int x = bounds.left + (int) (t * bounds.width());
                    canvas.drawRect(x, bounds.top, x + 2, bounds.bottom, paint);
                } else {
                    int y = bounds.top + (int) (t * bounds.height());
                    canvas.drawRect(bounds.left, y, bounds.right, y + 2, paint);
                }
            }
            if (base != null) {
                // The corners and, if any, the stroke from the shape itself.
                base.setAlpha(0);
                base.setBounds(bounds);
            }
        }

        int colourAt(float t) {
            for (int i = 1; i < offsets.length; i++) {
                if (t <= offsets[i]) {
                    float span = offsets[i] - offsets[i - 1];
                    float amount = span <= 0 ? 0 : (t - offsets[i - 1]) / span;
                    return blend(colours[i - 1], colours[i], amount);
                }
            }
            return colours[colours.length - 1];
        }

        static int blend(int from, int to, float amount) {
            int a = (int) (Color.alpha(from) + (Color.alpha(to) - Color.alpha(from)) * amount);
            int r = (int) (Color.red(from) + (Color.red(to) - Color.red(from)) * amount);
            int g = (int) (Color.green(from) + (Color.green(to) - Color.green(from)) * amount);
            int b = (int) (Color.blue(from) + (Color.blue(to) - Color.blue(from)) * amount);
            return Color.argb(a, r, g, b);
        }

        @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); }
        @Override public void setColorFilter(android.graphics.ColorFilter filter) {
            paint.setColorFilter(filter);
        }
        @Override public int getOpacity() { return android.graphics.PixelFormat.TRANSLUCENT; }
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
