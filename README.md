# HideMyAcc Facebook Reel Tool

Dashboard local không cần đăng nhập. Với mỗi Page, nhập **link bài viết**, **Mô tả thước phim của bạn** và **Nội dung video**, rồi bấm **Lưu nội dung**. Link bài viết chỉ dùng để lấy ảnh; mô tả Reel và chữ chính trên video lấy từ hai ô nhập thủ công, không tự lấy tiêu đề hay đoạn mở đầu từ bài báo. Nội dung video không giới hạn ký tự trong form; nếu dài hơn khung hình, tool tự chia thành nhiều màn hình và tăng thời lượng để hiện đủ chữ. Phía dưới video luôn có lời dẫn “xem thêm chi tiết trong bình luận 👇”, được dịch theo ngôn ngữ nội dung đã nhập. Khi bấm **Chạy Page** hoặc **Chạy tất cả**, tool tạo Reel 9:16 dài ngẫu nhiên 15–30 giây với nội dung ngắn (có thể dài hơn nếu nhiều màn hình), chọn ngẫu nhiên một tệp âm thanh trong thư mục `sound/`, đăng lên Facebook và bình luận link nguồn. Nếu thư mục không có âm thanh hợp lệ, quá trình tạo Reel sẽ báo lỗi thay vì đăng video im lặng.

Luồng hiện tại: `Lưu link + mô tả + nội dung video → Chạy Page / Chạy tất cả → lấy ảnh và tạo Reel → đăng với mô tả đã nhập → quét bài khớp mô tả → comment link`. Nút **Tạm dừng** giữ tác vụ ở điểm an toàn trước khi đăng hoặc trước Page tiếp theo; **Tiếp tục** chạy tiếp. Nếu Reel đã đăng, tool hoàn tất tìm bài và bình luận trước khi tạm dừng.

Khung **Tiến độ realtime** hiển thị từng công đoạn và Page đang xử lý. Khi lỗi, mở **Xem lỗi chi tiết** để xem nguyên nhân; nhật ký của lần chạy gần nhất vẫn hiện sau khi tác vụ kết thúc.

Mỗi Page có nút **Xem lịch sử** mở popup các lần chạy đã lưu (từ khi tính năng này được thêm). Bảng hiển thị STT, thời gian, tiêu đề, trạng thái đăng Reel, trạng thái comment, link bài và lỗi. Trạng thái **Đã gửi, chưa xác minh** nghĩa là tool đã bấm đăng nhưng chưa tìm được bài mới; hãy kiểm tra Page trước khi chạy lại để tránh đăng trùng.
Sau khi Reel và bình luận được xác nhận thành công, ba ô link, mô tả và nội dung video của Page được dọn để không vô tình đăng lại. Trạng thái **Đã đăng** và link Facebook vẫn còn; nội dung vừa dùng được giữ trong popup lịch sử. Khi comment còn lỗi, ô nhập chưa bị dọn để có thể xử lý lại.
Với lần chạy đã gửi bài nhưng comment chưa xong, dùng **Chạy lại comment** trong lịch sử: tool quét lại Page theo mô tả Reel đã lưu và chỉ bình luận trên bài hiện có, không tải/đăng lại video. Khi Facebook mở trang Reel riêng hoặc biến link bình luận thành thẻ xem trước qua `l.facebook.com`, tool nhận diện lại bài và kiểm tra link gốc để không bình luận trùng.

Sau khi xác nhận Reel đã đăng trên đúng Page, tool xoá riêng tệp `reel.mp4` do mình tạo trong `output/pages/` để tiết kiệm dung lượng. Nếu chưa xác minh được bài đăng, video được giữ lại; tệp video ngoài thư mục tạo Reel của tool không bị xoá.

Không cần quyền API Hidemyacc. Nút chạy sẽ tự kết nối đúng profile; nếu profile đang chạy khỏe, tool dùng lại browser hiện có. **Chạy tất cả** xử lý tuần tự những Page đã lưu đủ cả ba ô trong profile và trả kết quả cho từng Page.

Để hẹn giờ, chọn profile, lưu đủ ba ô cho các Page cần đăng, chọn **Thời điểm đăng** theo giờ máy rồi bấm **Đặt lịch**. Lịch lưu danh sách Page đã sẵn sàng tại lúc đặt; khi đến giờ, dashboard local tự kết nối profile và xử lý từng Page một lần. Dashboard phải tiếp tục chạy. Nếu browser đang bận, lịch sẽ chờ; nếu Page không còn đủ nội dung hoặc đang cần chạy lại comment, Page đó được bỏ qua và hiện trong kết quả. Có thể huỷ lịch đang chờ; muốn đăng ngay khi đã có lịch, hãy huỷ lịch trước. Nếu dashboard dừng giữa lúc đăng, lịch được đánh dấu cần kiểm tra khi khởi động lại, không tự đăng lặp. Nếu dashboard tắt và lỡ giờ hẹn quá một phút, lịch quá hạn cũng không tự đăng bù khi mở lại.

**Nhập hàng loạt:** Chọn profile rồi bấm **Nhập hàng loạt** → **Tải mẫu Page của profile**. Mở file TSV trong Excel/Google Sheets và điền bốn cột `page_id`, `link_bao`, `mo_ta_reel`, `noi_dung_video`; Page ID đã được điền sẵn. Dòng không có nội dung được bỏ qua. Dán bảng vào ô nhập hoặc chọn file TSV, bấm **Kiểm tra & xem trước**, rồi xác nhận **Lưu hàng loạt**. Nội dung video có thể xuống dòng trong một ô và không bị giới hạn ký tự. Tool kiểm tra đúng Page ID, báo rõ dòng lỗi và chỉ lưu khi toàn bộ dữ liệu hợp lệ; nhập hàng loạt chỉ lưu nội dung, không tự đăng bài.

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
python3.11 -m venv .venv311
source .venv311/bin/activate
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
source .venv311/bin/activate
pip install -r requirements-agent.txt
playwright install chromium

export SERVER_URL=https://dashboard-cua-ban.example.com
export WORKER_TOKEN=gia-tri-giong-tren-dashboard
export AGENT_NAME=mac-studio-01
export DRY_RUN=true
python -m apps.agent
```

Nếu Hidemyacc chưa mở, tool tự khởi động ứng dụng. Profile đang chạy và còn kết nối sẽ được dùng lại; chuyển sang profile khác không tắt profile cũ. Chỉ profile bị lỗi kết nối mới được đóng và mở lại. Nếu cửa sổ quản lý Hidemyacc đang mở nhưng chưa bật cổng điều khiển local, tool sẽ báo lỗi để bạn mở lại ứng dụng với cổng 9333, không tự đóng các profile đang chạy.

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
