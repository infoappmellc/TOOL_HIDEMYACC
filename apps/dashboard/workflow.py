from __future__ import annotations

import html
import random
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "output" / "pages"
SOUND_DIR = ROOT / "sound"
FONT_PATHS = (
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)


class ArticleExtractor:
    def extract(self, url: str, destination: Path) -> dict[str, str]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Link bài viết không hợp lệ")
        response = httpx.get(
            url,
            follow_redirects=True,
            timeout=35,
            headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "pl,en;q=0.8"},
        )
        response.raise_for_status()
        body = response.text[: 5 * 1024 * 1024]
        image_url = self._meta(body, "og:image")
        description = self._lead(body) or self._meta(body, "og:description") or self._meta(body, "description")
        if not image_url:
            raise ValueError("Không tìm thấy ảnh đầu tiên trong bài viết")
        if not description:
            raise ValueError("Không tìm thấy đoạn mở đầu trong bài viết")
        hook = self._limit(description, 230)
        destination.mkdir(parents=True, exist_ok=True)
        image_path = destination / "source-image"
        with httpx.stream("GET", image_url, follow_redirects=True, timeout=45) as image_response:
            image_response.raise_for_status()
            content_type = image_response.headers.get("content-type", "")
            suffix = ".webp" if "webp" in content_type else ".png" if "png" in content_type else ".jpg"
            image_path = image_path.with_suffix(suffix)
            total = 0
            with image_path.open("wb") as file_handle:
                for chunk in image_response.iter_bytes():
                    total += len(chunk)
                    if total > 25 * 1024 * 1024:
                        raise ValueError("Ảnh nguồn vượt quá 25 MB")
                    file_handle.write(chunk)
        return {"hook": hook, "image_path": str(image_path), "image_url": image_url}

    @staticmethod
    def _meta(body: str, key: str) -> str:
        escaped = re.escape(key)
        patterns = (
            rf'<meta[^>]+(?:property|name)=["\']{escaped}["\'][^>]+content=["\']([^"\']+)',
            rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{escaped}["\']',
        )
        for pattern in patterns:
            match = re.search(pattern, body, re.IGNORECASE)
            if match:
                return html.unescape(match.group(1)).strip()
        return ""

    @staticmethod
    def _lead(body: str) -> str:
        match = re.search(r'<p[^>]*class=["\'][^"\']*\blead\b[^"\']*["\'][^>]*>(.*?)</p>', body, re.I | re.S)
        if not match:
            return ""
        plain = re.sub(r"<[^>]+>", " ", match.group(1))
        return re.sub(r"\s+", " ", html.unescape(plain)).strip()

    @staticmethod
    def _limit(text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= limit:
            return text
        shortened = text[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return shortened + "…"


class ReelGenerator:
    width = 1080
    height = 1920
    top_height = 900
    fps = 30

    def render(self, image_path: Path, hook: str, duration: int = 20) -> Path:
        job_dir = OUTPUT_DIR / uuid.uuid4().hex
        job_dir.mkdir(parents=True, exist_ok=True)
        cover = job_dir / "cover.png"
        video = job_dir / "reel.mp4"
        self._cover(image_path, hook, cover)
        sounds = [path for path in SOUND_DIR.iterdir() if path.suffix.lower() in {".mp3", ".m4a", ".mp4", ".wav", ".aac"}]
        music = random.choice(sounds) if sounds else None
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frames = duration * self.fps
        filters = (
            f"zoompan=z='min(zoom+0.000035,1.02)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={self.width}x{self.height}:fps={self.fps},format=yuv420p"
        )
        command = [ffmpeg, "-y", "-loop", "1", "-i", str(cover)]
        if music:
            command += ["-stream_loop", "-1", "-i", str(music)]
            command += [
                "-filter_complex",
                f"[0:v]{filters}[v];[1:a]afade=t=in:st=0:d=1,afade=t=out:st={duration - 1.2}:d=1.2,volume=.82[a]",
                "-map", "[v]", "-map", "[a]",
            ]
        else:
            command += ["-vf", filters]
        command += [
            "-t", str(duration), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ]
        if music:
            command += ["-c:a", "aac", "-b:a", "160k"]
        command.append(str(video))
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError("Không tạo được video: " + " | ".join(result.stderr.splitlines()[-5:]))
        return video

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        for path in FONT_PATHS:
            if path.exists():
                return ImageFont.truetype(str(path), size=size)
        return ImageFont.load_default()

    def _wrap(self, draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
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

    def _cover(self, image_path: Path, hook: str, destination: Path) -> None:
        source = Image.open(image_path).convert("RGB")
        canvas = Image.new("RGB", (self.width, self.height), "#052b23")
        background = ImageOps.fit(source, (self.width, self.top_height), Image.Resampling.LANCZOS)
        background = ImageEnhance.Brightness(background).enhance(.72).filter(ImageFilter.GaussianBlur(2.2))
        canvas.paste(background, (0, 0))
        lower = Image.new("RGB", (self.width, self.height - self.top_height), "#07352b")
        lower_draw = ImageDraw.Draw(lower)
        for y in range(lower.height):
            ratio = y / lower.height
            lower_draw.line((0, y, self.width, y), fill=(5, round(53 - 28 * ratio), round(43 - 22 * ratio)))
        canvas.paste(lower, (0, self.top_height))

        circle_size = 650
        subject = ImageOps.fit(source, (circle_size, circle_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", (circle_size, circle_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, circle_size - 1, circle_size - 1), fill=255)
        framed = Image.new("RGBA", (circle_size + 32, circle_size + 32), (0, 0, 0, 0))
        ImageDraw.Draw(framed).ellipse((0, 0, circle_size + 31, circle_size + 31), fill="#ff5b63")
        framed.paste(subject, (16, 16), mask)
        canvas.paste(framed, ((self.width - framed.width) // 2, 110), framed)

        draw = ImageDraw.Draw(canvas)
        text_font = self._font(54 if len(hook) < 190 else 48)
        sentences = re.split(r"(?<=[.!?])\s+", hook, maxsplit=1)
        yellow = sentences[0]
        white = sentences[1] if len(sentences) > 1 else ""
        y = 1010
        for text, color in ((yellow, "#ffc928"), (white, "white")):
            for line in self._wrap(draw, text, text_font, 920):
                box = draw.textbbox((0, 0), line, font=text_font, stroke_width=3)
                line_width = box[2] - box[0]
                draw.text(((self.width - line_width) / 2, y), line, font=text_font, fill=color, stroke_width=3, stroke_fill="#111812")
                y += int(text_font.size * 1.18)
            y += 8

        cta = "Więcej szczegółów w komentarzach ↓"
        cta_font = self._font(31)
        box = draw.textbbox((0, 0), cta, font=cta_font)
        cta_width, cta_height = box[2] - box[0], box[3] - box[1]
        button_width, button_height = cta_width + 76, cta_height + 42
        x, y = (self.width - button_width) // 2, 1730
        draw.rounded_rectangle((x, y, x + button_width, y + button_height), radius=button_height // 2, fill="#071d18", outline="#ffe24a", width=5)
        draw.text(((self.width - cta_width) / 2, y + (button_height - cta_height) / 2 - 4), cta, font=cta_font, fill="white")
        canvas.save(destination, "PNG")
