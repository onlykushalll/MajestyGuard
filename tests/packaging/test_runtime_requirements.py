from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_pins_the_cpu_onnx_runtime_for_the_cpu_first_pipeline():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    assert "onnxruntime==1.18.1" in requirements
    assert "onnx==1.22.0" in requirements
    assert "mediapipe==0.10.21" in requirements
    assert "numpy==1.26.4" in requirements
    assert "protobuf==4.25.8" in requirements
    assert "opencv-contrib-python==4.10.0.84" in requirements
    assert "onnxruntime-directml" not in requirements
