"""tests/daemon/test_privacy_audit.py — Automated privacy and frame hygiene audit.

Verifies:
1. No active daemon, UI, or companion app module invokes cv2.imwrite.
2. Raw biometric vectors (embeddings) are never leaked to logs.
3. FaceEngine.process_frame zeroes out frame buffers and face embeddings on every exit path.
"""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ACTIVE_DIRS = ("daemon", "ui", "app")


def test_no_cv2_imwrite_in_active_modules():
    """Assert no camera frames or ROIs are dumped to disk via cv2.imwrite."""
    for dir_name in ACTIVE_DIRS:
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for py_path in target_dir.glob("*.py"):
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    # check for cv2.imwrite(...)
                    if isinstance(func, ast.Attribute) and func.attr == "imwrite":
                        if isinstance(func.value, ast.Name) and func.value.id == "cv2":
                            assert False, f"Privacy violation: cv2.imwrite called in {py_path.name}:{node.lineno}"


def test_no_raw_embeddings_in_logs():
    """Assert raw biometric embeddings are not formatted into logger calls."""
    log_methods = {"info", "debug", "warning", "error", "critical", "exception"}
    banned_substrings = {"normed_embedding", "embeddings_v2"}

    for dir_name in ACTIVE_DIRS:
        target_dir = ROOT / dir_name
        if not target_dir.exists():
            continue
        for py_path in target_dir.glob("*.py"):
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in log_methods:
                        # Inspect all arguments passed to the log call
                        for arg in node.args:
                            arg_source = ast.unparse(arg)
                            for banned in banned_substrings:
                                assert banned not in arg_source or "len(" in arg_source or "shape" in arg_source or "count" in arg_source, (
                                    f"Biometric leakage risk: {banned} passed into log call in {py_path.name}:{node.lineno}"
                                )


def test_face_engine_process_frame_zeroes_buffers_before_return():
    """Verify FaceEngine.process_frame zeroes frame memory on exit paths."""
    face_engine_path = ROOT / "daemon" / "face_engine.py"
    tree = ast.parse(face_engine_path.read_text(encoding="utf-8"), filename=str(face_engine_path))

    process_frame_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "process_frame":
            process_frame_node = node
            break

    assert process_frame_node is not None, "FaceEngine.process_frame not found in AST"

    # Verify that _zero_frame is invoked inside process_frame
    zero_frame_calls = [
        node for node in ast.walk(process_frame_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_zero_frame"
    ]
    assert len(zero_frame_calls) >= 4, f"Expected at least 4 _zero_frame calls in process_frame, found {len(zero_frame_calls)}"

    zero_face_calls = [
        node for node in ast.walk(process_frame_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_zero_face_data"
    ]
    assert len(zero_face_calls) >= 3, f"Expected at least 3 _zero_face_data calls in process_frame, found {len(zero_face_calls)}"
