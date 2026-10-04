package org.pymobile.app;

import android.content.Context;
import android.view.View;

import org.json.JSONObject;

/**
 * Project-local renderer contract for a custom PyMobile widget.
 *
 * Put an implementation named ``<TypeName>Renderer`` in
 * ``java/org/pymobile/app/widgets/`` in the project. The framework discovers it
 * automatically; project code never edits the installed ViewBuilder.java.
 */
public interface WidgetRenderer {
    /** The serialised Python widget type this renderer handles. */
    String typeName();

    /** Build the native view for one widget node. */
    View create(Context context, String id, JSONObject props);

    /**
     * Patch a live view after the Python widget's props change.
     *
     * Return true when the update is complete. Return false to ask PyMobile to
     * rebuild the screen (correct, but slower and may reset transient view
     * state). The default is deliberately safe.
     */
    default boolean update(View view, String id, JSONObject props) {
        return false;
    }
}
