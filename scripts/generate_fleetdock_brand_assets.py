"""Generate FleetDock IDEA raster and installer assets from reviewed brand geometry."""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
RESOURCES = ROOT / "dist-resources"
ICONS = RESOURCES / "icons"
SCALE = 4


def _font(
    size: int, bold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = [
        "segoeuib.ttf" if bold else "segoeui.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for name in names:
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def _gradient(
    size: tuple[int, int],
    start: tuple[int, int, int],
    end: tuple[int, int, int],
) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()
    denominator = max(width + height - 2, 1)
    for y in range(height):
        for x in range(width):
            ratio = (x + y) / denominator
            pixels[x, y] = tuple(
                round(start[index] * (1 - ratio) + end[index] * ratio)
                for index in range(3)
            )
    return image


def _scaled(
    points: list[tuple[float, float]], scale: float
) -> list[tuple[int, int]]:
    return [(round(x * scale), round(y * scale)) for x, y in points]


def _fleetdock_icon(size: int = 512) -> Image.Image:
    canvas_size = size * SCALE
    factor = canvas_size / 512
    background = _gradient(
        (canvas_size, canvas_size), (11, 41, 72), (7, 90, 128)
    ).convert("RGBA")
    mask = Image.new("L", (canvas_size, canvas_size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, canvas_size - 1, canvas_size - 1),
        radius=round(112 * factor),
        fill=255,
    )
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    image.paste(background, (0, 0), mask)
    draw = ImageDraw.Draw(image)

    # [UAV-IDE MODIFY] Keep packaging assets aligned with the reviewed gateway logo.
    def draw_route(
        points: list[tuple[float, float]],
        color: tuple[int, int, int, int],
        width: int,
    ) -> None:
        scaled_points = _scaled(points, factor)
        scaled_width = round(width * factor)
        draw.line(
            scaled_points,
            fill=color,
            width=scaled_width,
            joint="curve",
        )
        radius = scaled_width // 2
        for x, y in (scaled_points[0], scaled_points[-1]):
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=color,
            )

    light = (234, 248, 255, 255)
    cyan = (22, 199, 232, 255)
    green = (34, 197, 94, 255)
    dark = (6, 46, 84, 255)

    draw_route([(221, 132), (177, 132), (82, 224), (186, 328)], light, 34)
    draw_route([(221, 188), (184, 188), (149, 224), (184, 260)], light, 30)
    draw_route([(284, 130), (384, 224), (290, 330), (235, 330)], cyan, 34)
    draw_route([(211, 225), (294, 225), (244, 278), (211, 278)], cyan, 30)

    for x, y, outer_color, inner_color in [
        (82, 224, cyan, dark),
        (284, 130, green, light),
        (235, 330, light, dark),
    ]:
        outer_radius = 22 * factor
        inner_radius = 10 * factor
        draw.ellipse(
            (
                x * factor - outer_radius,
                y * factor - outer_radius,
                x * factor + outer_radius,
                y * factor + outer_radius,
            ),
            fill=outer_color,
        )
        draw.ellipse(
            (
                x * factor - inner_radius,
                y * factor - inner_radius,
                x * factor + inner_radius,
                y * factor + inner_radius,
            ),
            fill=inner_color,
        )

    return image.resize((size, size), Image.Resampling.LANCZOS)


def _installer_sidebar(icon: Image.Image) -> Image.Image:
    width, height = 164 * SCALE, 314 * SCALE
    image = _gradient(
        (width, height), (15, 23, 42), (3, 105, 161)
    ).convert("RGB")
    icon_size = 112 * SCALE
    icon_large = icon.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    image.paste(
        icon_large,
        ((width - icon_size) // 2, 70 * SCALE),
        icon_large.getchannel("A"),
    )
    draw = ImageDraw.Draw(image)
    title_font = _font(19 * SCALE, bold=True)
    subtitle_font = _font(13 * SCALE, bold=True)
    draw.text(
        (width // 2, 218 * SCALE),
        "FleetDock",
        font=title_font,
        fill=(248, 250, 252),
        anchor="mm",
    )
    draw.text(
        (width // 2, 246 * SCALE),
        "I D E A",
        font=subtitle_font,
        fill=(103, 232, 249),
        anchor="mm",
    )
    draw.line(
        (48 * SCALE, 270 * SCALE, 116 * SCALE, 270 * SCALE),
        fill=(34, 211, 238),
        width=2 * SCALE,
    )
    return image.resize((164, 314), Image.Resampling.LANCZOS)


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        ROOT / "src" / "assets" / "icon.svg", RESOURCES / "icon.svg"
    )

    icon = _fleetdock_icon()
    icon.convert("RGB").save(RESOURCES / "icon.png", format="PNG")
    icon.convert("RGB").save(ICONS / "512x512.png", format="PNG")
    icon.save(
        ICONS / "icon.ico",
        format="ICO",
        sizes=[
            (16, 16),
            (24, 24),
            (32, 32),
            (48, 48),
            (64, 64),
            (128, 128),
            (256, 256),
        ],
    )
    _installer_sidebar(icon).save(
        RESOURCES / "installerSidebar.bmp", format="BMP"
    )


if __name__ == "__main__":
    main()
