package org.pymobile.app;

import android.app.DatePickerDialog;
import android.app.TimePickerDialog;
import android.text.Editable;
import android.text.InputType;
import android.text.TextWatcher;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.SeekBar;
import android.widget.Spinner;
import android.widget.Switch;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;





/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewInputs {
    private final ViewBuilder owner;

    ViewInputs(ViewBuilder owner) {
        this.owner = owner;
    }


    android.content.res.ColorStateList primaryTint() {
        return new android.content.res.ColorStateList(
                new int[][]{new int[]{-android.R.attr.state_enabled}, new int[]{}},
                new int[]{owner.colorTextMuted, owner.colorPrimary});
    }

    android.content.res.ColorStateList controlTint() {
        return new android.content.res.ColorStateList(
                new int[][]{new int[]{-android.R.attr.state_enabled},
                    new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{owner.colorTextMuted, owner.colorPrimary, owner.colorTextMuted});
    }

    void tintProgress(android.widget.ProgressBar bar) {
        bar.setProgressTintList(primaryTint());
        bar.setIndeterminateTintList(primaryTint());
        bar.setProgressBackgroundTintList(android.content.res.ColorStateList.valueOf(owner.colorSurface));
    }

    android.content.Context pickerContext() {
        return new android.view.ContextThemeWrapper(owner.context, owner.darkTheme
                ? android.R.style.Theme_Material_Dialog_Alert
                : android.R.style.Theme_Material_Light_Dialog_Alert);
    }

    View buildAutoComplete(String id, JSONObject props) {
        AdvancedViews.AutoInput view = new AdvancedViews.AutoInput(owner.context);
        view.bind(id, props, owner.colorText, owner.colorTextMuted, owner.colorSurface);
        return view;
    }

    View buildRange(String id, JSONObject props) {
        AdvancedViews.RangeView view = new AdvancedViews.RangeView(owner.context);
        view.bind(id, props, owner.colorPrimary, owner.colorText);
        return view;
    }

    View buildDatePicker(final String id, JSONObject props) {
        final String current = props.optString("value", "");
        final Button button = new Button(owner.context);
        owner.controls.applyButton(button, new JSONObject());
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
                DatePickerDialog dialog = new DatePickerDialog(pickerContext(),
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

    View buildTimePicker(final String id, JSONObject props) {
        final String current = props.optString("value", "");
        final Button button = new Button(owner.context);
        owner.controls.applyButton(button, new JSONObject());
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
                TimePickerDialog dialog = new TimePickerDialog(pickerContext(),
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

    View buildTextInput(final String id, JSONObject props) {
        EditText input = new EditText(owner.context);
        input.setText(props.optString("value", ""));
        input.setHint(props.optString("placeholder", ""));
        input.setTextColor(owner.colorText);
        input.setHintTextColor(owner.colorTextMuted);
        owner.inputRevisions.put(input, props.optInt("revision", 0));
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

    View buildSwitch(final String id, JSONObject props) {
        Switch toggle = new Switch(owner.context);
        toggle.setChecked(props.optBoolean("checked", false));
        toggle.setTextColor(owner.colorText);
        toggle.setThumbTintList(controlTint());
        toggle.setTrackTintList(controlTint());
        toggle.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton view, boolean checked) {
                Native.dispatchEvent(id, "toggle", checked ? "true" : "false");
            }
        });
        return toggle;
    }

    View buildCheckbox(final String id, JSONObject props) {
        CheckBox checkbox = new CheckBox(owner.context);
        checkbox.setChecked(props.optBoolean("checked", false));
        checkbox.setTextColor(owner.colorText);
        checkbox.setButtonTintList(controlTint());
        checkbox.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            @Override
            public void onCheckedChanged(CompoundButton view, boolean checked) {
                Native.dispatchEvent(id, "toggle", checked ? "true" : "false");
            }
        });
        return checkbox;
    }

    View buildSlider(final String id, JSONObject props) {
        SeekBar seek = new SeekBar(owner.context);
        tintProgress(seek);
        seek.setThumbTintList(primaryTint());
        owner.applySliderScale(seek, props);
        seek.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override
            public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                if (fromUser) {
                    double[] scale = owner.sliderScales.get(bar);
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
    void applySliderScale(SeekBar seek, JSONObject props) {
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
        owner.sliderScales.put(seek, new double[]{minimum, maximum, step});
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

    View buildRatingBar(final String id, JSONObject props) {
        // A hand-drawn view, not android.widget.RatingBar: see StarView.
        AdvancedViews.StarView view = new AdvancedViews.StarView(owner.context);
        view.bind(id, props, owner.colorPrimary, owner.colorText);
        return view;
    }

    static final class OptionAdapter extends android.widget.ArrayAdapter<String> {
        final String[] values;
        final int textColor, surfaceColor;

        OptionAdapter(android.content.Context context, JSONObject props, int text, int surface) {
            super(context, android.R.layout.simple_spinner_item);
            textColor = text; surfaceColor = surface;
            JSONArray options = props.optJSONArray("options");
            values = new String[options == null ? 0 : options.length()];
            for (int i = 0; i < values.length; i++) {
                values[i] = options.optString(i, "");
                add(ViewControls.optionLabel(props, i));
            }
            setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        }
        private View colour(View view) {
            ((android.widget.TextView) view).setTextColor(textColor);
            view.setBackgroundColor(surfaceColor);
            return view;
        }
        @Override public View getView(int position, View convertView, android.view.ViewGroup parent) {
            return colour(super.getView(position, convertView, parent));
        }
        @Override public View getDropDownView(int position, View convertView, android.view.ViewGroup parent) {
            return colour(super.getDropDownView(position, convertView, parent));
        }
    }

    View buildDropdown(final String id, JSONObject props) {
        Spinner spinner = new Spinner(owner.context);
        final OptionAdapter adapter = new OptionAdapter(owner.context, props, owner.colorText, owner.colorSurface);
        String[] entries = adapter.values;
        spinner.setAdapter(adapter);
        spinner.setPopupBackgroundDrawable(new android.graphics.drawable.ColorDrawable(owner.colorSurface));
        spinner.setBackgroundTintList(primaryTint());
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

    View buildRadioButton(final String id, JSONObject props) {
        RadioButton radio = new RadioButton(owner.context);
        radio.setText(props.optString("text", ""));
        radio.setTextColor(owner.colorText);
        radio.setButtonTintList(controlTint());
        radio.setChecked(props.optBoolean("selected", false));
        radio.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        return radio;
    }

    View buildRadioGroup(JSONObject node, final String id, JSONObject props)
            throws JSONException {
        RadioGroup group = new RadioGroup(owner.context);
        group.setOrientation(LinearLayout.VERTICAL);
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                JSONObject childNode = children.getJSONObject(i);
                // buildRadioButton already applies the child's `selected`
                // state directly. Do not call group.check(...) here: children
                // never carry a View id (nothing calls setId), so the old
                // expression evaluated to check(NO_ID) and cleared the group.
                group.addView(owner.buildChild(childNode));
            }
        }
        // The outer group carries the widget id.
        group.setTag(id);
        return group;
    }
}
