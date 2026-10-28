package org.pymobile.app;

import android.app.Dialog;
import android.content.DialogInterface;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.GradientDrawable;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ViewConfiguration;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;




import org.pymobile.app.ViewBuilder.DialogHost;

/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewOverlays {
    private final ViewBuilder owner;

    ViewOverlays(ViewBuilder owner) {
        this.owner = owner;
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
        final String id = node.optString("id", "");
        final FrameLayout anchor = new FrameLayout(owner.context);

        LinearLayout box = new LinearLayout(owner.context);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = owner.dp(16);
        box.setPadding(pad, pad, pad, pad);
        TextView head = new TextView(owner.context);
        head.setTypeface(null, Typeface.BOLD);
        head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        head.setPadding(0, 0, 0, owner.dp(8));
        box.addView(head, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        JSONArray children = node.optJSONArray("children");
        int spacing = owner.dp(props.optInt("spacing", 0));
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                if (spacing > 0 && i > 0) {
                    // C-2: the dialog's own spacing, without a wrapping Column.
                    params.topMargin = spacing;
                }
                box.addView(owner.buildChild(children.getJSONObject(i)), params);
            }
        }
        ScrollView scroll = new ScrollView(owner.context);
        scroll.addView(box, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        Dialog window = new Dialog(owner.context);
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
        owner.dialogs.put(anchor, host);
        owner.applyDialogProps(host, props);

        anchor.addOnAttachStateChangeListener(new View.OnAttachStateChangeListener() {
            @Override
            public void onViewAttachedToWindow(View v) {
                owner.syncDialog(v);
            }

            @Override
            public void onViewDetachedFromWindow(View v) {
                DialogHost h = owner.dialogs.get(v);
                if (h != null && h.window.isShowing()) {
                    h.window.dismiss();
                }
            }
        });
        return anchor;
    }

    void applyDialogProps(DialogHost host, JSONObject props) {
        String title = props.optString("title", "");
        if (!title.contentEquals(host.title.getText())) {
            host.title.setText(title);
        }
        host.title.setVisibility(title.isEmpty() ? View.GONE : View.VISIBLE);
        host.title.setTextColor(owner.colorText);
        boolean sheet = props.optBoolean("sheet", false);
        GradientDrawable surface = new GradientDrawable();
        surface.setColor(owner.colorSurface);
        float r = owner.dp(sheet ? 16 : 12);
        surface.setCornerRadii(sheet
                ? new float[]{r, r, r, r, 0, 0, 0, 0}
                : new float[]{r, r, r, r, r, r, r, r});
        host.box.setBackground(surface);
        Window w = host.window.getWindow();
        if (w != null) {
            w.setBackgroundDrawable(new ColorDrawable(Color.TRANSPARENT));
            w.setGravity(sheet ? Gravity.BOTTOM : Gravity.CENTER);
            int screen = owner.context.getResources().getDisplayMetrics().widthPixels;
            w.setLayout(sheet ? ViewGroup.LayoutParams.MATCH_PARENT : Math.round(screen * 0.88f),
                    ViewGroup.LayoutParams.WRAP_CONTENT);
        }
    }

    /** Show or dismiss the dialog window to match the node and the anchor. */
    void syncDialog(View anchor) {
        DialogHost host = owner.dialogs.get(anchor);
        if (host == null) {
            return;
        }
        boolean show = host.wanted && anchor.getWindowToken() != null && owner.ancestorsVisible(anchor);
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

    /** Whether a view is the snackbar (MainActivity keeps it when rebuilding). */
    boolean isSnackbar(View view) {
        return view != null && view == owner.snackbar;
    }

    /** Show, update or hide the snackbar described by root["snackbar"]. */
    void syncSnackbar(FrameLayout container, JSONObject data) {
        if (data == null) {
            owner.hideSnackbar();
            return;
        }
        if (owner.snackbar == null) {
            owner.createSnackbar();
        }
        if (owner.snackbar.getParent() != container) {
            if (owner.snackbar.getParent() instanceof ViewGroup) {
                ((ViewGroup) owner.snackbar.getParent()).removeView(owner.snackbar);
            }
            FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.BOTTOM);
            int margin = owner.dp(12);
            params.setMargins(margin, margin, margin, margin);
            container.addView(owner.snackbar, params);
        } else if (container.indexOfChild(owner.snackbar) != container.getChildCount() - 1) {
            owner.snackbar.bringToFront();
        }
        owner.styleSnackbar();
        final int token = data.optInt("token", 0);
        owner.snackbarText.setText(data.optString("message", ""));
        String action = data.optString("action", "");
        owner.snackbarAction.setText(action);
        owner.snackbarAction.setVisibility(action.isEmpty() ? View.GONE : View.VISIBLE);
        owner.snackbarAction.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(owner.SNACKBAR_ID, "press", String.valueOf(token));
            }
        });
        if (token != owner.snackbarToken || owner.snackbar.getVisibility() != View.VISIBLE || owner.snackbarHiding) {
            owner.snackbarToken = token;
            owner.snackbarHiding = false;
            owner.snackbar.animate().cancel();
            owner.snackbar.setVisibility(View.VISIBLE);
            owner.snackbar.setTranslationX(0f);
            owner.snackbar.setAlpha(1f);
            owner.snackbar.setTranslationY(owner.dp(96));
            owner.snackbar.animate().translationY(0f).setDuration(200).start();
        }
    }

    void hideSnackbar() {
        if (owner.snackbar == null || owner.snackbar.getVisibility() != View.VISIBLE || owner.snackbarHiding) {
            return;
        }
        owner.snackbarHiding = true;
        owner.snackbarToken = -1;
        owner.snackbar.animate().cancel();
        owner.snackbar.animate().translationY(owner.dp(96)).alpha(0f).setDuration(180)
                .withEndAction(new Runnable() {
                    @Override
                    public void run() {
                        if (owner.snackbarHiding) {
                            owner.snackbar.setVisibility(View.GONE);
                            owner.snackbarHiding = false;
                        }
                    }
                })
                .start();
    }

    void createSnackbar() {
        owner.snackbar = new LinearLayout(owner.context);
        owner.snackbar.setOrientation(LinearLayout.HORIZONTAL);
        owner.snackbar.setGravity(Gravity.CENTER_VERTICAL);
        owner.snackbar.setPadding(owner.dp(16), owner.dp(6), owner.dp(8), owner.dp(6));
        owner.snackbar.setMinimumHeight(owner.dp(48));
        owner.snackbar.setVisibility(View.GONE);
        owner.snackbar.setClickable(true);  // taps must not fall through to the screen
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            owner.snackbar.setElevation(owner.dp(6));
        }
        owner.snackbarText = new TextView(owner.context);
        owner.snackbarText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        owner.snackbarText.setMaxLines(2);
        owner.snackbar.addView(owner.snackbarText, new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        owner.snackbarAction = new TextView(owner.context);
        owner.snackbarAction.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
        owner.snackbarAction.setTypeface(Typeface.DEFAULT_BOLD);
        owner.snackbarAction.setPadding(owner.dp(12), owner.dp(10), owner.dp(12), owner.dp(10));
        owner.snackbarAction.setClickable(true);
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            android.content.res.TypedArray a = owner.context.obtainStyledAttributes(
                    new int[]{android.R.attr.selectableItemBackground});
            owner.snackbarAction.setBackgroundResource(a.getResourceId(0, 0));
            a.recycle();
        }
        owner.snackbar.addView(owner.snackbarAction, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        owner.snackbar.setOnTouchListener(new View.OnTouchListener() {
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
                        if (!dragging && Math.abs(dx) > ViewConfiguration.get(owner.context).getScaledTouchSlop()) {
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
                            final int token = owner.snackbarToken;
                            owner.snackbarHiding = true;
                            v.animate().translationX(Math.signum(dx) * v.getWidth()).alpha(0f)
                                    .setDuration(150).withEndAction(new Runnable() {
                                        @Override
                                        public void run() {
                                            owner.snackbar.setVisibility(View.GONE);
                                            owner.snackbarHiding = false;
                                            Native.dispatchEvent(owner.SNACKBAR_ID, "dismiss",
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
    void styleSnackbar() {
        GradientDrawable shape = new GradientDrawable();
        shape.setCornerRadius(owner.dp(4));
        shape.setColor(owner.darkTheme ? Color.parseColor("#E6E6E6") : Color.parseColor("#323232"));
        owner.snackbar.setBackground(shape);
        owner.snackbarText.setTextColor(owner.darkTheme ? Color.parseColor("#212121") : Color.WHITE);
        int action = owner.colorPrimary;
        if (!owner.darkTheme) {
            // Lighten the primary colour so the action reads on the dark bar.
            int r = Color.red(action) + (255 - Color.red(action)) * 45 / 100;
            int g = Color.green(action) + (255 - Color.green(action)) * 45 / 100;
            int b = Color.blue(action) + (255 - Color.blue(action)) * 45 / 100;
            action = Color.rgb(r, g, b);
        }
        owner.snackbarAction.setTextColor(action);
    }
}
