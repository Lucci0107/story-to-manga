"""favicon.svgと同じ記号からPNG/ICOを生成する。"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "static" / "icons"


def draw_icon(size: int) -> Image.Image:
    scale = 4
    canvas_size = 64 * scale
    image = Image.new("RGB", (canvas_size, canvas_size), "#20211f")
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(value * scale for value in values)  # type: ignore[return-value]

    radius = 14 * scale if size >= 32 else 10 * scale
    draw.rounded_rectangle((0, 0, canvas_size, canvas_size), radius=radius, fill="#20211f")
    draw.polygon(
        [(16 * scale, 10 * scale), (40 * scale, 10 * scale), (48 * scale, 18 * scale), (48 * scale, 54 * scale), (16 * scale, 54 * scale)],
        fill="#f3f3ee",
    )
    draw.polygon(
        [(40 * scale, 10 * scale), (40 * scale, 20 * scale), (48 * scale, 20 * scale)],
        fill="#a64328",
    )
    draw.rounded_rectangle(box((20, 25, 44, 30)), radius=2 * scale, fill="#e66d45")
    draw.rounded_rectangle(box((20, 35, 30, 47)), radius=2 * scale, fill="#20211f")
    draw.rounded_rectangle(box((34, 35, 44, 47)), radius=2 * scale, fill="#e66d45")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    draw_icon(16).save(OUTPUT_DIR / "favicon-16x16.png", format="PNG", optimize=True)
    draw_icon(32).save(OUTPUT_DIR / "favicon-32x32.png", format="PNG", optimize=True)
    draw_icon(180).save(OUTPUT_DIR / "apple-touch-icon.png", format="PNG", optimize=True)
    draw_icon(64).save(
        OUTPUT_DIR / "favicon.ico",
        format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
    )


if __name__ == "__main__":
    main()
