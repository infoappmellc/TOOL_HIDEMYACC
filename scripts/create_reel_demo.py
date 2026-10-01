from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "reel-demo"
SOURCE = OUTPUT / "source.webp"
MUSIC = ROOT / "sound" / "Excited Day.mp4"
COVER = OUTPUT / "cover.png"
VIDEO = OUTPUT / "reel-demo.mp4"

WIDTH, HEIGHT = 1080, 1920
TOP_HEIGHT = 900
DURATION = 20
FPS = 30

HOOK_YELLOW = "Horror w gabinecie weterynaryjnym w Poznaniu!"
HOOK_WHITE = (
    "Amstaff zagryzł małą suczkę szpica. Wszystko wydarzyło się na oczach "
    "przerażonej opiekunki i lekarza."
)
CTA = "Więcej szczegółów w komentarzach ↓"


def font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def cover_crop(image: Image.Image, size: tuple[int, int], focus_x: float = 0.5) -> Image.Image:
    target_w, target_h = size
    scale = max(target_w / image.width, target_h / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = round((resized.width - target_w) * focus_x)
    top = max(0, (resized.height - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def wrapped_lines(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=text_font, stroke_width=2)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    y: int,
    text_font: ImageFont.FreeTypeFont,
    fill: str,
    spacing: int = 12,
) -> int:
    for line in lines:
        box = draw.textbbox((0, 0), line, font=text_font, stroke_width=3)
        line_w = box[2] - box[0]
        line_h = box[3] - box[1]
        draw.text(
            ((WIDTH - line_w) / 2, y),
            line,
            font=text_font,
            fill=fill,
            stroke_width=3,
            stroke_fill="#111812",
        )
        y += line_h + spacing
    return y


def create_cover() -> None:
    source = Image.open(SOURCE).convert("RGB")
    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#052b23")

    background = ImageOps.fit(
        source, (WIDTH, TOP_HEIGHT), Image.Resampling.LANCZOS, centering=(0.5, 0.5)
    )
    background = ImageEnhance.Brightness(background).enhance(0.72)
    blurred = background.filter(ImageFilter.GaussianBlur(2.2))
    canvas.paste(blurred, (0, 0))

    gradient = Image.new("RGBA", (WIDTH, HEIGHT - TOP_HEIGHT), (0, 0, 0, 0))
    gradient_draw = ImageDraw.Draw(gradient)
    for row in range(HEIGHT - TOP_HEIGHT):
        ratio = row / (HEIGHT - TOP_HEIGHT)
        color = (
            round(5 + 3 * ratio),
            round(48 - 22 * ratio),
            round(38 - 18 * ratio),
            255,
        )
        gradient_draw.line((0, row, WIDTH, row), fill=color)
    canvas.paste(gradient.convert("RGB"), (0, TOP_HEIGHT))

    circle_size = 650
    subject = ImageOps.fit(
        source,
        (circle_size, circle_size),
        Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    mask = Image.new("L", (circle_size, circle_size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, circle_size - 1, circle_size - 1), fill=255)
    framed = Image.new("RGBA", (circle_size + 32, circle_size + 32), (0, 0, 0, 0))
    framed_draw = ImageDraw.Draw(framed)
    framed_draw.ellipse((0, 0, circle_size + 31, circle_size + 31), fill="#ff5b63")
    framed.paste(subject, (16, 16), mask)
    canvas.paste(framed, ((WIDTH - framed.width) // 2, 110), framed)

    draw = ImageDraw.Draw(canvas)
    body_font = font(59)
    yellow_lines = wrapped_lines(draw, HOOK_YELLOW, body_font, 920)
    white_lines = wrapped_lines(draw, HOOK_WHITE, body_font, 920)
    y = 1015
    y = draw_centered_lines(draw, yellow_lines, y, body_font, "#ffc928")
    y = draw_centered_lines(draw, white_lines, y + 4, body_font, "#ffffff")

    cta_font = font(31)
    cta_box = draw.textbbox((0, 0), CTA, font=cta_font)
    cta_w = cta_box[2] - cta_box[0]
    cta_h = cta_box[3] - cta_box[1]
    button_w = cta_w + 76
    button_h = cta_h + 42
    button_x = (WIDTH - button_w) // 2
    button_y = 1730
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.rounded_rectangle(
        (button_x - 8, button_y - 8, button_x + button_w + 8, button_y + button_h + 8),
        radius=button_h // 2,
        fill=(255, 220, 0, 135),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(18))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (button_x, button_y, button_x + button_w, button_y + button_h),
        radius=button_h // 2,
        fill="#071d18",
        outline="#ffe24a",
        width=5,
    )
    draw.text(
        ((WIDTH - cta_w) / 2, button_y + (button_h - cta_h) / 2 - 4),
        CTA,
        font=cta_font,
        fill="white",
    )

    OUTPUT.mkdir(parents=True, exist_ok=True)
    canvas.save(COVER, quality=95)


def create_video() -> None:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    frames = DURATION * FPS
    fade_out = DURATION - 1.2
    zoompan = (
        f"zoompan=z='min(zoom+0.000035,1.02)':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p"
    )
    command = [
        ffmpeg,
        "-y",
        "-loop", "1",
        "-i", str(COVER),
        "-ss", "12",
        "-i", str(MUSIC),
        "-filter_complex", f"[0:v]{zoompan}[v];[1:a]afade=t=in:st=0:d=1,afade=t=out:st={fade_out}:d=1.2,volume=0.82[a]",
        "-map", "[v]",
        "-map", "[a]",
        "-t", str(DURATION),
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        str(VIDEO),
    ]
    subprocess.run(command, check=True)


if __name__ == "__main__":
    create_cover()
    create_video()
    print(VIDEO)
