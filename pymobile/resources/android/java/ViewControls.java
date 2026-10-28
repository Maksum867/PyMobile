package org.pymobile.app;

import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.HorizontalScrollView;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import org.pymobile.app.FlowViews.FlowLayout;




/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewControls {
    private final ViewBuilder owner;

    ViewControls(ViewBuilder owner) {
        this.owner = owner;
    }


    /**
     * A declarative drawing. It shares {@link AdvancedViews.VectorView} with
     * Chart and Icon: the shape list is identical, only the intent differs, so
     * the Android side gained a branch rather than a renderer.
     */
    View buildCanvas(String id, JSONObject props) {
        AdvancedViews.VectorView view = new AdvancedViews.VectorView(owner.context, "Canvas");
        view.bind(id, props, owner.colorText);
        return view;
    }

    View buildVector(String kind, String id, JSONObject props) {
        AdvancedViews.VectorView view = new AdvancedViews.VectorView(owner.context, kind);
        view.bind(id, props, owner.colorText);
        return view;
    }

    // -- leaves -----------------------------------------------------------

    View buildBottomNavigation(final String id, JSONObject props) throws JSONException {
        LinearLayout bar = new LinearLayout(owner.context);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        JSONArray options = props.optJSONArray("options");
        final String value = props.optString("value", "");
        if (options != null) {
            for (int i = 0; i < options.length(); i++) {
                final String label = options.optString(i, "");
                Button tab = new Button(owner.context);
                tab.setText(label);
                tab.setAllCaps(false);
                tab.setLayoutParams(new LinearLayout.LayoutParams(
                        0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
                owner.styleTab(tab, label.equals(value));
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

    void styleTab(Button tab, boolean selected) {
        tab.setBackgroundColor(selected ? owner.colorPrimary : owner.colorSurface);
        tab.setTextColor(selected ? owner.colorOnPrimary : owner.colorText);
    }

    View buildLabel(JSONObject props) {
        TextView label = new TextView(owner.context);
        label.setText(props.optString("text", ""));
        label.setTextColor(owner.colorText);
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
    View buildUnknown(String type, JSONObject props) {
        android.util.Log.w("pymobile", "no native renderer for widget type \"" + type + "\"");
        String text = props.optString("text", "");
        TextView placeholder = new TextView(owner.context);
        // The widget's own text still shows, so a screen built from a custom
        // type before its Java branch exists is not blank on the device.
        placeholder.setText(text.isEmpty()
                ? "[" + type + ": no native renderer]"
                : "[" + type + ": no native renderer] " + text);
        placeholder.setTextColor(Color.parseColor("#C62828"));
        placeholder.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        placeholder.setPadding(owner.dp(4), owner.dp(4), owner.dp(4), owner.dp(4));
        return placeholder;
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
        String variant = props.optString("variant", "primary");
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(owner.dp(variant.equals("text") ? 0 : 10));
        int fill = owner.colorPrimary;
        int textColour = Color.WHITE;
        if ("tonal".equals(variant)) {
            fill = owner.blend(owner.colorPrimary, owner.colorSurface, 0.82f);
            textColour = owner.readableOn(fill);
        } else if ("outlined".equals(variant)) {
            shape.setColor(Color.TRANSPARENT);
            shape.setStroke(Math.max(1, owner.dp(1)), owner.colorPrimary);
            textColour = owner.colorPrimary;
        } else if ("text".equals(variant)) {
            shape.setColor(Color.TRANSPARENT);
            textColour = owner.colorPrimary;
        } else if ("danger".equals(variant)) {
            fill = owner.colorError;
            textColour = owner.readableOn(fill);
        }
        if (!"outlined".equals(variant) && !"text".equals(variant)) {
            shape.setColor(fill);
        }
        Button button = new Button(owner.context);
        button.setBackground(shape);
        button.setTextColor(textColour);
        button.setText(props.optString("text", ""));
        button.setAllCaps(false);
        button.setElevation(owner.dp("primary".equals(variant) || "danger".equals(variant) ? 2 : 0));
        button.setPadding(owner.dp(20), owner.dp(10), owner.dp(20), owner.dp(10));
        button.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return button;
    }

    View buildChip(final String id, JSONObject props) {
        Button chip = new Button(owner.context);
        chip.setText(props.optString("text", ""));
        chip.setAllCaps(false);
        if (props.optBoolean("selected", false)) {
            chip.setTextColor(Color.parseColor("#FFFFFF"));
            chip.setBackgroundColor(owner.parseColor(props.optString("selectedColor", ""),
                    owner.colorPrimary));
        }
        chip.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return chip;
    }

    View buildBadge(JSONObject props) {
        TextView badge = new TextView(owner.context);
        badge.setText(props.optString("text", ""));
        badge.setTextColor(owner.parseColor(props.optString("color", "#FFFFFF"),
                Color.parseColor("#FFFFFF")));
        badge.setBackgroundColor(owner.parseColor(props.optString("background", ""), owner.colorPrimary));
        badge.setGravity(Gravity.CENTER);
        int pad = owner.dp(6);
        badge.setPadding(pad, owner.dp(2), pad, owner.dp(2));
        return badge;
    }

    View buildStepper(final String id, JSONObject props) {
        LinearLayout row = new LinearLayout(owner.context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER);

        Button minus = new Button(owner.context);
        minus.setText("−");
        minus.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "decrement", "");
            }
        });
        TextView value = new TextView(owner.context);
        value.setText(String.valueOf(props.optInt("value", 0)));
        value.setGravity(Gravity.CENTER);
        value.setPadding(owner.dp(12), 0, owner.dp(12), 0);
        value.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        value.setTextColor(owner.colorText);
        Button plus = new Button(owner.context);
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
        JSONArray options = props.optJSONArray("options");
        String selected = props.optString("value", "");
        boolean wrap = props.optBoolean("wrap", false);

        final ViewGroup row;
        if (wrap) {
            row = new FlowLayout(owner.context, 0, 0, FlowLayout.ALIGN_START);
        } else {
            LinearLayout line = new LinearLayout(owner.context);
            line.setOrientation(LinearLayout.HORIZONTAL);
            row = line;
        }
        View selectedSegment = null;
        if (options != null) {
            for (int i = 0; i < options.length(); i++) {
                final String label = options.optString(i, "");
                Button segment = new Button(owner.context);
                segment.setText(label);
                segment.setAllCaps(false);
                if (label.equals(selected)) {
                    segment.setTextColor(owner.colorOnPrimary);
                    segment.setBackgroundColor(owner.colorPrimary);
                    selectedSegment = segment;
                } else {
                    segment.setTextColor(owner.colorText);
                }
                segment.setOnClickListener(new View.OnClickListener() {
                    @Override
                    public void onClick(View v) {
                        Native.dispatchEvent(id, "change", label);
                    }
                });
                if (wrap) {
                    row.addView(segment, new ViewGroup.MarginLayoutParams(
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT));
                } else {
                    row.addView(segment);
                }
            }
        }
        if (wrap) {
            return row;
        }

        final HorizontalScrollView scroller = new HorizontalScrollView(owner.context);
        scroller.setHorizontalScrollBarEnabled(false);
        scroller.addView(row, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        if (selectedSegment != null) {
            final View target = selectedSegment;
            // After the first layout, bring the selected segment to the middle of the bar.
            scroller.post(new Runnable() {
                @Override
                public void run() {
                    int x = target.getLeft() - (scroller.getWidth() - target.getWidth()) / 2;
                    scroller.scrollTo(Math.max(0, x), 0);
                }
            });
        }
        return scroller;
    }

    View buildLink(final String id, JSONObject props) {
        TextView link = new TextView(owner.context);
        link.setText(props.optString("text", ""));
        link.setTextColor(owner.colorPrimary);
        link.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        final String url = props.optString("url", "");
        link.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                // Open the URL in the system browser directly from Java (no JNI
                // round-trip), so links work even with an older libpymobile.so.
                if (url != null && !url.isEmpty()) {
                    DeviceServices.openUrl(owner.context, url);
                } else {
                    Native.dispatchEvent(id, "press", "");
                }
            }
        });
        return link;
    }

    /** A horizontal bar with its formatted label ("Downloading 42%") below. */
    View buildProgressText(final String id, JSONObject props) {
        LinearLayout box = new LinearLayout(owner.context);
        box.setOrientation(LinearLayout.VERTICAL);
        ProgressBar bar = new ProgressBar(owner.context, null,
                android.R.attr.progressBarStyleHorizontal);
        owner.setScaledProgress(bar, props);
        box.addView(bar, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        TextView label = new TextView(owner.context);
        label.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        label.setTextColor(owner.colorTextMuted);
        owner.setTextAndVisibility(label, props.optString("text", ""), false);
        box.addView(label);
        return box;
    }

    View buildDataTable(JSONObject node, JSONObject props) throws JSONException {
        LinearLayout table = new LinearLayout(owner.context);
        table.setOrientation(LinearLayout.VERTICAL);
        JSONArray headers = props.optJSONArray("headers");
        JSONArray rows = props.optJSONArray("rows");
        if (headers != null) {
            LinearLayout head = new LinearLayout(owner.context);
            head.setOrientation(LinearLayout.HORIZONTAL);
            for (int i = 0; i < headers.length(); i++) {
                TextView cell = new TextView(owner.context);
                cell.setText(headers.optString(i, ""));
                cell.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
                cell.setTextColor(owner.colorText);
                cell.setPadding(owner.dp(8), owner.dp(4), owner.dp(8), owner.dp(4));
                head.addView(cell);
            }
            table.addView(head);
        }
        if (rows != null) {
            for (int r = 0; r < rows.length(); r++) {
                LinearLayout row = new LinearLayout(owner.context);
                row.setOrientation(LinearLayout.HORIZONTAL);
                JSONArray cells = rows.optJSONArray(r);
                for (int c = 0; c < (cells == null ? 0 : cells.length()); c++) {
                    TextView cell = new TextView(owner.context);
                    cell.setText(cells.optString(c, ""));
                    cell.setTextColor(owner.colorText);
                    cell.setPadding(owner.dp(8), owner.dp(4), owner.dp(8), owner.dp(4));
                    row.addView(cell);
                }
                table.addView(row);
            }
        }
        return table;
    }

    /** Patch a DataTable in place so row/header edits show without a rebuild. */
    boolean updateDataTable(ViewGroup table, JSONObject props) {
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

    View buildAvatar(JSONObject props) {
        TextView avatar = new TextView(owner.context);
        String text = props.optString("text", "");
        if (text.length() > 2) {
            text = text.substring(0, 2);
        }
        avatar.setText(text.toUpperCase());
        avatar.setTextColor(owner.parseColor(props.optString("color", "#FFFFFF"),
                Color.parseColor("#FFFFFF")));
        avatar.setGravity(Gravity.CENTER);
        int size = owner.dp(props.optInt("size", 48));
        avatar.setBackgroundColor(owner.parseColor(props.optString("background", ""), owner.colorPrimary));
        avatar.setMinimumWidth(size);
        avatar.setMinimumHeight(size);
        return avatar;
    }

    View buildProgress(JSONObject props) {
        boolean indeterminate = props.optBoolean("indeterminate", false);
        ProgressBar bar = new ProgressBar(
                owner.context, null,
                indeterminate ? android.R.attr.progressBarStyle
                              : android.R.attr.progressBarStyleHorizontal);
        bar.setIndeterminate(indeterminate);
        if (!indeterminate) {
            owner.setScaledProgress(bar, props);
        }
        return bar;
    }

    View buildImage(JSONObject props) {
        ImageView image = new ImageView(owner.context);
        owner.applyImage(image, props);
        return image;
    }

    /** Load the image source and fit; used for the first build and for patches. */
    void applyImage(ImageView image, JSONObject props) {
        image.setScaleType("cover".equals(props.optString("fit", "contain"))
                ? ImageView.ScaleType.CENTER_CROP
                : ImageView.ScaleType.FIT_CENTER);
        String source = props.optString("source", "");
        if (source.equals(owner.imageSources.get(image))) {
            return;
        }
        owner.imageSources.put(image, source);
        image.setImageDrawable(null);
        try {
            java.io.File file = new java.io.File(source);
            if (!file.isAbsolute()) {
                file = new java.io.File(owner.context.getFilesDir(), "pymobile/app/" + source);
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

    View buildSpacer(JSONObject props) {
        // No setLayoutParams here: the parent container assigns params of its
        // own type. A bare ViewGroup.LayoutParams would make LinearLayout throw
        // ClassCastException and silently drop the rest of the tree.
        View spacer = new View(owner.context);
        spacer.setMinimumWidth(owner.dp(props.optInt("size", 8)));
        spacer.setMinimumHeight(owner.dp(props.optInt("size", 8)));
        return spacer;
    }
}
