from unittest.mock import MagicMock, Mock

import pytest
from PIL import Image

from apps.dashboard import workflow


def test_article_link_is_used_only_for_image(tmp_path, monkeypatch):
    response = Mock(text='<html><article><img src="/story.jpg"></article></html>', url='https://news.example/story')
    response.raise_for_status = Mock()
    monkeypatch.setattr(workflow.httpx, 'get', Mock(return_value=response))
    image_response = Mock(headers={'content-type': 'image/jpeg'})
    image_response.iter_bytes.return_value = [b'image bytes']
    stream = MagicMock()
    stream.__enter__.return_value = image_response
    fetch_image = Mock(return_value=stream)
    monkeypatch.setattr(workflow.httpx, 'stream', fetch_image)

    article = workflow.ArticleExtractor().extract('https://news.example/story', tmp_path)

    assert article == {'image_path': str(tmp_path / 'source-image.jpg'),
                       'image_url': 'https://news.example/story.jpg'}
    assert (tmp_path / 'source-image.jpg').read_bytes() == b'image bytes'
    assert fetch_image.call_args.args[1] == 'https://news.example/story.jpg'


def test_cover_keeps_translated_comment_prompt_at_bottom(tmp_path):
    source = tmp_path / 'source.jpg'
    output = tmp_path / 'cover.png'
    Image.new('RGB', (1080, 1080), '#446688').save(source)

    workflow.ReelGenerator()._cover(source, 'Das ist eine Nachricht', output, 'de')

    with Image.open(output) as cover:
        assert cover.size == (1080, 1920)
        yellow_pixels = sum(1 for y in range(1760, 1840) for x in range(150, 930, 4)
                            if cover.getpixel((x, y))[0] > 220
                            and cover.getpixel((x, y))[1] > 180
                            and cover.getpixel((x, y))[2] < 140)
        assert yellow_pixels > 20


def test_long_video_text_is_split_without_overflow_or_truncation():
    generator = workflow.ReelGenerator()
    content = ("To jest długa wiadomość o wydarzeniu. " * 55) + "OSTATNIE_ZDANIE"
    pages = generator._text_pages(content)

    assert len(pages) > 1
    assert all(1 <= len(page) <= 9 for page in pages)
    assert "OSTATNIE_ZDANIE" in "".join(line for page in pages for line, _ in page)
    assert generator.display_duration(content, 15) >= len(pages) * 8


def test_long_video_uses_all_text_slides(tmp_path, monkeypatch):
    content = ("To jest długa wiadomość o wydarzeniu. " * 55) + "OSTATNIE_ZDANIE"
    generator = workflow.ReelGenerator()
    pages = generator._text_pages(content)
    sound_dir = tmp_path / "sound"
    sound_dir.mkdir()
    (sound_dir / "music.mp3").touch()
    monkeypatch.setattr(workflow, "SOUND_DIR", sound_dir)
    monkeypatch.setattr(workflow, "OUTPUT_DIR", tmp_path / "output")
    covers = []
    monkeypatch.setattr(generator, "_cover", lambda *args: covers.append(args))
    monkeypatch.setattr(workflow.imageio_ffmpeg, "get_ffmpeg_exe", Mock(return_value="ffmpeg"))
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(workflow.subprocess, "run", run)

    generator.render(tmp_path / "image.jpg", content, 15)

    assert len(covers) == len(pages)
    assert all(covers[index][4] == pages[index] for index in range(len(pages)))
    command = run.call_args.args[0]
    assert "-framerate" in command
    assert "cover_%03d.png" in command[command.index("-i") + 1]
    assert int(command[command.index("-t") + 1]) == generator.display_duration(content, 15)


def test_reel_uses_random_sound_file(tmp_path, monkeypatch):
    sound_dir = tmp_path / "sound"
    sound_dir.mkdir()
    first = sound_dir / "first.mp4"
    second = sound_dir / "second.mp3"
    first.touch()
    second.touch()
    (sound_dir / "notes.txt").touch()
    (sound_dir / "folder.mp3").mkdir()
    monkeypatch.setattr(workflow, "SOUND_DIR", sound_dir)
    monkeypatch.setattr(workflow, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(workflow.ReelGenerator, "_cover", Mock())
    monkeypatch.setattr(workflow.imageio_ffmpeg, "get_ffmpeg_exe", Mock(return_value="ffmpeg"))
    choice = Mock(return_value=second)
    monkeypatch.setattr(workflow.random, "choice", choice)
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(workflow.subprocess, "run", run)

    workflow.ReelGenerator().render(tmp_path / "image.jpg", "hook", 15)

    assert set(choice.call_args.args[0]) == {first, second}
    command = run.call_args.args[0]
    assert command[command.index("-stream_loop") + 3] == str(second)
    assert "[a]" in command


def test_reel_requires_sound_file(tmp_path, monkeypatch):
    sound_dir = tmp_path / "sound"
    sound_dir.mkdir()
    monkeypatch.setattr(workflow, "SOUND_DIR", sound_dir)
    monkeypatch.setattr(workflow, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(workflow.ReelGenerator, "_cover", Mock())

    with pytest.raises(ValueError, match="sound"):
        workflow.ReelGenerator().render(tmp_path / "image.jpg", "hook")
