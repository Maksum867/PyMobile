package org.pymobile.app;

import android.animation.ValueAnimator;
import android.graphics.Rect;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.ViewParent;
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewTreeObserver;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;




import org.pymobile.app.ViewBuilder.ListState;
import org.pymobile.app.ViewGestures.PullList;
import org.pymobile.app.ViewBuilder.SwipeState;

/** Focused renderer collaborator; all mutable state belongs to ViewBuilder. */
final class ViewLists {
    private final ViewBuilder owner;

    ViewLists(ViewBuilder owner) {
        this.owner = owner;
    }


    /**
     * List is already virtualised on the Python side (only a window of rows is
     * serialised), so here it is a plain vertical stack of its children.
     */
    View buildList(JSONObject node, JSONObject props) throws JSONException {
        String id = node.optString("id", "");
        PullList list = new PullList(owner, id);
        list.setOrientation(LinearLayout.VERTICAL);
        ListState state = new ListState();
        state.hasMore = props.optBoolean("has_more", false);
        owner.lists.put(list, state);
        if (props.optBoolean("refreshable", false)) {
            state.header = owner.buildRefreshHeader();
            list.addView(state.header, new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, 0));
            owner.applyRefreshing(state, props.optBoolean("refreshing", false), false);
        }
        int spacing = owner.dp(props.optInt("spacing", 0));
        JSONArray children = node.optJSONArray("children");
        if (children != null) {
            for (int i = 0; i < children.length(); i++) {
                LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT);
                if (spacing > 0 && i > 0) {
                    params.topMargin = spacing;
                }
                list.addView(owner.buildChild(children.getJSONObject(i)), params);
            }
        }
        owner.watchList(id, list, props);
        owner.applyScrollRequest(id, list, props);
        return list;
    }

    /** Rows before the first data row: the pull-to-refresh header, if any. */
    int rowOffset(LinearLayout list) {
        ListState state = owner.lists.get(list);
        return state != null && state.header != null ? 1 : 0;
    }

    /** The spinner row a refreshable List keeps (collapsed) above its rows. */
    FrameLayout buildRefreshHeader() {
        FrameLayout header = new FrameLayout(owner.context);
        ProgressBar spinner = new ProgressBar(owner.context);
        spinner.setIndeterminate(true);
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(owner.dp(32), owner.dp(32));
        params.gravity = Gravity.CENTER;
        header.addView(spinner, params);
        header.setClipChildren(true);
        return header;
    }

    void setHeaderHeight(ListState state, int height) {
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
            spinner.setAlpha(Math.min(1f, height / (float) owner.dp(56)));
        }
    }

    void animateHeader(final ListState state, int target) {
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
                owner.setHeaderHeight(state, (Integer) animation.getAnimatedValue());
            }
        });
        state.headerAnimator = animator;
        animator.start();
    }

    /** Show or hide the spinner as Python asks (refreshing = True/False). */
    void applyRefreshing(ListState state, boolean refreshing, boolean animate) {
        state.refreshing = refreshing;
        int target = refreshing ? owner.dp(56) : 0;
        if (animate) {
            owner.animateHeader(state, target);
        } else {
            owner.setHeaderHeight(state, target);
        }
    }

    /** Scroll to the row a List.scroll_to() asked for, once per request. */
    void applyScrollRequest(String id, final LinearLayout list, JSONObject props) {
        int serial = props.optInt("scroll_serial", 0);
        final int index = props.optInt("scroll_to", -1);
        if (serial <= 0 || index < 0) {
            return;
        }
        Integer seen = owner.scrollSerials.get(id);
        if (seen != null && seen == serial) {
            return;
        }
        owner.scrollSerials.put(id, serial);
        final boolean animated = props.optBoolean("scroll_animated", true);
        // Posted: rows appended in this frame are laid out first.
        list.post(new Runnable() {
            @Override
            public void run() {
                owner.scrollToRow(list, index, animated);
            }
        });
    }

    void scrollToRow(LinearLayout list, int index, boolean animated) {
        int child = index + owner.rowOffset(list);
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
    void watchList(final String id, final LinearLayout list, JSONObject props) {
        final ViewTreeObserver.OnScrollChangedListener onScroll =
                new ViewTreeObserver.OnScrollChangedListener() {
                    @Override
                    public void onScrollChanged() {
                        owner.maybeLoadMore(id, list);
                    }
                };
        final ViewTreeObserver.OnGlobalLayoutListener onLayout =
                new ViewTreeObserver.OnGlobalLayoutListener() {
                    @Override
                    public void onGlobalLayout() {
                        owner.maybeLoadMore(id, list);
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

    void maybeLoadMore(String id, LinearLayout list) {
        ListState state = owner.lists.get(list);
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
    boolean updateList(LinearLayout list, JSONObject node, JSONObject props)
            throws JSONException {
        ListState state = owner.lists.get(list);
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
            if (!owner.updateNode(list.getChildAt(offset + i), children.getJSONObject(i))) {
                return false;
            }
        }
        int spacing = owner.dp(props.optInt("spacing", 0));
        for (int i = existing; i < count; i++) {
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
            if (spacing > 0 && i > 0) {
                params.topMargin = spacing;
            }
            list.addView(owner.buildChild(children.getJSONObject(i)), params);
        }
        state.hasMore = props.optBoolean("has_more", false);
        if (state.header != null) {
            boolean refreshing = props.optBoolean("refreshing", false);
            ViewGroup.LayoutParams params = state.header.getLayoutParams();
            int shown = params == null ? 0 : params.height;
            boolean settled = shown == (refreshing ? owner.dp(56) : 0);
            if (refreshing != state.refreshing || !settled) {
                owner.applyRefreshing(state, refreshing, true);
            }
        }
        owner.applyScrollRequest(node.optString("id", ""), list, props);
        return true;
    }

    /** A tappable list row: title + subtitle + trailing, dispatching "press". */
    View buildListTile(final String id, JSONObject props) {
        LinearLayout row = new LinearLayout(owner.context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setPadding(owner.dp(12), owner.dp(10), owner.dp(12), owner.dp(10));
        // A subtle ripple requires a background; use a selectable borderless item.
        if (android.os.Build.VERSION.SDK_INT >= 21) {
            android.content.res.TypedArray a = owner.context.obtainStyledAttributes(
                    new int[]{android.R.attr.selectableItemBackground});
            row.setBackgroundResource(a.getResourceId(0, 0));
            a.recycle();
        }

        LinearLayout texts = new LinearLayout(owner.context);
        texts.setOrientation(LinearLayout.VERTICAL);
        // The subtitle and trailing views always exist (GONE when empty): the
        // row's structure never changes, so every field can be patched.
        TextView title = new TextView(owner.context);
        title.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        texts.addView(title);
        TextView sub = new TextView(owner.context);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        texts.addView(sub);

        LinearLayout.LayoutParams textsParams = new LinearLayout.LayoutParams(
                0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        row.addView(texts, textsParams);

        TextView trailingView = new TextView(owner.context);
        trailingView.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18);
        row.addView(trailingView);

        row.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Native.dispatchEvent(id, "press", "");
            }
        });
        owner.applyListTile(row, id, props);
        return row;
    }

    /** Fill (or re-fill) every part of a ListTile row. */
    boolean applyListTile(ViewGroup row, String id, JSONObject props) {
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
        owner.setTextAndVisibility(title, props.optString("title", ""), true);
        owner.setTextAndVisibility(sub, props.optString("subtitle", ""), false);
        owner.setTextAndVisibility(trailing, props.optString("trailing", ""), false);
        title.setTextColor(owner.colorText);
        sub.setTextColor(owner.colorTextMuted);
        trailing.setTextColor(owner.colorTextMuted);
        owner.setLongPress(row, id, props.optBoolean("long_pressable", false));
        owner.setSwipe(row, id, props);
        return true;
    }

    /** Configure (or remove) the sideways swipe of a ListTile row. */
    void setSwipe(View row, String id, JSONObject props) {
        boolean left = props.optBoolean("swipe_left", false);
        boolean right = props.optBoolean("swipe_right", false);
        SwipeState state = owner.swipes.get(row);
        if (!left && !right) {
            if (state != null) {
                owner.resetSwipe(row, state, false);
                owner.swipes.remove(row);
            }
            row.setOnTouchListener(null);
            return;
        }
        if (state == null) {
            state = new SwipeState();
            owner.swipes.put(row, state);
            row.setOnTouchListener(owner.swipeListener);
        }
        state.id = id;
        state.left = left;
        state.right = right;
        state.leftColor = owner.parseColor(props.optString("swipe_left_color", ""), state.leftColor);
        state.rightColor = owner.parseColor(props.optString("swipe_right_color", ""), state.rightColor);
        // A redraw after the handler ran (the row now shows the next item, or
        // the same one if nothing was deleted) puts the row back in place.
        if (!state.dragging && !state.committing) {
            owner.resetSwipe(row, state, false);
        }
    }

    void resetSwipe(View row, SwipeState state, boolean animate) {
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

    void setLongPress(View row, final String id, boolean enabled) {
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
}
