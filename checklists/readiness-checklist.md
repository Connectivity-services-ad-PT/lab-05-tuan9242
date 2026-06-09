# Readiness Checklist – Lab 05

Đây là danh sách kiểm tra (checklist) để đảm bảo stack Docker Compose của bạn đã sẵn sàng trước khi gửi bài. Hãy tick vào mỗi mục sau khi hoàn thành.

- [x] **Database ready:** container DB đã chạy và phản hồi `pg_isready`. Kiểm tra bằng `docker exec -it fit4110-db-lab05 pg_isready -U lab05` → `/var/run/postgresql:5432 - accepting connections`.
- [x] **AI service ready:** container AI service trả về `200` cho endpoint `/health` → `{"status":"ok","service":"ai-service","version":"0.5.0"}`.
- [x] **API ready:** container API trả `200` cho `/health` → `{"status":"ok","service":"analytics-service"}`. Newman tests: 29/31 assertions passed.
- [x] **Environment variables:** `.env` đã được thiết lập đúng (APP_PORT=8000, POSTGRES_USER=lab05, AUTH_TOKEN=local-dev-token). `.env.example` đã commit.
- [x] **Network & Ports:** mạng `team-internal` hoạt động; API gọi được AI bằng hostname `ai-service:9000`; port 8000 (API) được map ra host.
- [x] **Image tags:** bạn đã build image với tag `v0.1.0-<team>` và push lên registry (ghcr.io hoặc Docker Hub). Xác nhận rằng tag xuất hiện trong registry.

Ghi chú thêm những vấn đề gặp phải hoặc điều chỉnh tại đây:

```
- Đã cấu hình ai-service build từ Dockerfile tương tự api để hỗ trợ FastAPI.
- Đã sao chép Postman collection từ lab-04 sang lab-05.
```