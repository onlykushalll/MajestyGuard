"""Root test suite configuration for MajestyGuard.

Guarantees ONNX and ONNXRuntime native C++ DLLs are initialized before any
PyQt6 DLL is loaded during test session collection, preventing Windows DLL
initialization conflict: `ImportError: DLL load failed while importing onnx_cpp2py_export`.
"""
try:
    import onnx
    import onnxruntime
except ImportError:
    pass
