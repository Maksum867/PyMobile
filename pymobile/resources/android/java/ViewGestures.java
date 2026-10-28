package org.pymobile.app;

import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.ViewConfiguration;
import android.view.ViewParent;
import android.view.View;
import android.view.ViewGroup;
import android.widget.LinearLayout;
import android.widget.ScrollView;




import org.pymobile.app.ViewBuilder.ListState;
import org.pymobile.app.ViewBuilder.SwipeState;

/** Pull-to-refresh and swipe gesture state machines, sharing renderer-owned state. */
final class ViewGestures {
    private ViewGestures() {
    }
    static final class PullList extends LinearLayout {
        private final ViewBuilder owner;
        private final String listId;
        private final int touchSlop;
        private float downX;
        private float downY;
        private boolean armed;
        private boolean pulling;

        PullList(ViewBuilder owner, String listId) {
            super(owner.context);
            this.owner = owner;
            this.listId = listId;
            this.touchSlop = ViewConfiguration.get(owner.context).getScaledTouchSlop();
        }

        @Override
        public boolean dispatchTouchEvent(MotionEvent event) {
            ListState state = owner.lists.get(this);
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
                        int height = (int) Math.max(0, Math.min(dy * 0.5f, owner.dp(120)));
                        if (state.headerAnimator != null) {
                            state.headerAnimator.cancel();
                        }
                        owner.setHeaderHeight(state, height);
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
                                && params != null && params.height >= owner.dp(64);
                        if (release) {
                            state.refreshing = true;
                            owner.animateHeader(state, owner.dp(56));
                            performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                            Native.dispatchEvent(listId, "refresh", "");
                        } else {
                            owner.animateHeader(state, 0);
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

    static final class SwipeListener implements View.OnTouchListener {
        private final ViewBuilder owner;
        SwipeListener(ViewBuilder owner) {
            this.owner = owner;
        }

        @Override
        public boolean onTouch(final View row, MotionEvent event) {
            final SwipeState state = owner.swipes.get(row);
            if (state == null || !row.isEnabled() || state.committing) {
                return false;
            }
            int slop = ViewConfiguration.get(owner.context).getScaledTouchSlop();
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
                    row.setBackgroundColor(owner.withAlpha(color, Math.min(1f, Math.abs(tx) / third) * 0.85f));
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
                    int minFling = ViewConfiguration.get(owner.context).getScaledMinimumFlingVelocity() * 8;
                    boolean flung = Math.abs(vx) > minFling && Math.signum(vx) == Math.signum(tx)
                            && Math.abs(tx) > slop * 2;
                    boolean commit = event.getActionMasked() == MotionEvent.ACTION_UP
                            && tx != 0f && (Math.abs(tx) > row.getWidth() / 3f || flung);
                    if (!commit) {
                        owner.resetSwipe(row, state, true);
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
                                                owner.resetSwipe(row, state, true);
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
        }
}
