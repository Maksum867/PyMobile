"""Source-contract fixture for the split Android renderer.

Implementation collaborators come before the delegating facade, so method-body
checks still inspect the implementation, not just a forwarding seam. Strip the
explicit receiver only: the semantic checks are independent of field ownership.
Compilation and dex packaging are verified separately.
"""

from pathlib import Path


def renderer_source() -> str:
    root = Path(__file__).resolve().parents[1] / "resources/android/java"
    parts = sorted(p for p in root.glob("View*.java") if p.name != "ViewBuilder.java")
    parts += [root / "FlowViews.java", root / "ViewBuilder.java"]
    return "\n".join(p.read_text(encoding="utf-8") for p in parts).replace("owner.", "")
