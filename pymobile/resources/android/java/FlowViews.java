package org.pymobile.app;

import android.content.Context;
import android.util.AttributeSet;
import android.view.View;
import android.view.ViewGroup;

/** Flow measurement and wrapping views, independent of widget dispatch. */
final class FlowViews {
    private FlowViews() {
    }

    // -- flow layout ---------------------------------------------------------
    // Wrap, and SegmentedButtons with wrap=true, put children side by side and start a
    // new line when the next one does not fit. Android has no such ViewGroup.

    /**
     * The rule a {@link FlowLayout} breaks its lines by.
     *
     * Free of Android classes so it can be checked on any JVM, and the same rule as
     * {@code flow_lines()} in pymobile/core/ui/flow.py, which the desktop previews
     * use: change one and change the other.
     */
    static final class FlowMath {
        private FlowMath() {
        }

        /**
         * The line of every child: -1 for a hidden one (negative width), otherwise
         * 0, 1, 2 ... A child stays on the current line if it fits in what is left
         * after the gap. The first child of a line is always placed, even when it is
         * wider than the line: it then takes the whole line instead of vanishing.
         */
        static int[] lines(int[] widths, int available, int gap) {
            int[] line = new int[widths.length];
            int current = -1;
            long used = 0;
            for (int i = 0; i < widths.length; i++) {
                if (widths[i] < 0) {
                    line[i] = -1;
                    continue;
                }
                if (current < 0) {
                    current = 0;
                    used = widths[i];
                } else if (used + gap + widths[i] > available) {
                    current++;
                    used = widths[i];
                } else {
                    used += gap + widths[i];
                }
                line[i] = current;
            }
            return line;
        }

        /** Space before the first child of a line that is {@code used} wide. */
        static int offset(int alignment, int available, long used) {
            long spare = Math.max(0, available - used);
            if (alignment == FlowLayout.ALIGN_CENTER) {
                return (int) (spare / 2);
            }
            return alignment == FlowLayout.ALIGN_END ? (int) spare : 0;
        }
    }

    /**
     * Children in lines: left to right, a new line when the next child does not fit.
     *
     * Every child keeps its natural size (a child wider than the layout gets a line
     * of its own and wraps its own content), margins are honoured, and hidden
     * (GONE) children take no room. {@code gap} separates children on a line,
     * {@code runGap} the lines; a line shorter than the layout is placed by
     * {@code alignment}.
     */
    static final class FlowLayout extends ViewGroup {
        static final int ALIGN_START = 0;
        static final int ALIGN_CENTER = 1;
        static final int ALIGN_END = 2;

        private final int gap;
        private final int runGap;
        private final int alignment;

        FlowLayout(Context context, int gap, int runGap, int alignment) {
            super(context);
            this.gap = gap;
            this.runGap = runGap;
            this.alignment = alignment;
        }

        /** The children grouped into lines for a given inner width, from their measured sizes. */
        private final class Rows {
            final int[] line;
            final int[] outerWidth;
            final int[] outerHeight;
            final int[] lineWidth;
            final int[] lineHeight;

            Rows(int available) {
                int count = getChildCount();
                outerWidth = new int[count];
                outerHeight = new int[count];
                for (int i = 0; i < count; i++) {
                    View child = getChildAt(i);
                    if (child.getVisibility() == View.GONE) {
                        outerWidth[i] = -1;
                        continue;
                    }
                    MarginLayoutParams params = (MarginLayoutParams) child.getLayoutParams();
                    outerWidth[i] = child.getMeasuredWidth() + params.leftMargin + params.rightMargin;
                    outerHeight[i] = child.getMeasuredHeight() + params.topMargin + params.bottomMargin;
                }
                line = FlowMath.lines(outerWidth, available, gap);
                int lines = 0;
                for (int value : line) {
                    lines = Math.max(lines, value + 1);
                }
                lineWidth = new int[lines];
                lineHeight = new int[lines];
                int[] members = new int[lines];
                for (int i = 0; i < count; i++) {
                    int l = line[i];
                    if (l < 0) {
                        continue;
                    }
                    lineWidth[l] = members[l] == 0 ? outerWidth[i] : lineWidth[l] + gap + outerWidth[i];
                    lineHeight[l] = Math.max(lineHeight[l], outerHeight[i]);
                    members[l]++;
                }
            }
        }

        @Override
        protected void onMeasure(int widthSpec, int heightSpec) {
            int count = getChildCount();
            for (int i = 0; i < count; i++) {
                View child = getChildAt(i);
                if (child.getVisibility() != View.GONE) {
                    measureChildWithMargins(child, widthSpec, 0, heightSpec, 0);
                }
            }
            int padding = getPaddingLeft() + getPaddingRight();
            int available = MeasureSpec.getMode(widthSpec) == MeasureSpec.UNSPECIFIED
                    ? Integer.MAX_VALUE
                    : Math.max(0, MeasureSpec.getSize(widthSpec) - padding);
            Rows rows = new Rows(available);
            int contentWidth = 0;
            int contentHeight = 0;
            for (int l = 0; l < rows.lineWidth.length; l++) {
                contentWidth = Math.max(contentWidth, rows.lineWidth[l]);
                contentHeight += rows.lineHeight[l] + (l > 0 ? runGap : 0);
            }
            setMeasuredDimension(
                    resolveSize(contentWidth + padding, widthSpec),
                    resolveSize(contentHeight + getPaddingTop() + getPaddingBottom(), heightSpec));
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            int inner = Math.max(0, right - left - getPaddingLeft() - getPaddingRight());
            Rows rows = new Rows(inner);
            int count = getChildCount();
            int y = getPaddingTop();
            for (int l = 0; l < rows.lineWidth.length; l++) {
                int x = getPaddingLeft() + FlowMath.offset(alignment, inner, rows.lineWidth[l]);
                for (int i = 0; i < count; i++) {
                    if (rows.line[i] != l) {
                        continue;
                    }
                    View child = getChildAt(i);
                    MarginLayoutParams params = (MarginLayoutParams) child.getLayoutParams();
                    int childLeft = x + params.leftMargin;
                    int childTop = y + params.topMargin;
                    child.layout(childLeft, childTop,
                            childLeft + child.getMeasuredWidth(),
                            childTop + child.getMeasuredHeight());
                    x += rows.outerWidth[i] + gap;
                }
                y += rows.lineHeight[l] + runGap;
            }
        }

        @Override
        protected boolean checkLayoutParams(ViewGroup.LayoutParams params) {
            return params instanceof MarginLayoutParams;
        }

        @Override
        protected ViewGroup.LayoutParams generateDefaultLayoutParams() {
            return new MarginLayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        }

        @Override
        protected ViewGroup.LayoutParams generateLayoutParams(ViewGroup.LayoutParams params) {
            return new MarginLayoutParams(params);
        }

        @Override
        public ViewGroup.LayoutParams generateLayoutParams(AttributeSet attributes) {
            return new MarginLayoutParams(getContext(), attributes);
        }
    }

}
