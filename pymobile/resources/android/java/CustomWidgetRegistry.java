package org.pymobile.app;

import android.content.Context;
import android.util.Log;
import android.view.View;

import org.json.JSONObject;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/** Discovers project-local ``WidgetRenderer`` implementations by convention. */
final class CustomWidgetRegistry {
    private static final String TAG = "pymobile";
    private static final String RENDERER_PACKAGE = "org.pymobile.app.widgets.";
    private static final Map<String, WidgetRenderer> RENDERERS = new HashMap<>();
    private static final Set<String> LOOKED_UP = new HashSet<>();

    private CustomWidgetRegistry() {
    }

    static View build(Context context, String type, String id, JSONObject props) {
        WidgetRenderer renderer = rendererFor(type);
        return renderer == null ? null : renderer.create(context, id, props);
    }

    static boolean hasRenderer(String type) {
        return rendererFor(type) != null;
    }

    static boolean update(View view, String type, String id, JSONObject props) {
        WidgetRenderer renderer = rendererFor(type);
        return renderer != null && renderer.update(view, id, props);
    }

    private static synchronized WidgetRenderer rendererFor(String type) {
        if (type == null || !type.matches("[A-Za-z][A-Za-z0-9]*")) {
            return null;
        }
        WidgetRenderer cached = RENDERERS.get(type);
        if (cached != null || LOOKED_UP.contains(type)) {
            return cached;
        }
        LOOKED_UP.add(type);
        String className = RENDERER_PACKAGE + type + "Renderer";
        try {
            Class<?> candidate = Class.forName(className);
            Object instance = candidate.newInstance();
            if (!(instance instanceof WidgetRenderer)) {
                Log.e(TAG, className + " must implement org.pymobile.app.WidgetRenderer");
                return null;
            }
            WidgetRenderer renderer = (WidgetRenderer) instance;
            if (!type.equals(renderer.typeName())) {
                Log.e(TAG, className + " declares typeName()=" + renderer.typeName()
                        + ", expected " + type);
                return null;
            }
            RENDERERS.put(type, renderer);
            return renderer;
        } catch (ClassNotFoundException missing) {
            return null;
        } catch (InstantiationException | IllegalAccessException | LinkageError error) {
            Log.e(TAG, "could not load custom widget renderer " + className, error);
            return null;
        }
    }
}
