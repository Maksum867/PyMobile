package org.pymobile.app.widgets;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewParent;

import org.json.JSONObject;
import org.pymobile.app.Native;
import org.pymobile.app.WidgetRenderer;

import java.util.ArrayList;
import java.util.List;

/** Native drawing surface for {@code pymobile.SignaturePad}. */
public final class SignaturePadRenderer implements WidgetRenderer {
    @Override
    public String typeName() {
        return "SignaturePad";
    }

    @Override
    public View create(Context context, String id, JSONObject props) {
        Pad pad = new Pad(context, id);
        pad.configure(props);
        pad.revision = props.optInt("revision", 0);
        return pad;
    }

    @Override
    public boolean update(View view, String id, JSONObject props) {
        if (!(view instanceof Pad)) {
            return false;
        }
        Pad pad = (Pad) view;
        pad.configure(props);
        int revision = props.optInt("revision", 0);
        if (revision != pad.revision) {
            // Python called clear(): the drawing is erased without sending an event.
            pad.revision = revision;
            pad.erase();
        }
        return true;
    }

    /** The drawing surface. Strokes are stored in 0..1 coordinates of the view. */
    static final class Pad extends View {
        private final String id;
        private final float density;
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final List<List<float[]>> strokes = new ArrayList<>();
        private List<float[]> current;
        private int heightPx;
        int revision;

        Pad(Context context, String id) {
            super(context);
            this.id = id;
            this.density = context.getResources().getDisplayMetrics().density;
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            setContentDescription("Signature pad");
        }

        void configure(JSONObject props) {
            heightPx = Math.round(props.optInt("height", 200) * density);
            paint.setColor(parseColour(props.optString("stroke_color", "#000000"), Color.BLACK));
            paint.setStrokeWidth((float) props.optDouble("stroke_width", 4.0) * density);
            setBackgroundColor(parseColour(props.optString("background", "#FFFFFF"), Color.WHITE));
            invalidate();
        }

        void erase() {
            strokes.clear();
            current = null;
            invalidate();
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            int widthMode = MeasureSpec.getMode(widthMeasureSpec);
            int width = widthMode == MeasureSpec.UNSPECIFIED
                    ? Math.round(300 * density)
                    : MeasureSpec.getSize(widthMeasureSpec);
            setMeasuredDimension(width, resolveSize(heightPx, heightMeasureSpec));
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            float w = getWidth();
            float h = getHeight();
            if (w <= 0 || h <= 0) {
                return false;
            }
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    // Keep a surrounding ScrollView from taking the stroke away.
                    setParentIntercept(true);
                    current = new ArrayList<>();
                    strokes.add(current);
                    addPoint(event.getX(), event.getY(), w, h);
                    invalidate();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    if (current == null) {
                        return true;
                    }
                    for (int i = 0; i < event.getHistorySize(); i++) {
                        addPoint(event.getHistoricalX(i), event.getHistoricalY(i), w, h);
                    }
                    addPoint(event.getX(), event.getY(), w, h);
                    invalidate();
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (current != null) {
                        current = null;
                        setParentIntercept(false);
                        publish();
                    }
                    return true;
                default:
                    return false;
            }
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float w = getWidth();
            float h = getHeight();
            for (List<float[]> stroke : strokes) {
                if (stroke.size() == 1) {
                    float[] p = stroke.get(0);
                    canvas.drawPoint(p[0] * w, p[1] * h, paint);
                    continue;
                }
                Path path = new Path();
                float[] first = stroke.get(0);
                path.moveTo(first[0] * w, first[1] * h);
                for (int i = 1; i < stroke.size(); i++) {
                    float[] p = stroke.get(i);
                    path.lineTo(p[0] * w, p[1] * h);
                }
                canvas.drawPath(path, paint);
            }
        }

        private void addPoint(float x, float y, float w, float h) {
            float nx = Math.min(1f, Math.max(0f, x / w));
            float ny = Math.min(1f, Math.max(0f, y / h));
            current.add(new float[] {nx, ny});
        }

        private void setParentIntercept(boolean disallow) {
            ViewParent parent = getParent();
            if (parent != null) {
                parent.requestDisallowInterceptTouchEvent(disallow);
            }
        }

        /** Send every stroke so far to Python: [[[x, y], ...], ...] in 0..1. */
        private void publish() {
            StringBuilder json = new StringBuilder("[");
            for (int s = 0; s < strokes.size(); s++) {
                if (s > 0) {
                    json.append(',');
                }
                json.append('[');
                List<float[]> stroke = strokes.get(s);
                for (int i = 0; i < stroke.size(); i++) {
                    float[] p = stroke.get(i);
                    if (i > 0) {
                        json.append(',');
                    }
                    json.append('[').append(round4(p[0])).append(',').append(round4(p[1])).append(']');
                }
                json.append(']');
            }
            json.append(']');
            Native.dispatchEvent(id, "change", json.toString());
        }

        private static String round4(float value) {
            return Double.toString(Math.round(value * 10000.0) / 10000.0);
        }

        private static int parseColour(String value, int fallback) {
            try {
                return Color.parseColor(value);
            } catch (IllegalArgumentException invalid) {
                return fallback;
            }
        }
    }
}
