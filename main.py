"""CLI batch optimizer — thin wrapper around the reusable engine."""

import os

from optimizer import (
    INPUT_FOLDER,
    OUTPUT_FOLDER,
    SUPPORTED_EXTENSIONS,
    compress_image,
)

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

for filename in os.listdir(INPUT_FOLDER):
    if filename.lower().endswith(SUPPORTED_EXTENSIONS):
        input_path = os.path.join(INPUT_FOLDER, filename)
        output_name = os.path.splitext(filename)[0] + ".webp"
        output_path = os.path.join(OUTPUT_FOLDER, output_name)

        original_size = os.path.getsize(input_path) / 1024
        new_size, quality = compress_image(input_path, output_path)

        print(f"{filename:25} | {original_size:7.1f}KB → {new_size:7.1f}KB (q={quality})")
