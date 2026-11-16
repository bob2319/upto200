"""Generate deterministic synthetic fixtures for integration tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def ensure_fixtures() -> Path:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    photo = Image.new("RGB", (640, 400), (24, 48, 72))
    draw = ImageDraw.Draw(photo)
    for i in range(0, 640, 16):
        draw.rectangle([i, 0, i + 8, 400], fill=(40 + (i % 80), 90, 120))
    draw.ellipse([180, 80, 460, 320], fill=(210, 160, 90))
    photo.save(FIXTURES_DIR / "photo.jpg", quality=92)

    shot = Image.new("RGB", (900, 560), (245, 246, 248))
    draw = ImageDraw.Draw(shot)
    draw.rectangle([0, 0, 900, 48], fill=(35, 45, 55))
    draw.text((20, 14), "Dashboard / Settings", fill=(255, 255, 255))
    draw.rectangle([40, 90, 860, 500], outline=(180, 190, 200), width=2)
    draw.text((60, 120), "Export report", fill=(20, 20, 20))
    draw.text((60, 160), "Invoice #1042 — $128.00", fill=(40, 40, 40))
    shot.save(FIXTURES_DIR / "screenshot.png")

    text_img = Image.new("RGB", (700, 220), (255, 255, 255))
    draw = ImageDraw.Draw(text_img)
    draw.text((24, 40), "The quick brown fox jumps over the lazy dog.", fill=(10, 10, 10))
    draw.text((24, 90), "OPTIMIZE TO 150 KB", fill=(15, 90, 70))
    draw.text((24, 140), "0123456789 — clarity check", fill=(30, 30, 30))
    text_img.save(FIXTURES_DIR / "text.png")

    alpha = Image.new("RGBA", (240, 180), (0, 0, 0, 0))
    draw = ImageDraw.Draw(alpha)
    draw.ellipse([30, 20, 210, 160], fill=(220, 50, 50, 255))
    draw.rectangle([140, 40, 220, 140], fill=(40, 120, 220, 120))
    alpha.save(FIXTURES_DIR / "transparent.png")

    large = Image.new("RGB", (3200, 1800), (90, 110, 130))
    draw = ImageDraw.Draw(large)
    draw.rectangle([200, 200, 3000, 1600], fill=(150, 80, 60))
    large.save(FIXTURES_DIR / "large.jpg", quality=95)

    webp = Image.new("RGB", (320, 240), (30, 140, 120))
    ImageDraw.Draw(webp).text((40, 100), "webp-source", fill=(255, 255, 255))
    webp.save(FIXTURES_DIR / "source.webp", "WEBP", quality=90)

    # Distinctive EXIF-oriented JPEG: stored 200x100, orientation 6 => visual 100x200.
    oriented = Image.new("RGB", (200, 100), (255, 255, 255))
    draw = ImageDraw.Draw(oriented)
    draw.rectangle([150, 20, 190, 80], fill=(220, 30, 30))  # mark on the right
    exif = oriented.getexif()
    exif[0x0112] = 6  # Rotate 90 CW
    oriented.save(FIXTURES_DIR / "exif_orient.jpg", quality=95, exif=exif)

    if pillow_heif is not None:
        heic_img = Image.new("RGB", (160, 120), (180, 70, 40))
        ImageDraw.Draw(heic_img).ellipse([20, 20, 140, 100], fill=(240, 220, 180))
        pillow_heif.from_pillow(heic_img).save(
            str(FIXTURES_DIR / "sample.heic"), quality=90
        )
        pillow_heif.from_pillow(heic_img).save(
            str(FIXTURES_DIR / "sample.heif"), quality=90
        )

    return FIXTURES_DIR
