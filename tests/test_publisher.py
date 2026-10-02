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
            result = await publisher._upload_reel(page, '123', 'Demo Page', video, True, 'Article headline')
            assert page.url == 'https://www.facebook.com/profile.php?id=123'
            assert await page.locator('#image').evaluate('(el) => el.files.length') == 0
            assert await page.locator('#video').evaluate('(el) => el.files[0].name') == 'reel.mp4'
            assert await page.evaluate('window.posted || false') is False
            assert await page.evaluate('window.wrongNext || false') is False
            if await page.locator('#description').count():
                assert await page.locator('#description').input_value() == 'Article headline'
            if await page.locator('#description-editor').count():
                assert await page.locator('#description-editor').inner_text() == 'Article headline'
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
            <textarea id="description" placeholder="Mô tả thước phim của bạn..."></textarea>
            <button aria-label="Chia sẻ" disabled></button>
        </div>
        <input id="injected" type="file" accept="video/*">
    '''))


def test_reel_description_uses_facebook_aria_placeholder_editor(tmp_path):
    asyncio.run(exercise_upload(tmp_path, '''
        <p>Demo Page</p>
        <button onclick="document.querySelector('#uploader').hidden=false">Thước phim</button>
        <div id="uploader" role="dialog" hidden>
            <input id="video" type="file" accept="video/*" onchange="document.querySelector('#post').hidden=false">
            <button id="post" hidden onclick="window.posted=true">Đăng</button>
            <div role="form" aria-label="Thước phim">
                <div id="description-editor" contenteditable="true" role="textbox"
                     aria-placeholder="Mô tả thước phim của bạn..."><p><br></p></div>
            </div>
        </div>
        <input id="image" type="file" accept="image/*">
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


@pytest.mark.parametrize('raw, expected', [
    ('Demo Page, 5 notifications', 'Demo Page'),
    ('Trang Tin\n3 thông báo', 'Trang Tin'),
    ('HjemmeVen, đang chọn', 'HjemmeVen'),
    ('See all profiles', ''),
    ('Đăng xuất', ''),
    ('', ''),
])
def test_profile_switcher_name_cleanup(raw, expected):
    assert FacebookPublisher._clean_profile_name(raw) == expected


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
            <textarea id="description" placeholder="Mô tả thước phim của bạn..."></textarea>
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
                <textarea id="description" placeholder="Mô tả thước phim của bạn..."></textarea>
            </div>
        ''', actor_after_upload="999"))


@pytest.mark.parametrize('posts, previous, expected', [
    ([{"key": "new", "minutes": 0}], set(), "new"),
    ([{"key": "new", "minutes": 3}], set(), "new"),
    ([{"key": "old", "minutes": 0}, {"key": "new", "minutes": 1}], {"old"}, "new"),
    ([{"key": "old", "minutes": 31}], set(), "old"),
    ([{"key": "unknown", "minutes": None}], set(), "unknown"),
    ([{"key": "old", "minutes": 2}], {"old"}, None),
])
def test_recent_post_selection(posts, previous, expected):
    selected = FacebookPublisher._select_recent_post(
        [{**post, "text": "Article headline"} for post in posts], previous, "Article headline"
    )
    assert (selected["key"] if selected else None) == expected


def test_post_selection_stops_at_first_matching_title_in_feed_order():
    selected = FacebookPublisher._select_recent_post([
        {"key": "one", "minutes": None, "text": "Article headline"},
        {"key": "two", "minutes": 0, "text": "Article headline"},
    ], set(), "Article headline")
    assert selected["key"] == "one"


def test_recent_post_selection_requires_matching_title():
    assert FacebookPublisher._select_recent_post([
        {"key": "new", "minutes": 0, "text": "Unrelated video"},
    ], set(), "Article headline") is None


def test_recent_post_selection_accepts_collapsed_long_title():
    title = "A detailed article headline that continues far beyond the visible caption in the feed"
    selected = FacebookPublisher._select_recent_post([
        {"key": "new", "minutes": 0, "text": title[:60] + "… See more"},
    ], set(), title)
    assert selected["key"] == "new"


def test_feed_finds_fresh_reel_without_permalink():
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
                await page.set_content('''<style>video{width:200px;height:200px}</style>
                    <div id="recent"><a href="https://www.facebook.com/profile.php?id=123">F1 Deutschland</a>
                        <span>Người đăng: Một người khác</span><span>Vừa xong</span>
                        <p>Das ist in der deutschen Nationalmannschaft absolut nicht akzeptabel</p>
                        <video poster="https://example.com/poster.jpg"></video>
                        <div role="button" aria-label="Viết bình luận">Bình luận</div>
                    </div>''')
                posts = await FacebookPublisher(BrowserBridge())._feed_posts(page, '123')
                assert len(posts) == 1
                assert posts[0]['minutes'] == 0
                assert posts[0]['url'] == ''
                assert FacebookPublisher._select_recent_post(
                    posts, set(), 'Das ist in der deutschen Nationalmannschaft absolut nicht akzeptabel'
                )['index'] == 0
            finally:
                await browser.close()
    asyncio.run(run())


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
                html = '''<meta charset="utf-8"><style>a,span,button{display:block}</style>
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
                        <p>Article headline</p>
                        <button aria-label="Viết bình luận" onclick="document.querySelector('#editor').hidden=false">Comment</button>
                        <div id="editor" hidden contenteditable="true" role="textbox" onkeydown="if(event.key==='Enter'){event.preventDefault();window.commentText=this.innerText;const a=document.createElement('a');a.href='https://l.facebook.com/l.php?u='+encodeURIComponent(this.innerText.split(/\\s+/).at(-1)+'?fbclid=tracking');a.textContent='Xem bài báo';this.parentElement.append(a);this.innerText='';}"></div>
                    </div>
                '''
                await page.context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
                publisher = FacebookPublisher(BrowserBridge())
                url = await publisher._comment_on_recent_post(
                    page, '123', 'https://example.com/news', set(), 'Article headline'
                )
                assert url == 'https://www.facebook.com/reel/444/'
                assert await page.evaluate('window.commentText') == 'Full article: https://example.com/news'
                assert await page.locator('#recent a[href^="https://l.facebook.com/l.php"]').count() == 1
                assert await page.locator('#old [contenteditable]').inner_text() == ''
                assert await page.locator('#wrong [contenteditable]').inner_text() == ''
            finally:
                await browser.close()
    asyncio.run(run())


@pytest.mark.parametrize('time_label, expected_minutes', [('Vừa xong', 0), ('', None)])
def test_feed_recognizes_title_before_comment_button_appears(time_label, expected_minutes):
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
                await page.set_content(f'''<meta charset="utf-8">
                    <div id="new"><a href="https://www.facebook.com/profile.php?id=123">Demo Page</a>
                        <span>{time_label}</span><p>Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny</p>
                        <video></video></div>
                    <div id="other"><a href="https://www.facebook.com/profile.php?id=999">Other Page</a>
                        <span>Vừa xong</span><p>Other headline</p><video></video></div>''')
                publisher = FacebookPublisher(BrowserBridge())
                posts = await publisher._feed_posts(page, '123')
                selected = publisher._select_recent_post(
                    posts, set(), 'Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny'
                )
                assert len(posts) == 1
                assert selected is not None and selected['minutes'] == expected_minutes
                assert await page.locator('[data-hma-feed-post="0"]').count() == 1
            finally:
                await browser.close()
    asyncio.run(run())


def test_feed_recognizes_caption_before_video_mounts():
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
                await page.set_content('''<meta charset="utf-8">
                    <div id="new"><a href="https://www.facebook.com/profile.php?id=123">Demo Page</a>
                        <p>Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny</p>
                        <button aria-label="Viết bình luận">Comment</button></div>
                    <div id="other"><a href="https://www.facebook.com/profile.php?id=999">Other Page</a>
                        <p>Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny</p>
                        <video></video></div>''')
                title = 'Tragedia nad Bałtykiem. Z morza wyłowiono ciało mężczyzny'
                publisher = FacebookPublisher(BrowserBridge())
                posts = await publisher._feed_posts(page, '123', title)
                selected = publisher._select_recent_post(posts, set(), title)
                assert len(posts) == 1
                assert selected is not None and selected['minutes'] is None
                assert await page.locator('#new[data-hma-feed-post="0"]').count() == 1
            finally:
                await browser.close()
    asyncio.run(run())


def test_existing_facebook_redirect_comment_is_not_posted_twice():
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
                await page.set_content('''<div id="post">
                    <a href="https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2Fnews%3Ffbclid%3Dtracking">Xem bài báo</a>
                    <div contenteditable="true" role="textbox"></div>
                </div>''')
                publisher = FacebookPublisher(BrowserBridge())
                post = page.locator('#post')
                assert await post.evaluate(publisher._COMMENT_LINK_CHECK, 'https://example.com/news')
                await publisher._comment_in_post(page, post, '123', 'https://example.com/news')
                assert await post.locator('a').count() == 1
                assert await post.locator('[contenteditable]').inner_text() == ''
            finally:
                await browser.close()
    asyncio.run(run())


def test_pending_existing_comment_waits_and_does_not_send_duplicate():
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
                await page.set_content('''<meta charset="utf-8"><div id="post">
                    <p>Cały artykuł: https://example.com/news</p><small id="pending">Đang viết...</small>
                    <div contenteditable="true" role="textbox" onkeydown="window.sent=true"></div>
                </div>''')

                async def finish_pending(_milliseconds):
                    await page.locator('#pending').evaluate('(element) => element.remove()')

                page.wait_for_timeout = finish_pending
                publisher = FacebookPublisher(BrowserBridge())
                await publisher._comment_in_post(page, page.locator('#post'), '123',
                                                 'https://example.com/news')
                assert await page.evaluate('window.sent || false') is False
                assert await page.locator('#post p').count() == 1
            finally:
                await browser.close()
    asyncio.run(run())


def test_new_comment_waits_for_facebook_pending_label_to_clear():
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
                page.context.cookies = AsyncMock(return_value=[{"name": "i_user", "value": "123"}])
                await page.set_content('''<meta charset="utf-8"><div id="post">
                    <div contenteditable="true" role="textbox" onkeydown="if(event.key==='Enter'){
                      event.preventDefault();const p=document.createElement('p');p.textContent=this.innerText;
                      this.parentElement.append(p);const s=document.createElement('small');s.id='pending';
                      s.textContent='Đang viết...';this.parentElement.append(s);this.innerText='';}"></div>
                </div>''')

                async def advance(milliseconds):
                    if milliseconds == 2000 and await page.locator('#pending').count():
                        await page.locator('#pending').evaluate('(element) => element.remove()')

                page.wait_for_timeout = advance
                events = []
                publisher = FacebookPublisher(BrowserBridge())
                await publisher._comment_in_post(
                    page, page.locator('#post'), '123', 'https://example.com/news',
                    title='Article headline', progress=lambda *event: events.append(event),
                )
                assert await page.locator('#post p').count() == 1
                assert await page.locator('#post p').inner_text() == 'Full article: https://example.com/news'
                assert any(event[0] == 'comment_wait' for event in events)
            finally:
                await browser.close()
    asyncio.run(run())


def test_comment_rechecks_reel_after_facebook_changes_route():
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
                html = '''<style>video{width:200px;height:200px}</style><div id="post">
                    <a href="https://www.facebook.com/profile.php?id=123">Demo Page</a>
                    <span>Vừa xong</span><p>Article headline</p><video></video>
                    <button aria-label="Viết bình luận" onclick="history.pushState({},'', '/reel/444/');
                      const a=document.createElement('a');a.href='https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2Fnews%3Ffbclid%3Dtracking';a.textContent='Xem bài báo';document.body.append(a)">Comment</button>
                </div>'''
                await page.context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
                publisher = FacebookPublisher(BrowserBridge())
                url = await publisher._comment_on_recent_post(
                    page, '123', 'https://example.com/news', set(), 'Article headline'
                )
                assert url == 'https://www.facebook.com/reel/444'
                assert await page.locator('#post a[href^="https://l.facebook.com/l.php"]').count() == 0
                assert await page.locator('body > a[href^="https://l.facebook.com/l.php"]').count() == 1
            finally:
                await browser.close()
    asyncio.run(run())
