from __future__ import annotations

import html
import math
import random
import re
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .language import video_cta

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "output" / "pages"
SOUND_DIR = ROOT / "sound"
FONT_PATHS = (
    Path("/System/Library/Fonts/Supplemental/Arial Black.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
EMOJI_FONT_PATH = Path("/System/Library/Fonts/Apple Color Emoji.ttc")


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
        if not image_url:
            article = re.search(r"<article\b.*?</article>", body, re.I | re.S)
            image_html = article.group(0) if article else body
            image = re.search(r'<img\b[^>]*\b(?:src|data-src)\s*=\s*["\']([^"\']+)', image_html, re.I)
            image_url = html.unescape(image.group(1)) if image else ""
        if not image_url:
            raise ValueError("Không tìm thấy ảnh trong link bài viết")
        image_url = urljoin(str(response.url) if getattr(response, "url", None) else url, image_url)
        if urlparse(image_url).scheme not in {"http", "https"}:
            raise ValueError("Đường dẫn ảnh trong bài viết không hợp lệ")
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
        return {"image_path": str(image_path), "image_url": image_url}

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

class ReelGenerator:
    width = 1080
    height = 1920
    top_height = 900
    fps = 30

    def render(self, image_path: Path, hook: str, duration: int = 20, language: str = "") -> Path:
        job_dir = OUTPUT_DIR / uuid.uuid4().hex
        job_dir.mkdir(parents=True, exist_ok=True)
        video = job_dir / "reel.mp4"
        pages = self._text_pages(hook)
        duration = max(duration, len(pages) * 8)
        if len(pages) == 1:
            cover = job_dir / "cover.png"
            self._cover(image_path, hook, cover, language, pages[0])
        else:
            for index, lines in enumerate(pages):
                self._cover(image_path, hook, job_dir / f"cover_{index:03d}.png", language, lines)
        sounds = [
            path for path in SOUND_DIR.iterdir()
            if path.is_file() and path.suffix.lower() in {".mp3", ".m4a", ".mp4", ".wav", ".aac"}
        ] if SOUND_DIR.is_dir() else []
        if not sounds:
            raise ValueError("Thư mục sound không có tệp âm thanh để tạo Reel")
        music = random.choice(sounds)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        frames = math.ceil(duration * self.fps / len(pages))
        filters = (
            f"zoompan=z='min(zoom+0.000035,1.02)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={self.width}x{self.height}:fps={self.fps},format=yuv420p"
        )
        if len(pages) == 1:
            command = [ffmpeg, "-y", "-loop", "1", "-i", str(cover)]
        else:
            command = [ffmpeg, "-y", "-framerate", "1", "-start_number", "0",
                       "-i", str(job_dir / "cover_%03d.png")]
        command += ["-stream_loop", "-1", "-i", str(music)]
        command += [
            "-filter_complex",
            f"[0:v]{filters}[v];[1:a]afade=t=in:st=0:d=1,afade=t=out:st={duration - 1.2}:d=1.2,volume=.82[a]",
            "-map", "[v]", "-map", "[a]",
        ]
        command += [
            "-t", str(duration), "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ]
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
        for paragraph in text.splitlines():
            if not paragraph.strip():
                lines.append("")
                continue
            current = ""
            for word in paragraph.split():
                candidate = f"{current} {word}".strip()
                if draw.textbbox((0, 0), candidate, font=text_font, stroke_width=2)[2] <= max_width:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                    current = ""
                while word and draw.textbbox((0, 0), word, font=text_font, stroke_width=2)[2] > max_width:
                    cut = len(word)
                    while cut > 1 and draw.textbbox((0, 0), word[:cut], font=text_font, stroke_width=2)[2] > max_width:
                        cut //= 2
                    while cut < len(word) and draw.textbbox((0, 0), word[:cut + 1], font=text_font, stroke_width=2)[2] <= max_width:
                        cut += 1
                    lines.append(word[:cut])
                    word = word[cut:]
                current = word
            if current:
                lines.append(current)
        while lines and not lines[-1]:
            lines.pop()
        return lines

    def _text_pages(self, hook: str) -> list[list[tuple[str, str]]]:
        font = self._font(54 if len(hook) < 190 else 48)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        sentences = re.split(r"(?<=[.!?])\s+", hook, maxsplit=1)
        colored: list[tuple[str, str]] = []
        for index, part in enumerate(sentences):
            color = "#ffc928" if index == 0 else "white"
            if index and colored:
                colored.append(("", color))
            colored.extend((line, color) for line in self._wrap(draw, part, font, 920))
        while colored and not colored[-1][0]:
            colored.pop()
        return [colored[index:index + 9] for index in range(0, len(colored), 9)] or [[("", "white")]]

    def display_duration(self, hook: str, base_duration: int) -> int:
        return max(base_duration, len(self._text_pages(hook)) * 8)

    def _cover(self, image_path: Path, hook: str, destination: Path, language: str = "",
               text_lines: list[tuple[str, str]] | None = None) -> None:
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
        y = 1010
        for line, color in (text_lines if text_lines is not None else self._text_pages(hook)[0]):
            if line:
                box = draw.textbbox((0, 0), line, font=text_font, stroke_width=3)
                line_width = box[2] - box[0]
                draw.text(((self.width - line_width) / 2, y), line, font=text_font, fill=color,
                          stroke_width=3, stroke_fill="#111812")
            y += int(text_font.size * 1.18)

        label = video_cta(hook, language).removesuffix(" 👇")
        emoji_size, gap, padding = 32, 12, 30
        for size in range(34, 21, -1):
            cta_font = self._font(size)
            box = draw.textbbox((0, 0), label, font=cta_font)
            cta_width = box[2] - box[0]
            if cta_width + emoji_size + gap + padding * 2 <= 970:
                break
        cta_height = box[3] - box[1]
        button_width = cta_width + emoji_size + gap + padding * 2
        button_height = max(cta_height, emoji_size) + 32
        button_x = (self.width - button_width) // 2
        button_y = 1760
        draw.rounded_rectangle(
            (button_x, button_y, button_x + button_width, button_y + button_height),
            radius=button_height // 2, fill="#071d18", outline="#ffe24a", width=5,
        )
        draw.text((button_x + padding, button_y + (button_height - cta_height) // 2 - box[1]),
                  label, font=cta_font, fill="white")
        emoji_x = button_x + padding + cta_width + gap
        emoji_y = button_y + (button_height - emoji_size) // 2
        if EMOJI_FONT_PATH.exists():
            emoji_font = ImageFont.truetype(str(EMOJI_FONT_PATH), size=emoji_size)
            draw.text((emoji_x, emoji_y), "👇", font=emoji_font, embedded_color=True)
        else:
            draw.text((emoji_x, emoji_y), "↓", font=self._font(emoji_size), fill="white")
        canvas.save(destination, "PNG")
