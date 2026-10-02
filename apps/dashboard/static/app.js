const profileList = document.querySelector('#profile-list');
const pageList = document.querySelector('#page-list');
let selectedProfile = null;
let selectedRunnableCount = 0;
let selectedPages = [];
let activeSchedule = null;
let runState = { active: false, paused: false, profile_id: null, page_id: null, events: [] };
let renderedFirstEventId = null;
let renderedLastEventId = 0;
let historyPageId = null;
let historyOffset = 0;
let bulkPreviewState = null;
const historyPageSize = 50;

function pageIsReady(page) {
  return Boolean(page && !page.needs_comment_retry && page.source_url?.trim() &&
    page.reel_description?.trim() && page.video_content?.trim());
}

const savedFields = { source_url: 'savedSourceurl', reel_description: 'savedReeldescription', video_content: 'savedVideocontent' };

function formMatchesSaved(form) {
  return Object.entries(savedFields).every(([name, saved]) =>
    form.elements[name].value.trim() === form.dataset[saved]);
}

function formIsSavedAndReady(form) {
  return formMatchesSaved(form) && Object.keys(savedFields).every(name => form.elements[name].value.trim());
}

function hasUnsavedContent() {
  return [...pageList.querySelectorAll('.source-form')].some(form => !formMatchesSaved(form));
}

function updateActionButtons() {
  const blocked = runState.active || Boolean(activeSchedule) || hasUnsavedContent();
  document.querySelector('#run-all-pages').disabled = blocked || selectedRunnableCount === 0;
  document.querySelector('#bulk-import-open').disabled = blocked || !selectedProfile || selectedPages.length === 0;
  document.querySelector('#schedule-submit').disabled = blocked || selectedRunnableCount === 0 || !selectedProfile || Boolean(activeSchedule);
  pageList.querySelectorAll('[data-run]').forEach(button => {
    const form = button.closest('.page-card').querySelector('.source-form');
    button.disabled = runState.active || Boolean(activeSchedule) || !formIsSavedAndReady(form);
  });
}

function updatePageOverview(pages) {
  document.querySelector('#ready-count').textContent = pages.filter(pageIsReady).length;
  document.querySelector('#draft-count').textContent = pages.filter(page => !pageIsReady(page) && !page.needs_comment_retry && page.workflow_status !== 'published').length;
  document.querySelector('#failed-count').textContent = pages.filter(page => page.workflow_status === 'failed' || page.needs_comment_retry).length;
}

function applyPageFilters() {
  const query = document.querySelector('#page-search').value.trim().toLocaleLowerCase();
  const filter = document.querySelector('#page-filter').value;
  let visible = 0;
  pageList.querySelectorAll('.page-card').forEach(card => {
    const page = selectedPages.find(item => String(item.id) === card.dataset.pageId);
    const matchesText = !query || `${page?.name || ''} ${page?.facebook_id || ''}`.toLocaleLowerCase().includes(query);
    const matchesStatus = filter === 'all' || (filter === 'ready' && pageIsReady(page)) ||
      (filter === 'draft' && !pageIsReady(page) && !page.needs_comment_retry && page.workflow_status !== 'published') ||
      (filter === 'failed' && (page.workflow_status === 'failed' || page.needs_comment_retry));
    card.hidden = !(matchesText && matchesStatus);
    if (!card.hidden) visible++;
  });
  const empty = document.querySelector('#filter-empty');
  if (empty) empty.hidden = visible > 0;
}

function localDateTimeValue(date) {
  const pad = value => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setupScheduleTime() {
  const input = document.querySelector('#schedule-time');
  const now = new Date();
  input.min = localDateTimeValue(new Date(now.getTime() + 60_000));
  if (!input.value || new Date(input.value) <= now) {
    input.value = localDateTimeValue(new Date(now.getTime() + 10 * 60_000));
  }
}

function scheduleStatus(schedule) {
  return ({ pending: 'Đang chờ', running: 'Đang chạy', completed: 'Đã chạy',
    failed: 'Cần kiểm tra', cancelled: 'Đã huỷ' })[schedule.status] || schedule.status;
}

async function loadSchedules(profileId = selectedProfile?.id) {
  if (!profileId) return;
  const list = document.querySelector('#schedule-list');
  try {
    const schedules = await getJson(`/api/profiles/${profileId}/pages/schedules`);
    if (selectedProfile?.id !== profileId) return;
    const active = schedules.find(schedule => ['pending', 'running'].includes(schedule.status));
    activeSchedule = active || null;
    updateActionButtons();
    const latest = active || schedules[0];
    if (!latest) {
      list.innerHTML = '<span class="schedule-empty">Chưa có lịch hẹn cho profile này.</span>';
      return;
    }
    const when = new Date(latest.scheduled_at).toLocaleString('vi-VN');
    const published = latest.results.filter(item => item.status === 'published').length;
    const result = ['completed', 'failed'].includes(latest.status)
      ? `<span> · Đã đăng ${published}/${latest.page_ids.length} Page</span>` : '';
    const details = latest.results.length ? `<details class="schedule-results"><summary>Kết quả từng Page</summary><ul>${latest.results.map(item => `<li><strong>${escapeHtml(item.name)}</strong> · ${escapeHtml(({ published: 'Đã đăng', failed: 'Có lỗi', skipped: 'Đã bỏ qua' })[item.status] || item.status)}${item.error ? ` — ${escapeHtml(item.error)}` : ''}</li>`).join('')}</ul></details>` : '';
    list.innerHTML = `<div class="schedule-item"><span class="schedule-state state-${escapeAttr(latest.status)}">${scheduleStatus(latest)}</span><strong>${escapeHtml(when)}</strong><span>${latest.page_ids.length} Page${result}</span>${latest.status === 'pending' ? `<button type="button" class="secondary" data-cancel-schedule="${latest.id}">Huỷ lịch</button>` : ''}</div>${latest.error ? `<p class="schedule-error">${escapeHtml(latest.error)}</p>` : ''}${details}`;
  } catch (error) {
    if (selectedProfile?.id === profileId) list.innerHTML = `<span class="error">${escapeHtml(error.message)}</span>`;
  }
}

function renderRunLog(events = []) {
  const panel = document.querySelector('#run-log-panel');
  const list = document.querySelector('#run-log');
  panel.hidden = events.length === 0;
  if (!events.length) {
    list.innerHTML = '';
    renderedFirstEventId = null;
    renderedLastEventId = 0;
    return;
  }
  if (renderedFirstEventId !== events[0].id) {
    list.innerHTML = '';
    renderedFirstEventId = events[0].id;
    renderedLastEventId = 0;
  }
  const stickToBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
  for (const event of events) {
    if (event.id <= renderedLastEventId) continue;
    const time = new Date(event.time).toLocaleTimeString('vi-VN');
    const detail = event.detail ? `<details><summary>Xem lỗi chi tiết</summary><pre>${escapeHtml(event.detail)}</pre></details>` : '';
    list.insertAdjacentHTML('beforeend', `<li class="${escapeAttr(event.level)}"><time>${escapeHtml(time)}</time>${event.page_id ? `<span class="page-ref">Page #${event.page_id}</span>` : ''}<span>${escapeHtml(event.message)}</span>${detail}</li>`);
    renderedLastEventId = event.id;
  }
  if (stickToBottom) list.scrollTop = list.scrollHeight;
}

function showRunState(state) {
  runState = state;
  renderRunLog(state.events || []);
  const visible = state.active;
  const label = document.querySelector('#run-status');
  label.hidden = !visible;
  label.textContent = state.paused
    ? (state.waiting ? 'Đã tạm dừng' : 'Đang chờ điểm dừng an toàn')
    : `Đang chạy${state.page_id ? ` Page #${state.page_id}` : ''}`;
  document.querySelector('#pause-run').hidden = !visible || state.paused;
  document.querySelector('#resume-run').hidden = !visible || !state.paused;
  updateActionButtons();
}

async function loadRunState() {
  try { showRunState(await getJson('/api/run/status')); } catch (_) {}
}

function escapeHtml(value = '') {
  const node = document.createElement('div');
  node.textContent = String(value);
  return node.innerHTML;
}

function escapeAttr(value = '') {
  return escapeHtml(value).replaceAll('"', '&quot;').replaceAll("'", '&#39;');
}

async function getJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error('Không tải được dữ liệu');
  return response.json();
}

async function selectProfile(profile, button) {
  selectedProfile = profile;
  activeSchedule = null;
  selectedRunnableCount = 0;
  selectedPages = [];
  document.querySelectorAll('.profile-item').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  document.querySelector('#selected-profile').textContent = profile.name;
  const connectButton = document.querySelector('#connect-browser');
  const refreshButton = document.querySelector('#refresh-pages');
  const runAllButton = document.querySelector('#run-all-pages');
  connectButton.disabled = false;
  connectButton.textContent = `Mở & kết nối ${profile.name}`;
  refreshButton.disabled = false;
  runAllButton.disabled = true;
  document.querySelector('#bulk-import-open').disabled = true;
  document.querySelector('#schedule-submit').disabled = true;
  loadBrowserStatus();
  loadSchedules(profile.id);
  pageList.innerHTML = '<div class="loading">Đang tải…</div>';

  try {
    const pages = await getJson(`/api/profiles/${profile.id}/pages`);
    if (selectedProfile?.id !== profile.id) return;
    selectedPages = pages;
    selectedRunnableCount = pages.filter(pageIsReady).length;
    runAllButton.textContent = `Đăng tất cả (${selectedRunnableCount})`;
    document.querySelector('#page-count').textContent = `${pages.length} Page`;
    updatePageOverview(pages);
    pageList.innerHTML = pages.length ? pages.map(page => pageCard(page)).join('') + '<div id="filter-empty" class="empty" hidden>Không có Page khớp bộ lọc.</div>' : '<div class="empty">Profile này chưa có Page. Bấm “Quét lại Page” để đồng bộ.</div>';
    pageList.querySelectorAll('.source-form').forEach(form => {
      form.addEventListener('submit', saveSource);
      form.addEventListener('input', updateActionButtons);
    });
    pageList.querySelectorAll('[data-run]').forEach(button => button.addEventListener('click', () => runPage(button.dataset.run, button)));
    pageList.querySelectorAll('[data-history]').forEach(button => button.addEventListener('click', () => openHistory(button.dataset.history, button.dataset.pageName)));
    showRunState(runState);
    applyPageFilters();
  } catch (error) {
    pageList.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
  }
}

function statusText(page) {
  if (page.needs_comment_retry) return 'Cần xử lý comment';
  if (page.workflow_status === 'idle') return pageIsReady(page) ? 'Sẵn sàng' : 'Thiếu thông tin';
  return ({ preparing: 'Đang tạo video…', ready: 'Video sẵn sàng', publishing: 'Đang đăng Facebook…', previewed: 'Đã chạy thử', published: 'Đã đăng', failed: 'Có lỗi' })[page.workflow_status] || page.workflow_status;
}

function outputUrl(path) {
  const marker = '/output/';
  const index = String(path || '').lastIndexOf(marker);
  return index >= 0 ? String(path).slice(index) : '';
}

function pageCard(page) {
  const videoUrl = outputUrl(page.video_path);
  return `<article class="page-card" data-page-id="${page.id}">
    <div class="page-info"><div><a href="${escapeAttr(page.url)}" target="_blank" rel="noopener"><b>${escapeHtml(page.name)}</b></a><code>${escapeHtml(page.facebook_id)}</code></div><span class="workflow-status status-${escapeAttr(page.workflow_status)}">${escapeHtml(statusText(page))}</span></div>
    ${page.workflow_status === 'published' && !pageIsReady(page) ? '<div class="article-title">Đã đăng xong, ô nhập đã dọn. Có thể xem lại nội dung trong lịch sử.</div>' : ''}
    ${page.needs_comment_retry ? '<div class="page-error">Bài đã được gửi. Hãy mở lịch sử và dùng “Chạy lại comment”; không đăng Reel mới.</div>' : page.last_error ? `<div class="page-error">${escapeHtml(page.last_error)}</div>` : ''}
    <div class="page-actions">
      <button class="primary" type="button" data-run="${page.id}" ${pageIsReady(page) ? '' : 'disabled'}>Đăng Page này</button>
      <button class="secondary" type="button" data-history="${page.id}" data-page-name="${escapeAttr(page.name)}">Lịch sử</button>
      ${videoUrl ? `<a class="secondary" href="${escapeAttr(videoUrl)}" target="_blank">Xem video</a>` : ''}
      ${page.post_url ? `<a class="post-link" href="${escapeAttr(page.post_url)}" target="_blank" rel="noopener">Bài đã đăng ↗</a>` : ''}
    </div>
    <details class="page-editor" ${!pageIsReady(page) && page.workflow_status !== 'published' ? 'open' : ''}><summary>${pageIsReady(page) ? 'Xem / sửa nội dung đã lưu' : 'Nhập nội dung để đăng'}</summary>
    <form class="source-form" data-page-id="${page.id}" data-saved-sourceurl="${escapeAttr(page.source_url || '')}" data-saved-reeldescription="${escapeAttr(page.reel_description || '')}" data-saved-videocontent="${escapeAttr(page.video_content || '')}">
      <label>Link bài viết (chỉ lấy ảnh)<input type="url" name="source_url" required value="${escapeAttr(page.source_url || '')}" placeholder="https://example.com/bai-viet"></label>
      <label>Mô tả thước phim của bạn<textarea name="reel_description" required maxlength="2000" rows="3" placeholder="Nội dung đăng kèm Reel">${escapeHtml(page.reel_description || '')}</textarea></label>
      <label>Nội dung video<textarea name="video_content" required rows="6" placeholder="Nhập nội dung dài tuỳ ý; video sẽ tự chia thành nhiều màn hình nếu cần">${escapeHtml(page.video_content || '')}</textarea><small class="field-help">Nếu chữ vượt khung, video tự chia màn hình và kéo dài đủ để hiển thị toàn bộ.</small></label>
      <button class="primary" type="submit">Lưu nội dung</button>
    </form>
    </details>
  </article>`;
}

function historyStatus(kind, value) {
  const labels = kind === 'post' ? {
    not_started: 'Chưa đăng', uploading: 'Đang chuẩn bị/đăng', submitted: 'Đã gửi, chưa xác minh',
    published: 'Đã đăng', failed: 'Lỗi', previewed: 'Chạy thử',
  } : {
    not_started: 'Chưa bình luận', pending: 'Đang bình luận', published: 'Đã bình luận',
    failed: 'Lỗi bình luận', not_applicable: 'Không áp dụng',
  };
  return `<span class="history-status history-${escapeAttr(value)}">${escapeHtml(labels[value] || value)}</span>`;
}

function historyLink(url, label) {
  if (!/^https?:\/\//i.test(url || '')) return '';
  return `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">${label} ↗</a>`;
}

function historyRow(item, index) {
  const date = new Date(item.created_at).toLocaleString('vi-VN');
  const retry = ['submitted', 'published'].includes(item.post_status) && !['published', 'not_applicable'].includes(item.comment_status)
    ? `<button class="secondary history-retry" type="button" data-history-retry="${item.id}">Chạy lại comment</button>` : '';
  return `<tr><td>${historyOffset + index + 1}</td><td>${escapeHtml(date)}</td>
    <td><div class="history-article">${escapeHtml(item.article_title || 'Chưa có mô tả Reel')}</div>
    <div class="history-links">${historyLink(item.source_url, 'Bài báo')} ${historyLink(item.post_url, 'Bài Facebook')}</div>
    ${item.video_content ? `<details><summary>Nội dung video</summary><pre>${escapeHtml(item.video_content)}</pre></details>` : ''}</td>
    <td>${historyStatus('post', item.post_status)}</td><td>${historyStatus('comment', item.comment_status)}</td>
    <td>${item.error ? `<details><summary>Xem lỗi</summary><pre>${escapeHtml(item.error)}</pre></details>` : '—'}</td>
    <td>${retry || '—'}</td></tr>`;
}

async function loadHistory(reset = false) {
  if (!historyPageId) return;
  if (reset) historyOffset = 0;
  const content = document.querySelector('#history-content');
  const more = document.querySelector('#history-more');
  if (reset) content.innerHTML = 'Đang tải…';
  more.disabled = true;
  try {
    const result = await getJson(`/api/pages/${historyPageId}/history?limit=${historyPageSize}&offset=${historyOffset}`);
    if (reset) {
      content.innerHTML = result.total ? '<div class="history-scroll"><table><thead><tr><th>STT</th><th>Thời gian</th><th>Mô tả Reel / bài báo</th><th>Trạng thái đăng</th><th>Trạng thái comment</th><th>Lỗi</th><th>Thao tác</th></tr></thead><tbody></tbody></table></div>' : '<p class="history-empty">Chưa có lịch sử. Các lần chạy mới sẽ được lưu tại đây.</p>';
    }
    const body = content.querySelector('tbody');
    if (body) body.insertAdjacentHTML('beforeend', result.items.map((item, index) => historyRow(item, index)).join(''));
    historyOffset += result.items.length;
    more.hidden = historyOffset >= result.total;
  } catch (error) {
    content.innerHTML = `<p class="history-empty error">${escapeHtml(error.message)}</p>`;
    more.hidden = true;
  } finally { more.disabled = false; }
}

async function openHistory(pageId, pageName) {
  historyPageId = pageId;
  document.querySelector('#history-title').textContent = pageName;
  document.querySelector('#history-dialog').showModal();
  await loadHistory(true);
}

document.querySelector('#history-close').addEventListener('click', () => document.querySelector('#history-dialog').close());
document.querySelector('#history-refresh').addEventListener('click', () => loadHistory(true));
document.querySelector('#history-more').addEventListener('click', () => loadHistory(false));
document.querySelector('#history-dialog').addEventListener('close', () => { historyPageId = null; });
document.querySelector('#history-content').addEventListener('click', async event => {
  const button = event.target.closest('[data-history-retry]');
  if (!button || !historyPageId) return;
  if (!confirm('Chỉ tìm lại bài đã đăng và bình luận link, không đăng Reel mới?')) return;
  button.disabled = true;
  button.textContent = 'Đang tìm bài…';
  try {
    await request(`/api/pages/${historyPageId}/history/${button.dataset.historyRetry}/comment/retry`, { method: 'POST' });
    await loadHistory(true);
    await loadProfiles();
  } catch (error) {
    alert(error.message);
    await loadHistory(true);
  }
});

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { 'Content-Type': 'application/json' } });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || 'Thao tác thất bại');
  return body;
}

function bulkTemplateText() {
  return ['page_id\tlink_bao\tmo_ta_reel\tnoi_dung_video',
    ...selectedPages.map(page => `${page.facebook_id}\t\t\t`)].join('\n');
}

function resetBulkPreview(message = 'Chưa kiểm tra dữ liệu.') {
  bulkPreviewState = null;
  document.querySelector('#bulk-apply').disabled = true;
  document.querySelector('#bulk-preview').textContent = message;
}

function renderBulkPreview(result) {
  const box = document.querySelector('#bulk-preview');
  const errors = result.errors.length
    ? `<div class="bulk-errors"><b>${result.errors.length} lỗi — chưa lưu Page nào:</b><ul>${result.errors.map(item => `<li>Dòng ${item.line}: ${escapeHtml(item.message)}</li>`).join('')}</ul></div>` : '';
  const summary = `<p><b>${result.rows.length} Page có dữ liệu</b> · ${result.changed} Page sẽ cập nhật · ${result.overwrite} Page có nội dung cũ sẽ bị thay thế</p>`;
  const table = result.rows.length ? `<div class="bulk-table-wrap"><table><thead><tr><th>Page</th><th>Link báo</th><th>Mô tả Reel</th><th>Nội dung video</th><th>Thao tác</th></tr></thead><tbody>${result.rows.map(row => `<tr><td><b>${escapeHtml(row.name)}</b><br><code>${escapeHtml(row.facebook_id)}</code></td><td class="bulk-url">${escapeHtml(row.source_url)}</td><td>${escapeHtml(row.reel_description.slice(0, 140))}${row.reel_description.length > 140 ? '…' : ''}</td><td><details><summary>${row.video_content.length} ký tự · Xem</summary><pre>${escapeHtml(row.video_content)}</pre></details></td><td>${escapeHtml(({ new: 'Nhập mới', overwrite: 'Ghi đè', unchanged: 'Không đổi' })[row.action])}</td></tr>`).join('')}</tbody></table></div>` : '';
  box.innerHTML = errors + summary + table;
}

document.querySelector('#bulk-import-open').addEventListener('click', () => {
  if (!selectedProfile || runState.active || activeSchedule || hasUnsavedContent()) return;
  document.querySelector('#bulk-title').textContent = `Nhập hàng loạt · ${selectedProfile.name}`;
  document.querySelector('#bulk-text').value = '';
  resetBulkPreview();
  document.querySelector('#bulk-dialog').showModal();
});
document.querySelector('#bulk-close').addEventListener('click', () => document.querySelector('#bulk-dialog').close());
document.querySelector('#bulk-text').addEventListener('input', () => resetBulkPreview('Dữ liệu đã thay đổi. Hãy kiểm tra lại trước khi lưu.'));
document.querySelector('#bulk-template').addEventListener('click', () => {
  if (!selectedProfile) return;
  const blob = new Blob(['\ufeff', bulkTemplateText()], { type: 'text/tab-separated-values;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `mau-nhap-page-${selectedProfile.name}.tsv`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector('#bulk-file').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  document.querySelector('#bulk-text').value = await file.text();
  event.target.value = '';
  resetBulkPreview('Đã đọc file. Bấm “Kiểm tra & xem trước” để đối chiếu Page.');
});
document.querySelector('#bulk-check').addEventListener('click', async event => {
  if (!selectedProfile) return;
  const text = document.querySelector('#bulk-text').value;
  if (!text.trim()) { resetBulkPreview('Hãy dán dữ liệu TSV hoặc chọn file mẫu đã điền.'); return; }
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await request(`/api/profiles/${selectedProfile.id}/pages/import`, {
      method: 'POST', body: JSON.stringify({ text, apply: false }),
    });
    bulkPreviewState = { profileId: selectedProfile.id, text, result };
    renderBulkPreview(result);
    document.querySelector('#bulk-apply').disabled = result.errors.length > 0 || result.changed === 0;
  } catch (error) { resetBulkPreview(error.message); }
  finally { button.disabled = false; }
});
document.querySelector('#bulk-apply').addEventListener('click', async event => {
  const state = bulkPreviewState;
  if (!state || state.profileId !== selectedProfile?.id ||
      state.text !== document.querySelector('#bulk-text').value || state.result.errors.length ||
      runState.active || activeSchedule || hasUnsavedContent()) return;
  if (!confirm(`Lưu nội dung cho ${state.result.changed} Page của ${selectedProfile.name}? ${state.result.overwrite} Page sẽ bị ghi đè nội dung cũ.`)) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const saved = await request(`/api/profiles/${state.profileId}/pages/import`, {
      method: 'POST', body: JSON.stringify({ text: state.text, apply: true }),
    });
    document.querySelector('#bulk-dialog').close();
    await loadProfiles(state.profileId);
    alert(`Đã lưu ${saved.saved} Page. Chưa đăng bài; bạn có thể đăng ngay hoặc đặt lịch.`);
  } catch (error) { alert(error.message); button.disabled = false; }
});

async function saveSource(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('button');
  const sourceUrl = form.elements.source_url.value.trim();
  const reelDescription = form.elements.reel_description.value.trim();
  const videoContent = form.elements.video_content.value.trim();
  button.disabled = true; button.textContent = 'Đang lưu…';
  try {
    await request(`/api/pages/${form.dataset.pageId}/source`, { method: 'POST', body: JSON.stringify({ source_url: sourceUrl, reel_description: reelDescription, video_content: videoContent }) });
    await loadProfiles();
  } catch (error) { alert(error.message); button.disabled = false; button.textContent = 'Lưu nội dung'; }
}

async function runPage(pageId, button) {
  button.disabled = true; const old = button.textContent; button.textContent = 'Đang tạo & đăng…';
  try {
    const running = request(`/api/pages/${pageId}/run`, { method: 'POST' });
    await loadRunState();
    await running;
    await loadProfiles();
    alert('Đã tạo video, đăng Reel và bình luận link nguồn.');
  } catch (error) {
    alert(error.message);
    await loadProfiles();
    button.disabled = false;
    button.textContent = old;
  } finally {
    await loadRunState();
  }
}

document.querySelector('#run-all-pages').addEventListener('click', async event => {
  if (!selectedProfile || !selectedRunnableCount) return;
  const profileId = selectedProfile.id;
  if (!confirm(`Tạo video và đăng Reel thật cho ${selectedRunnableCount} Page đã lưu đủ nội dung trong ${selectedProfile.name}?`)) return;
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = 'Đang chạy hàng loạt…';
  try {
    const running = request(`/api/profiles/${profileId}/pages/run`, { method: 'POST' });
    await loadRunState();
    const result = await running;
    await loadProfiles();
    const failures = result.results.filter(item => item.status === 'failed');
    alert(`Đã đăng ${result.published}/${result.total} Page.${failures.length ? ` Lỗi: ${failures.map(item => item.name).join(', ')}.` : ''}`);
  } catch (error) { alert(error.message); }
  finally {
    await loadRunState();
    button.disabled = runState.active || selectedRunnableCount === 0;
    button.textContent = `Đăng tất cả (${selectedRunnableCount})`;
  }
});

document.querySelector('#schedule-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!selectedProfile || !selectedRunnableCount || hasUnsavedContent()) return;
  const profileId = selectedProfile.id;
  const input = document.querySelector('#schedule-time');
  const when = new Date(input.value);
  if (Number.isNaN(when.getTime()) || when <= new Date()) {
    alert('Hãy chọn thời gian hợp lệ trong tương lai.');
    return;
  }
  if (!confirm(`Hẹn đăng ${selectedRunnableCount} Page đã lưu nội dung của ${selectedProfile.name} lúc ${when.toLocaleString('vi-VN')}?`)) return;
  const button = document.querySelector('#schedule-submit');
  button.disabled = true;
  try {
    await request(`/api/profiles/${profileId}/pages/schedules`, {
      method: 'POST', body: JSON.stringify({ scheduled_at: when.toISOString() }),
    });
    await loadSchedules(profileId);
  } catch (error) { alert(error.message); }
  finally { updateActionButtons(); }
});

document.querySelector('#schedule-list').addEventListener('click', async event => {
  const button = event.target.closest('[data-cancel-schedule]');
  if (!button || !selectedProfile) return;
  if (!confirm('Huỷ lịch đăng này? Nội dung đã lưu của các Page vẫn được giữ.')) return;
  button.disabled = true;
  try {
    await request(`/api/profiles/${selectedProfile.id}/pages/schedules/${button.dataset.cancelSchedule}`, { method: 'DELETE' });
    await loadSchedules();
  } catch (error) { alert(error.message); button.disabled = false; }
});

document.querySelector('#page-search').addEventListener('input', applyPageFilters);
document.querySelector('#page-filter').addEventListener('change', applyPageFilters);

document.querySelector('#pause-run').addEventListener('click', async () => {
  try { showRunState(await request('/api/run/pause', { method: 'POST' })); }
  catch (error) { alert(error.message); await loadRunState(); }
});

document.querySelector('#resume-run').addEventListener('click', async () => {
  try { showRunState(await request('/api/run/resume', { method: 'POST' })); }
  catch (error) { alert(error.message); await loadRunState(); }
});

async function loadBrowserStatus() {
  try {
    const profileName = selectedProfile?.name || '';
    const status = await getJson(`/api/browser/status?profile_name=${encodeURIComponent(profileName)}`);
    if (profileName !== (selectedProfile?.name || '')) return;
    const label = document.querySelector('#browser-status');
    label.textContent = status.connected
      ? `Đã kết nối ${status.profile_name}`
      : status.browser_running
        ? `${profileName} đang mở · chưa kết nối`
        : profileName ? `${profileName} chưa mở` : 'Chưa chọn profile';
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

document.querySelector('#refresh-pages').addEventListener('click', async event => {
  if (!selectedProfile) return;
  const profileId = selectedProfile.id;
  const button = event.currentTarget;
  const connectButton = document.querySelector('#connect-browser');
  button.disabled = true;
  connectButton.disabled = true;
  button.textContent = 'Đang mở profile & quét…';
  pageList.innerHTML = '<div class="loading">Đang quét danh sách Page trên Facebook…</div>';
  try {
    const result = await request(`/api/profiles/${profileId}/pages/refresh`, { method: 'POST' });
    await loadBrowserStatus();
    await loadProfiles(profileId);
    button.textContent = `Đã tìm ${result.count} Page`;
    window.setTimeout(() => { button.textContent = 'Quét lại Page'; }, 2200);
  } catch (error) {
    pageList.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
    alert(error.message);
    button.textContent = 'Quét lại Page';
  } finally {
    button.disabled = false;
    connectButton.disabled = false;
  }
});

async function loadProfiles(preferredProfileId = null) {
  try {
    const profiles = await getJson('/api/profiles');
    document.querySelector('#profile-count').textContent = profiles.length;
    profileList.innerHTML = '';
    let preferred = null;
    profiles.forEach((profile, index) => {
      const button = document.createElement('button');
      button.className = 'profile-item';
      button.innerHTML = `<span class="avatar">${escapeHtml(profile.name.replace('FB_', ''))}</span><span><b>${escapeHtml(profile.name)}</b><small>${profile.page_count || 0} Page</small></span><span class="chevron">›</span>`;
      button.addEventListener('click', () => selectProfile(profile, button));
      profileList.appendChild(button);
      if (profile.id === (preferredProfileId || selectedProfile?.id) || (!preferred && index === 0)) {
        preferred = { profile, button };
      }
    });
    if (preferred) await selectProfile(preferred.profile, preferred.button);
  } catch (error) {
    profileList.innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`;
  }
}

loadProfiles();
loadBrowserStatus();
loadRunState();
setupScheduleTime();
window.setInterval(loadRunState, 2000);
window.setInterval(() => loadSchedules(), 10000);
if (window.EventSource) {
  const progressStream = new EventSource('/api/run/events');
  progressStream.onmessage = event => {
    try { showRunState(JSON.parse(event.data)); } catch (_) {}
  };
}
