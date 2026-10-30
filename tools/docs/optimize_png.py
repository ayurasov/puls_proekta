"""Сжать PNG-иллюстрации документов до 256 цветов (снимки экрана сжимаются в 2–3 раза).

Метод MAXCOVERAGE: MEDIANCUT искажает небольшие насыщенные области (янтарный в легенде становится
красным), FASTOCTREE окрашивает белый фон в серый.

    python3 tools/docs/optimize_png.py docs/manuals/images/user docs/manuals/images/admin
"""
import sys
from pathlib import Path

from PIL import Image


def main(dirs):
    for folder in dirs:
        for path in sorted(Path(folder).glob("*.png")):
            before = path.stat().st_size
            image = Image.open(path).convert("RGB")
            image.quantize(colors=256, method=Image.Quantize.MAXCOVERAGE, dither=Image.Dither.NONE).save(path, optimize=True)
            print(f"  {path}: {before // 1024} → {path.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main(sys.argv[1:])
