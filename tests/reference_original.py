"""Exact copy of the original compress_image algorithm from main.py.

Used only by regression tests to verify the refactored engine preserves
compression behavior. Do not use this module in application code.
"""

from PIL import Image
import pillow_heif
import os

pillow_heif.register_heif_opener()

max_size_kb = 150
max_width = 1920
min_quality = 40
step_quality = 5


def compress_image(file_path, output_path):
    img = Image.open(file_path)

    # Resize proportionally
    if img.width > max_width:
        ratio = max_width / img.width
        new_size = (max_width, int(img.height * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    # Definition of transparency
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)

    img = img.convert("RGBA" if has_alpha else "RGB")

    # Initial quality (for lossy)
    quality = 90

    while quality >= min_quality:
        if has_alpha:
            # tradeoff: lossy storage with transparency
            img.save(output_path, "WEBP", quality=quality, method=6, lossless=False)
        else:
            img.save(output_path, "WEBP", quality=quality, method=6)

        size_kb = os.path.getsize(output_path) / 1024
        if size_kb <= max_size_kb:
            return size_kb, quality
        quality -= step_quality

    # fallback
    return os.path.getsize(output_path) / 1024, quality
