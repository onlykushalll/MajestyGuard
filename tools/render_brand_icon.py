"""Generate a multi-resolution Windows icon from the native monogram."""
import io
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtWidgets import QApplication

from ui.brand import icon_image


def main(target):
    app = QApplication([])
    sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    images = []
    for size in sizes:
        encoded = QByteArray()
        writer = QBuffer(encoded)
        writer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not icon_image(size).save(writer, "PNG"):
            raise OSError("Could not render branding icon")
        with Image.open(io.BytesIO(bytes(encoded))) as image:
            images.append(image.convert("RGBA"))
    images[-1].save(target, format="ICO", sizes=[(size, size) for size in sizes],
                    append_images=images[:-1])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
