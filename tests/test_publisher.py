import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from playwright.async_api import async_playwright

from apps.dashboard.publisher import BrowserBridge, FacebookPublisher


async def exercise_upload(tmp_path, html, *, switch_first=False, actor_id="123", actor_after_upload=None):
    async with async_playwright() as playwright:
        executable = Path(playwright.chromium.executable_path)
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not executable.exists():
            if not chrome.exists():
                pytest.skip('Install Playwright Chromium to run browser regression tests')
            executable = chrome
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page()
            await page.context.add_cookies([{
                "name": "i_user", "value": actor_id, "domain": ".facebook.com", "path": "/"
            }])
            await page.context.route('**/*', lambda route: route.fulfill(body='<meta charset="utf-8">' + html, content_type='text/html; charset=utf-8'))
            page.wait_for_timeout = AsyncMock()
            # Model the authenticated actor separately from the HTML fixture.
            if switch_first:
                fixture_actor = actor_id

                async def fixture_cookies(url):
                    nonlocal fixture_actor
                    selected = await page.evaluate("window.fixtureActor || null")
                    if selected:
                        fixture_actor = selected
                    return [{"name": "i_user", "value": fixture_actor}]

                page.context.cookies = fixture_cookies
            else:
                page.context.cookies = AsyncMock(return_value=[{"name": "i_user", "value": actor_id}])
                if actor_after_upload is not None:
                    page.context.cookies.side_effect = [
                        [{"name": "i_user", "value": actor_id}],
                        [{"name": "i_user", "value": actor_after_upload}],
                    ]
            video = tmp_path / 'reel.mp4'
            video.write_bytes(b'test video')
            publisher = FacebookPublisher(BrowserBridge())
            if switch_first:
                await publisher._switch_page(page, '123', 'Demo Page | York NY')
            result = await publisher._upload_reel(page, '123', 'Demo Page', video, True)
            assert page.url == 'https://www.facebook.com/profile.php?id=123'
            assert await page.locator('#image').evaluate('(el) => el.files.length') == 0
            assert await page.locator('#video').evaluate('(el) => el.files[0].name') == 'reel.mp4'
            assert await page.evaluate('window.posted || false') is False
            assert await page.evaluate('window.wrongNext || false') is False
            if await page.locator('#description').count():
                assert await page.locator('#description').input_value() == ''
            assert result[0].is_file()
            assert result[1] == ''
        finally:
            await browser.close()


def test_vietnamese_reel_upload_selects_video_and_stops_before_post(tmp_path):
    asyncio.run(exercise_upload(tmp_path, '''
        <p>Demo Page</p>
        <a href="/reels">Reels</a>
        <a href="/reel/?s=tab">Reel</a>
        <button onclick="window.wrongNext=true">Tiếp</button>
        <input id="image" type="file" accept="image/*">
        <button onclick="document.querySelector('#uploader').hidden=false">Thước phim</button>
        <div id="uploader" role="dialog" hidden>
            <input id="video" type="file" accept="video/*" onchange="document.querySelector('#post').hidden=false">
            <button id="post" hidden onclick="window.posted=true">Đăng</button>
            <textarea id="description" placeholder="Mô tả thước phim của bạn...">Tiêu đề không được đăng</textarea>
            <button aria-label="Chia sẻ" disabled></button>
        </div>
        <input id="injected" type="file" accept="video/*">
    '''))


def test_dry_run_fails_if_upload_does_not_reach_publish_step(tmp_path):
    with pytest.raises(RuntimeError, match='Không tìm thấy nút đăng Reel'):
        asyncio.run(exercise_upload(tmp_path, '''
            <p>Demo Page</p><input id="image" type="file" accept="image/*">
            <button>Thước phim</button><input id="video" type="file" accept="video/*">
        '''))


def test_reels_feed_tab_is_not_a_create_button(tmp_path):
    with pytest.raises(RuntimeError, match='Không tìm thấy nút tạo Thước phim'):
        asyncio.run(exercise_upload(tmp_path, '<p>Demo Page</p><a href="/reels">Reels</a><a href="/reel/?s=tab">Reel</a>'))


def test_reel_urls_exclude_notifications_and_feed_and_normalize_duplicates():
    assert FacebookPublisher._reel_urls([
        'https://www.facebook.com/reel/?s=tab',
        'https://www.facebook.com/reel/111/?notif_id=123',
        'https://www.facebook.com/reel/222/?ref=page',
        'https://www.facebook.com/reel/222/',
        'https://example.com/reel/333',
    ]) == {'https://www.facebook.com/reel/222'}


def test_switch_selects_page_from_menu_before_upload(tmp_path):
    asyncio.run(exercise_upload(tmp_path, '''
        <p>Demo Page</p>
        <button aria-label="Trang cá nhân của bạn" onclick="document.querySelector('#account').hidden=false">Account</button>
        <div id="account" role="dialog" hidden>
            <button onclick="document.querySelector('#profiles').hidden=false;this.parentElement.hidden=true">Xem tất cả trang cá nhân</button>
        </div>
        <div id="profiles" role="dialog" hidden>
            <button aria-label="Demo Page, 5 thông báo" onclick="window.fixtureActor='123';this.parentElement.hidden=true">Demo Page</button>
        </div>
        <input id="image" type="file" accept="image/*">
        <button onclick="document.querySelector('#uploader').hidden=false">Thước phim</button>
        <div id="uploader" role="dialog" hidden>
            <input id="video" type="file" accept="video/*" onchange="document.querySelector('#post').hidden=false">
            <button id="post" hidden onclick="window.posted=true">Đăng</button>
        </div>
    ''', switch_first=True, actor_id="999"))


def test_wrong_actor_stops_before_upload_even_if_page_name_is_visible(tmp_path):
    with pytest.raises(RuntimeError, match='chưa hoạt động dưới đúng Page ID'):
        asyncio.run(exercise_upload(tmp_path, '<p>Demo Page</p><button>Thước phim</button>', actor_id='999'))


@pytest.mark.parametrize('cookies', [[], [{"name": "c_user", "value": "123"}], [{"name": "i_user", "value": "999"}]])
def test_identity_requires_target_page_actor(cookies):
    page = Mock()
    page.context.cookies = AsyncMock(return_value=cookies)
    with pytest.raises(RuntimeError, match='chưa hoạt động dưới đúng Page ID'):
        asyncio.run(FacebookPublisher(BrowserBridge())._assert_page_identity(page, '123'))


def test_identity_change_after_upload_stops_before_post(tmp_path):
    with pytest.raises(RuntimeError, match='chưa hoạt động dưới đúng Page ID'):
        asyncio.run(exercise_upload(tmp_path, '''
            <p>Demo Page</p><input id="image" type="file" accept="image/*">
            <button onclick="document.querySelector('#uploader').hidden=false">Thước phim</button>
            <div id="uploader" role="dialog" hidden>
                <input id="video" type="file" accept="video/*" onchange="document.querySelector('#post').hidden=false">
                <button id="post" hidden onclick="window.posted=true">Đăng</button>
            </div>
        ''', actor_after_upload="999"))


@pytest.mark.parametrize('posts, previous, expected', [
    ([{"key": "new", "minutes": 0}], set(), "new"),
    ([{"key": "new", "minutes": 3}], set(), "new"),
    ([{"key": "old", "minutes": 0}, {"key": "new", "minutes": 1}], {"old"}, "new"),
    ([{"key": "old", "minutes": 6}], set(), None),
    ([{"key": "unknown", "minutes": None}], set(), None),
    ([{"key": "old", "minutes": 2}], {"old"}, None),
])
def test_recent_post_selection(posts, previous, expected):
    selected = FacebookPublisher._select_recent_post(posts, previous)
    assert (selected["key"] if selected else None) == expected


def test_recent_post_selection_rejects_ambiguous_posts():
    with pytest.raises(RuntimeError, match='Có nhiều bài mới'):
        FacebookPublisher._select_recent_post([
            {"key": "one", "minutes": 0}, {"key": "two", "minutes": 0},
        ], set())


def test_feed_comment_scopes_to_recent_page_video(tmp_path):
    async def run():
        async with async_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            if not executable.exists():
                if not chrome.exists():
                    pytest.skip('Install Playwright Chromium to run browser regression tests')
                executable = chrome
            browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
            try:
                page = await browser.new_page()
                page.wait_for_timeout = AsyncMock()
                page.context.cookies = AsyncMock(return_value=[{"name": "i_user", "value": "123"}])
                html = '''<meta charset="utf-8">
                    <div id="old"><a href="https://www.facebook.com/profile.php?id=123">Demo Page</a>
                        <span>4 giờ trước</span><video></video><button aria-label="Viết bình luận">Comment</button>
                        <div contenteditable="true" role="textbox"></div>
                    </div>
                    <div id="wrong"><a href="https://www.facebook.com/profile.php?id=999">Other Page</a>
                        <span>Vừa xong</span><video></video><button aria-label="Viết bình luận">Comment</button>
                        <div contenteditable="true" role="textbox"></div>
                    </div>
                    <div id="recent"><a href="https://www.facebook.com/profile.php?id=123">Demo Page</a>
                        <a href="https://www.facebook.com/reel/444/">2 phút trước</a><video></video>
                        <button aria-label="Viết bình luận" onclick="document.querySelector('#editor').hidden=false">Comment</button>
                        <div id="editor" hidden contenteditable="true" role="textbox" onkeydown="if(event.key==='Enter'){event.preventDefault();const a=document.createElement('a');a.href=this.innerText;a.textContent=this.innerText;this.parentElement.append(a);this.innerText='';}"></div>
                    </div>
                '''
                await page.context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
                publisher = FacebookPublisher(BrowserBridge())
                url = await publisher._comment_on_recent_post(page, '123', 'https://example.com/news', set())
                assert url == 'https://www.facebook.com/reel/444/'
                assert await page.locator('#recent a[href="https://example.com/news"]').count() == 1
                assert await page.locator('#old [contenteditable]').inner_text() == ''
                assert await page.locator('#wrong [contenteditable]').inner_text() == ''
            finally:
                await browser.close()
    asyncio.run(run())
