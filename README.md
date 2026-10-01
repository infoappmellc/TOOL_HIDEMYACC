# HideMyAcc Facebook Reel Tool

Dashboard local không cần đăng nhập. Chọn profile và Page, nhập link bài viết, tool lấy ảnh đầu tiên cùng đoạn mở đầu để tạo Reel 9:16. Link nguồn được dùng làm bình luận đầu tiên sau khi đăng.

Luồng hiện tại: `Link nguồn → tạo Reel → chạy thử Facebook → đăng Reel → comment link`.

Không cần quyền API Hidemyacc. Chọn profile trong dashboard rồi bấm **Mở & kết nối**; tool tự tìm đúng tên profile trên giao diện Hidemyacc, bấm **Run** và kết nối browser local. Luôn dùng **Chạy thử** trước; nút **Đăng Reel + comment** là thao tác đăng thật.

Hệ thống quản lý lịch tạo video và đăng Facebook, được tách thành hai tiến trình độc lập:

- **Dashboard**: deploy lên VPS/Render/Railway, quản lý profile, lịch đăng, trạng thái và log.
- **Local Agent**: chỉ chạy trên máy có Hidemyacc. Agent tạo video bằng FFmpeg rồi kết nối vào browser profile qua CDP/Playwright để đăng bài.

Dashboard không giữ cookie Facebook và không điều khiển trực tiếp máy local. Agent chủ động lấy việc qua HTTPS nên máy local không cần public port.

## Luồng xử lý

```text
Dashboard cloud ── job + caption ──> Local Agent
Dashboard cloud <── status + log ─── Local Agent
                                     ├─ FFmpeg tạo MP4
                                     └─ Giao diện HideMyAcc → Facebook
```

## 1. Chạy dashboard

```bash
cp .env.example .env
# sửa APP_SECRET, ADMIN_PASSWORD và WORKER_TOKEN
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dashboard.txt
python -m apps.dashboard
```

Mở `http://127.0.0.1:8000`, đăng nhập bằng `ADMIN_PASSWORD`.

Hoặc dùng Docker:

```bash
docker compose up --build -d
```

Khi deploy, gắn persistent volume vào `/app/data`, đặt HTTPS và khai báo ba biến bí mật. Không commit file `.env`.

### Deploy Render

Repo đã có `render.yaml`. Tạo **Blueprint** từ repository, nhập `ADMIN_PASSWORD` và `WORKER_TOKEN`, sau đó deploy. Disk 1 GB được mount ở `/app/data`; `COOKIE_SECURE=true` chỉ gửi session qua HTTPS. Với Railway/VPS, dùng cùng `Dockerfile` và nhớ tạo volume persistent tương ứng.

## 2. Chạy Local Agent

Yêu cầu:

- Dashboard tự tạo danh sách quy ước `FB_01` đến `FB_10`, không cần API để quét profile.
- Hidemyacc đã đăng nhập và đang mở trên máy local; tool điều khiển trực tiếp nút **Run**, không gọi API Hidemyacc.
- FFmpeg có trong `PATH`, hoặc dùng binary fallback được cài cùng `requirements-agent.txt`.
- Profile Hidemyacc đã đăng nhập Facebook trước đó.

```bash
source .venv/bin/activate
pip install -r requirements-agent.txt
playwright install chromium

export SERVER_URL=https://dashboard-cua-ban.example.com
export WORKER_TOKEN=gia-tri-giong-tren-dashboard
export AGENT_NAME=mac-studio-01
export DRY_RUN=true
python -m apps.agent
```

Nếu Hidemyacc chưa mở, tool tự khởi động ứng dụng. Nếu ứng dụng đã mở theo chế độ thông thường, tool khởi động lại cửa sổ quản lý một lần để bật điều khiển local; phiên đăng nhập và dữ liệu profile được giữ nguyên.

Giữ `DRY_RUN=true` để kiểm thử: agent sẽ tạo video, mở composer và điền nội dung nhưng **không bấm Đăng**. Khi đã thử đúng selector trên tài khoản của bạn, đổi thành `DRY_RUN=false`.

## Cách tạo job

1. Vào **Profiles**, chọn profile mặc định `FB_01`–`FB_10`; có thể đổi số lượng bằng `DEFAULT_PROFILE_COUNT`.
2. Vào **Tạo lịch đăng**, nhập caption, thời gian và cấu hình video.
3. Agent online sẽ nhận job đến hạn, tạo MP4 và đăng.

Nguồn hình có thể là URL `https://...` hoặc đường dẫn tuyệt đối trên máy chạy agent. Video nền hiện dùng ảnh tĩnh, màu nền, tiêu đề và nhạc tùy chọn. Nếu để trống ảnh, agent tạo một title card.

## API và an toàn

- UI dùng session cookie ký HMAC; API agent dùng `Bearer WORKER_TOKEN`.
- Không lưu mật khẩu Facebook, cookie hoặc proxy trên server.
- Mỗi job chỉ được một agent claim; lease tự hết hạn nếu agent chết giữa chừng.
- Chỉ tự động hóa các Page/group/profile bạn có quyền quản lý. Nội dung phải do bạn sở hữu hoặc có giấy phép và không được dùng để spam.

Tài liệu tương tác có tại `/docs` khi dashboard chạy.
