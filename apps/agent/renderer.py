from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageOps

from .config import Config


FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class VideoRenderer:
    def __init__(self, config: Config) -> None:
        self.config = config
        config.output_dir.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return self._ffmpeg_executable() is not None

    def render(self, job: Dict[str, Any]) -> Path:
        ffmpeg_executable = self._ffmpeg_executable()
        if not ffmpeg_executable:
            raise RuntimeError(
                "Không tìm thấy FFmpeg. Hãy cài requirements-agent hoặc thêm FFmpeg vào PATH."
            )

        job_dir = self.config.output_dir / job["id"]
        job_dir.mkdir(parents=True, exist_ok=True)
        cover = job_dir / "cover.png"
        output = job_dir / "video.mp4"
        image_source = self._resolve_source(job.get("source_image", ""), job_dir, "image")
        music_source = self._resolve_source(job.get("music_source", ""), job_dir, "music")
        self._make_cover(job, image_source, cover)

        command = [
            ffmpeg_executable,
            "-y",
            "-loop",
            "1",
            "-i",
            str(cover),
        ]
        if music_source:
            command += ["-stream_loop", "-1", "-i", str(music_source)]
        command += [
            "-t",
            str(job["duration_seconds"]),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
        ]
        if music_source:
            command += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
        command.append(str(output))

        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            detail = result.stderr.strip().splitlines()[-8:]
            raise RuntimeError("FFmpeg thất bại: " + " | ".join(detail))
        if not output.exists() or output.stat().st_size < 1024:
            raise RuntimeError("FFmpeg không tạo được file video hợp lệ")
        return output

    def _ffmpeg_executable(self) -> Optional[str]:
        system_binary = shutil.which(self.config.ffmpeg_bin)
        if system_binary:
            return system_binary
        try:
            import imageio_ffmpeg

            return imageio_ffmpeg.get_ffmpeg_exe()
        except (ImportError, RuntimeError, OSError):
            return None

    def _resolve_source(self, source: str, directory: Path, prefix: str) -> Optional[Path]:
        source = (source or "").strip()
        if not source:
            return None
        parsed = urlparse(source)
        if parsed.scheme in {"http", "https"}:
            extension = Path(parsed.path).suffix[:10] or (".jpg" if prefix == "image" else ".mp3")
            destination = directory / f"{prefix}{extension}"
            with httpx.stream("GET", source, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("content-length", 0))
                if content_length > 200 * 1024 * 1024:
                    raise ValueError("File nguồn vượt quá giới hạn 200 MB")
                total = 0
                with destination.open("wb") as file_handle:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > 200 * 1024 * 1024:
                            raise ValueError("File nguồn vượt quá giới hạn 200 MB")
                        file_handle.write(chunk)
            return destination
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file local: {path}")
        return path

    def _make_cover(self, job: Dict[str, Any], source: Optional[Path], destination: Path) -> None:
        width, height = int(job["width"]), int(job["height"])
        if source:
            try:
                with Image.open(source) as raw:
                    image = ImageOps.fit(raw.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
            except Exception as exc:
                raise ValueError(f"Không đọc được ảnh nguồn: {exc}") from exc
            image = ImageEnhance.Brightness(image).enhance(0.68)
        else:
            image = Image.new("RGB", (width, height), job.get("background_color", "#101828"))

        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        for y in range(height):
            alpha = max(0, int(155 * (y / height) - 20))
            draw_overlay.line((0, y, width, y), fill=(8, 8, 14, alpha))
        image = Image.alpha_composite(image.convert("RGBA"), overlay)

        title = (job.get("title") or "").strip()
        if title:
            draw = ImageDraw.Draw(image)
            font = self._font(max(32, int(width * 0.065)))
            small_font = self._font(max(18, int(width * 0.025)))
            max_chars = max(12, int(width / (font.size * 0.56)))
            lines = textwrap.wrap(title, width=max_chars, break_long_words=False)[:5]
            line_height = int(font.size * 1.23)
            block_height = len(lines) * line_height
            x = int(width * 0.075)
            y = height - block_height - int(height * 0.12)
            accent = job.get("background_color", "#6254f4")
            draw.rounded_rectangle(
                (x, y - int(font.size * 0.65), x + int(width * 0.15), y - int(font.size * 0.45)),
                radius=10,
                fill=accent,
            )
            for index, line in enumerate(lines):
                draw.text((x, y + index * line_height), line, font=font, fill="white", stroke_width=1)
            draw.text((x, height - int(height * 0.055)), "HMA STUDIO", font=small_font, fill=(255, 255, 255, 170))
        image.convert("RGB").save(destination, "PNG", optimize=True)

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont:
        for candidate in FONT_CANDIDATES:
            if Path(candidate).is_file():
                return ImageFont.truetype(candidate, size=size)
        return ImageFont.load_default(size=size)
