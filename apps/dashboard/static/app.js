const profileList = document.querySelector('#profile-list');
const pageList = document.querySelector('#page-list');
let selectedProfile = null;

function escapeHtml(value = '') {
  const node = document.createElement('div');
  node.textContent = String(value);
  return node.innerHTML;
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('Không tải được dữ liệu');
  return response.json();
}

async function selectProfile(profile, button) {
  selectedProfile = profile;
  document.querySelectorAll('.profile-item').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  document.querySelector('#selected-profile').textContent = profile.name;
  const connectButton = document.querySelector('#connect-browser');
  connectButton.disabled = false;
  connectButton.textContent = `Mở & kết nối ${profile.name}`;
  pageList.innerHTML = '<div class="loading">Đang tải…</div>';

  try {
    const pages = await getJson(`/api/profiles/${profile.id}/pages`);
    document.querySelector('#page-count').textContent = `${pages.length} Page`;
    pageList.innerHTML = pages.length ? pages.map(page => pageCard(page)).join('') : '<div class="empty">Profile này chưa có Page.</div>';
    pageList.querySelectorAll('.prepare-form').forEach(form => form.addEventListener('submit', prepareVideo));
    pageList.querySelectorAll('[data-preview]').forEach(button => button.addEventListener('click', () => publishPage(button.dataset.preview, true, button)));
    pageList.querySelectorAll('[data-publish]').forEach(button => button.addEventListener('click', () => publishPage(button.dataset.publish, false, button)));
  } catch (error) {
    pageList.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
  }
}

function statusText(status) {
  return ({ idle: 'Chưa tạo video', preparing: 'Đang tạo video…', ready: 'Video sẵn sàng', publishing: 'Đang xử lý Facebook…', previewed: 'Đã chạy thử', published: 'Đã đăng', failed: 'Có lỗi' })[status] || status;
}

function outputUrl(path) {
  const marker = '/output/';
  const index = String(path || '').lastIndexOf(marker);
  return index >= 0 ? String(path).slice(index) : '';
}

function pageCard(page) {
  const videoUrl = outputUrl(page.video_path);
  return `<article class="page-card">
    <div class="page-info"><div><a href="${escapeHtml(page.url)}" target="_blank" rel="noopener"><b>${escapeHtml(page.name)}</b></a><code>${escapeHtml(page.facebook_id)}</code></div><span class="workflow-status status-${escapeHtml(page.workflow_status)}">${escapeHtml(statusText(page.workflow_status))}</span></div>
    <form class="prepare-form" data-page-id="${page.id}">
      <label>Link bài viết<input type="url" name="source_url" required value="${escapeHtml(page.source_url || '')}" placeholder="https://bestbabies.info/..."></label>
      <label class="duration">Thời lượng<select name="duration"><option value="15">15 giây</option><option value="20" selected>20 giây</option><option value="30">30 giây</option></select></label>
      <button class="primary" type="submit">Tạo video</button>
    </form>
    ${page.last_error ? `<div class="page-error">${escapeHtml(page.last_error)}</div>` : ''}
    <div class="page-actions">
      ${videoUrl ? `<a class="secondary" href="${escapeHtml(videoUrl)}" target="_blank">Xem video</a>` : ''}
      <button class="secondary" data-preview="${page.id}" ${videoUrl ? '' : 'disabled'}>Chạy thử</button>
      <button class="danger" data-publish="${page.id}" ${videoUrl ? '' : 'disabled'}>Đăng Reel + comment</button>
      ${page.post_url ? `<a class="post-link" href="${escapeHtml(page.post_url)}" target="_blank">Xem bài đã đăng ↗</a>` : ''}
    </div>
  </article>`;
}

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json' } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || 'Thao tác thất bại');
  return body;
}

async function prepareVideo(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const data = Object.fromEntries(new FormData(form));
  button.disabled = true; button.textContent = 'Đang tạo…';
  try {
    await request(`/api/pages/${form.dataset.pageId}/prepare`, { method: 'POST', body: JSON.stringify({ source_url: data.source_url, duration: Number(data.duration) }) });
    const active = document.querySelector('.profile-item.active');
    active?.click();
  } catch (error) { alert(error.message); button.disabled = false; button.textContent = 'Tạo video'; }
}

async function publishPage(pageId, dryRun, button) {
  if (!dryRun && !confirm('Đăng Reel thật lên Page và bình luận link nguồn?')) return;
  button.disabled = true; const old = button.textContent; button.textContent = dryRun ? 'Đang mở bản thử…' : 'Đang đăng…';
  try {
    await request(`/api/pages/${pageId}/publish`, { method: 'POST', body: JSON.stringify({ dry_run: dryRun }) });
    alert(dryRun ? 'Đã mở composer và dừng trước nút Đăng.' : 'Đã đăng Reel và bình luận link nguồn.');
    document.querySelector('.profile-item.active')?.click();
  } catch (error) { alert(error.message); button.disabled = false; button.textContent = old; }
}

async function loadBrowserStatus() {
  try {
    const status = await getJson('/api/browser/status');
    const label = document.querySelector('#browser-status');
    label.textContent = status.connected
      ? `Đã kết nối ${status.profile_name || 'browser'}`
      : status.browser_running
        ? 'Browser đang mở · chưa kết nối'
        : 'Chưa mở profile Hidemyacc';
    label.classList.toggle('connected', status.connected);
  } catch (_) {}
}

document.querySelector('#connect-browser').addEventListener('click', async event => {
  if (!selectedProfile) return;
  const button = event.currentTarget; button.disabled = true; button.textContent = 'Đang kết nối…';
  try { await request(`/api/profiles/${selectedProfile.id}/connect`, { method: 'POST' }); await loadBrowserStatus(); }
  catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = `Mở & kết nối ${selectedProfile.name}`; }
});

async function loadProfiles() {
  try {
    const profiles = await getJson('/api/profiles');
    document.querySelector('#profile-count').textContent = profiles.length;
    profileList.innerHTML = '';
    profiles.forEach((profile, index) => {
      const button = document.createElement('button');
      button.className = 'profile-item';
      button.innerHTML = `<span class="avatar">${escapeHtml(profile.name.replace('FB_', ''))}</span><span><b>${escapeHtml(profile.name)}</b><small>${profile.page_count || 0} Page</small></span><span class="chevron">›</span>`;
      button.addEventListener('click', () => selectProfile(profile, button));
      profileList.appendChild(button);
      if (index === 0) selectProfile(profile, button);
    });
  } catch (error) {
    profileList.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
  }
}

loadProfiles();
loadBrowserStatus();
