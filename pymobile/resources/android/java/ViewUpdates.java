package org.pymobile.app;

import android.app.Dialog;
import android.graphics.Color;
import android.view.View;
import android.view.ViewGroup;
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
import android.widget.ScrollView;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import org.pymobile.app.FlowViews.FlowLayout;



import org.pymobile.app.ViewBuilder.DialogHost;

/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewUpdates {
    private final ViewBuilder owner;

    ViewUpdates(ViewBuilder owner) {
        this.owner = owner;
    }


    /** Fade and slightly scale a view when an opted-in widget changes visibility. */
    void animateVisibility(final View view, boolean visible, int duration) {
        view.animate().cancel();
        int millis = Math.max(0, Math.min(duration, 5000));
        if (millis == 0) {
            view.setAlpha(1f);
            view.setScaleY(1f);
            view.setVisibility(visible ? View.VISIBLE : View.GONE);
            return;
        }
        view.setPivotY(0f);
        if (visible) {
            view.setVisibility(View.VISIBLE);
            view.setAlpha(0f);
            view.setScaleY(0.96f);
            view.animate().alpha(1f).scaleY(1f).setDuration(millis).start();
        } else {
            view.animate().alpha(0f).scaleY(0.96f).setDuration(millis).withEndAction(new Runnable() {
                @Override
                public void run() {
                    view.setVisibility(View.GONE);
                    view.setAlpha(1f);
                    view.setScaleY(1f);
                }
            }).start();
        }
    }

    boolean updateNode(View view, JSONObject node) throws JSONException {
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
        boolean visible = node.optBoolean("visible", true);
        JSONObject animation = node.optJSONObject("animation");
        boolean animateVisibility = animation != null
                && "fade_scale".equals(animation.optString("visibility", ""));
        if (animateVisibility) {
            Boolean target = owner.animatedVisibilityTargets.get(view);
            if (target == null) {
                owner.animatedVisibilityTargets.put(view, visible);
                view.setVisibility(visible ? View.VISIBLE : View.GONE);
            } else if (target.booleanValue() != visible) {
                owner.animatedVisibilityTargets.put(view, visible);
                owner.animateVisibility(view, visible, animation.optInt("duration_ms", 220));
            }
        } else {
            owner.animatedVisibilityTargets.remove(view);
            view.setVisibility(visible ? View.VISIBLE : View.GONE);
        }

        // Re-apply style (background, padding, elevation, ...) so theme switches
        // and styling changes are reflected without a full rebuild.
        owner.applyStyle(view, node.optJSONObject("style"));

        // Composite views built from props (they serialise as leaves, or have
        // extra native children) must be handled before the generic ViewGroup
        // walk below: it compares native and tree child counts, and a mismatch
        // makes the caller rebuild the ENTIRE screen — closing the keyboard,
        // resetting scroll and losing widget state on every render.

        if (CustomWidgetRegistry.hasRenderer(type)) {
            return CustomWidgetRegistry.update(view, type, id, props);
        }
        if ("Icon".equals(type) || "IconButton".equals(type) || "Chart".equals(type)
                || "Canvas".equals(type)) {
            if (!(view instanceof AdvancedViews.VectorView)
                    || !type.equals(((AdvancedViews.VectorView) view).kind)) return false;
            ((AdvancedViews.VectorView) view).bind(id, props, owner.colorText);
            return true;
        }
        if ("AutoComplete".equals(type)) {
            if (!(view instanceof AdvancedViews.AutoInput)) return false;
            ((AdvancedViews.AutoInput) view).bind(id, props, owner.colorText, owner.colorTextMuted);
            return true;
        }
        if ("RatingBar".equals(type)) {
            if (!(view instanceof AdvancedViews.StarView)) return false;
            ((AdvancedViews.StarView) view).bind(id, props, owner.colorPrimary, owner.colorText);
            return true;
        }
        if ("RangeSlider".equals(type)) {
            if (!(view instanceof AdvancedViews.RangeView)) return false;
            ((AdvancedViews.RangeView) view).bind(id, props, owner.colorPrimary, owner.colorText);
            return true;
        }
        if ("PageView".equals(type)) {
            if (!(view instanceof AdvancedViews.PagerView)) return false;
            AdvancedViews.PagerView pager = (AdvancedViews.PagerView) view;
            JSONArray children = node.optJSONArray("children");
            if (children == null || children.length() != 1) return false;
            pager.bind(id, props);
            JSONObject page = children.getJSONObject(0);
            if (pager.getChildCount() != 1 || !owner.updateNode(pager.getChildAt(0), page)) {
                pager.removeAllViews();
                pager.addView(owner.buildChild(page), new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
            }
            return true;
        }

        if ("Dialog".equals(type)) {
            DialogHost host = owner.dialogs.get(view);
            if (host == null) {
                return false;
            }
            host.wanted = node.optBoolean("visible", true);
            view.setVisibility(View.GONE);
            owner.applyDialogProps(host, props);
            owner.applyStyle(host.box, node.optJSONObject("style"));
            JSONArray children = node.optJSONArray("children");
            int count = children == null ? 0 : children.length();
            // +1: the title TextView is always the first child of the box.
            if (host.box.getChildCount() != count + 1) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                if (!owner.updateNode(host.box.getChildAt(i + 1), children.getJSONObject(i))) {
                    return false;
                }
            }
            owner.syncDialog(view);
            return true;
        }

        if ("List".equals(type) && view instanceof LinearLayout) {
            // A new page appends rows; the generic walk below would see a
            // different child count and rebuild the screen (scroll back to top).
            return owner.updateList((LinearLayout) view, node, props);
        }

        if ("ListTile".equals(type) && view instanceof ViewGroup) {
            // Title, subtitle, trailing and long-press are all patched: row ids
            // are positional, so after a deletion a row shows another item.
            return owner.applyListTile((ViewGroup) view, id, props);
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
                owner.styleTab(tab, label.equals(value));
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
            owner.setScaledProgress((ProgressBar) box.getChildAt(0), props);
            owner.setTextAndVisibility((TextView) box.getChildAt(1), props.optString("text", ""), false);
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
            ViewGroup group = owner.segmentRow(view);
            JSONArray options = props.optJSONArray("options");
            int count = options == null ? 0 : options.length();
            // A different set of labels (a new language, an added option) or a switch
            // between scrolling and wrapping cannot be patched: rebuild the bar.
            if (group.getChildCount() != count
                    || props.optBoolean("wrap", false) != (view instanceof FlowLayout)) {
                return false;
            }
            for (int i = 0; i < count; i++) {
                View segment = group.getChildAt(i);
                if (!(segment instanceof TextView)
                        || !options.optString(i, "").contentEquals(((TextView) segment).getText())) {
                    return false;
                }
            }
            String selected = props.optString("value", "");
            for (int i = 0; i < group.getChildCount(); i++) {
                View child = group.getChildAt(i);
                if (child instanceof TextView) {
                    boolean isSelected = ((TextView) child).getText().toString()
                            .equals(selected);
                    if (isSelected) {
                        child.setBackgroundColor(owner.colorPrimary);
                        ((TextView) child).setTextColor(owner.colorOnPrimary);
                    } else {
                        child.setBackgroundColor(Color.TRANSPARENT);
                        ((TextView) child).setTextColor(owner.colorText);
                    }
                }
            }
            return true;
        }
        if ("DataTable".equals(type) && view instanceof ViewGroup) {
            // The Python side mutates rows/headers in place and the table
            // serialises as a leaf, so patch it live — otherwise add_row()
            // and cell edits never reach the screen.
            return owner.updateDataTable((ViewGroup) view, props);
        }

        if (view instanceof ViewGroup) {
            JSONArray children = node.optJSONArray("children");
            ViewGroup group = (ViewGroup) view;

            // A Grid nests its cells in one LinearLayout per row, so the flat
            // list of children in the tree has to be walked row by row.
            if ("Grid".equals(type)) {
                return owner.updateGrid(group, props, children);
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
                if (!owner.updateNode(target.getChildAt(i), children.getJSONObject(i))) {
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
                if (!owner.updateNode(group.getChildAt(i), radios.getJSONObject(i))) {
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
            owner.applySliderScale((SeekBar) view, props);
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
            view.setBackgroundColor(owner.parseColor(props.optString("color", "#1F000000"),
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
            Integer known = owner.inputRevisions.get(input);
            boolean programmatic = known == null || known != revision;
            owner.inputRevisions.put(input, revision);
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
                owner.setScaledProgress(bar, props);
            }
            return true;
        }
        if (view instanceof ImageView) {
            owner.applyImage((ImageView) view, props);
            return true;
        }
        if (view instanceof TextView) {
            ((TextView) view).setText(props.optString("text", ""));
            return true;
        }
        return true;
    }

    /** Patch a grid in place, mapping the flat child list onto its rows. */
    boolean updateGrid(ViewGroup grid, JSONObject props, JSONArray children)
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
                if (!owner.updateNode(cells.getChildAt(column), children.getJSONObject(index))) {
                    return false;
                }
            }
        }
        return true;
    }
}
