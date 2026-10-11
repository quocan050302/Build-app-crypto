# V14 SETUP QUALITY & FOCUSED CHART EVIDENCE REPORT

## 1. Executive Summary
- **Repository**: `quocan050302/Build-app-crypto`
- **Branch**: `feature/aurum-repair-smc-rr`
- **Base HEAD**: `c7f9e54db7886349dc7bcd053caec7aa3013d2ff` (fix(v13.8): finalize causal replay accounting and strict acceptance evidence)
- **Feature Name**: Bộ đánh giá chất lượng điểm vào SMC phiên Mỹ và bằng chứng cấu trúc theo lệnh trên Chart (V14)
- **Operating Mode**: `EXPLAIN_ONLY` (mặc định giải thích và ghi nhận chất lượng, tuyệt đối không thay đổi quyết định vào/ra lệnh, sizing, trigger thresholds, quota hay execution guards).
- **Core Verification Status**: **PASSED ALL GATES**
  - Backend Unit & Integration Tests: **41/41 PASSED** (9 V14 quality tests + 32 V13.8 regression tests).
  - Frontend Vitest Suite: **123/123 PASSED** across 16 test suites.
  - Frontend Production Build: **PASSED** (`vite build` in 797ms).
  - Browser Verification: **PASSED** on real browser with Lightweight Charts v5.2, R:R overlay, compact 4-line quality card, and `TradeDetailsDrawer` inspection.

> **Lưu ý phương pháp luận quan trọng**: Chức năng V14 phục vụ mục đích thu thập và giải thích chất lượng setup có thể kiểm chứng được để phục vụ nghiên cứu nâng cao winrate. Chưa có kiểm chứng thực nghiệm forward độc lập thì không tuyên bố chắc chắn tăng winrate hoặc gán xác suất thắng tùy tiện.

---

## 2. Key Defect Fixes & Enhancements

### 2.1. Bug Sửa Lỗi Hiển Thị CHOCH trên Canvas (Part 08, 85, C1)
- **Vấn đề phát hiện**: Backend SMC detector xuất `event_type = 'CHOCH'` (chữ in hoa). Trong khi đó, `SMCStructurePrimitive.ts` của frontend kiểm tra `if (ev.event_type !== 'BOS' && ev.event_type !== 'CHoCH') continue;`. Vì `'CHOCH' !== 'CHoCH'`, toàn bộ các sự kiện CHOCH từ backend bị bỏ qua và không bao giờ được vẽ lên biểu đồ Lightweight Charts.
- **Giải pháp**:
  - Chuẩn hóa canonical domain enum thành `CHOCH` in hoa.
  - Tại `chartEvidence.ts`, hàm `normalizeStructureKind` chấp nhận cả `CHOCH`, `CHoCH`, và `choch`.
  - Trong `SMCStructurePaneRenderer.draw`:
    - Hỗ trợ cả uppercase `CHOCH` và `CHoCH`.
    - Màu vẽ đường CHOCH là tím `#a855f7`, nhãn hiển thị thân thiện `'CHoCH'`.
    - Đường BOS giữ màu theo hướng (`#06b6d4` cho Bullish, `#f97316` cho Bearish).

### 2.2. Loại Bỏ Tọa Độ Giả `0` / `200` (Part C2)
- **Vấn đề phát hiện**: Khi sự kiện break nằm ngoài khoảng nến đang tải hoặc coordinate trả về `null`, code cũ đặt fallback `const x1 = rawX1 !== null ? Number(rawX1) : 0; const x2 = rawX2 !== null ? Number(rawX2) : 200;`. Điều này khiến các đường kẻ bị kéo dài vô lý về góc 0 pixel hoặc 200 pixel của màn hình.
- **Giải pháp**: Loại bỏ hoàn toàn fallback tùy tiện; kiểm tra `rawX1` và `rawX2` chặt chẽ, tính toán độ rộng hữu hạn và bỏ qua an toàn nếu tọa độ không hợp lệ.

### 2.3. Hỗ Trợ Glyphs Cho Sự Kiện Liquidity Sweep (Part 85, C1)
- Thêm xử lý riêng cho `ev.event_type === 'SWEEP'`: vẽ tick ngang tại mức giá bị quét kèm glyph mũi tên/tam giác màu hổ phách `#eab308` và huy hiệu `SWEEP`, tách biệt rõ với đường kẻ đứt của BOS/CHOCH.

### 2.4. Khắc Phục Lỗi Trộn Lẫn Bằng Chứng Cấu Trúc Khi Chọn Lệnh (Part 09, 75, 86)
- **Vấn đề phát hiện**: Trước đây khi người dùng click vào một setup dự kiến, R:R được cố định nhưng các sự kiện BOS/CHOCH/swings trên biểu đồ vẫn lấy từ phân tích thị trường hiện tại (`analysis.swings` và `analysis.structure_events`), dẫn tới R:R của setup A lại hiển thị kèm BOS/CHOCH của thời điểm B.
- **Giải pháp**:
  - Xây dựng `SelectedSetupContext` kết hợp với `buildChartEvidenceBundle`.
  - Khi một setup được chọn, chỉ hiển thị đúng các bằng chứng cấu trúc đã khóa tại thời điểm đánh giá (`as_of_ms`) thông qua `selectedEvidence` truyền vào `ChartComponent`.
  - Người dùng có thể chuyển đổi giữa chế độ "Liên quan lệnh" (`SELECTED`), "Toàn bộ thị trường" (`ALL`), hoặc ẩn cấu trúc (`NONE`).

### 2.5. Chặn Biến Dạng Setup Khi Kéo Giá (Part 10, 80)
- **Vấn đề phát hiện**: Khi người dùng kéo đường R:R trên biểu đồ, handler `onOverlayChange` giữ nguyên `source: 'WATCH_SETUP'` và trạng thái `state`, vô tình biến một bản chỉnh sửa giá tự do thành setup của máy chủ.
- **Giải pháp**:
  - Khi kéo giá trên biểu đồ, hệ thống tự động chuyển sang `source: 'DRAFT'`, tạo ID draft mới `draft-${Date.now()}`, hủy trạng thái `READY`/`ARMED` của máy chủ.
  - Kích hoạt endpoint tính toán xem trước `POST /api/v1/quality/preview` với debounce, không ghi đè setup gốc và không tạo order tự động.
  - Vị thế đang mở (`OPEN_POSITION`) được khóa `read-only`, không cho phép kéo làm thay đổi mức giá.

### 2.6. Chống Race Condition Khi Chọn Setup Liên Tiếp (Part 81)
- Trong `App.tsx`, mọi yêu cầu tải đánh giá chất lượng được quản lý bằng generation token (`qualityGenRef`) và `AbortController`. Khi người dùng chuyển đổi nhanh giữa các setup, các response đến muộn tự động bị loại bỏ, tránh việc setup A nhận dữ liệu của setup B.

---

## 3. Kiến Trúc Backend Module Mới

### 3.1. Pure Service: `backend/services/setup_quality_service.py`
- Không phụ thuộc DB session, clock hay network client.
- Thứ tự ưu tiên suy diễn trạng thái (`derive_quality_status`):
  1. `validation_errors` hoặc mã check trùng lặp -> `ERROR`
  2. `existing_hard_blockers` -> `BLOCKED_EXISTING`
  3. `not assessed` -> `NOT_ASSESSED`
  4. Có check `REQUIRED` ở trạng thái `UNKNOWN` -> `INSUFFICIENT_DATA`
  5. Có check `REQUIRED` ở trạng thái `NOT_MET` -> `PARTIAL`
  6. Toàn bộ check `REQUIRED` đều `MET` -> `SATISFIED`
- Truy vết nhân quả (`select_related_evidence`):
  - Duyệt theo `source_ids` từ các sự kiện đã khớp.
  - Loại bỏ các bằng chứng có `known_at_ms > as_of_ms` (chống lùi thời gian / lookahead bias).
  - Đảm bảo tính duy nhất của ID bằng cơ chế deduplication an toàn.

### 3.2. Idempotent Repository: `backend/services/setup_quality_repository.py`
- Lưu trữ các snapshot bất biến vào bảng `setup_quality_snapshots`.
- Hỗ trợ hàm đọc theo batch `get_snapshots_batch()` giúp endpoint `GET /api/v1/setups/upcoming` nạp nhanh danh sách mà không bị lỗi N+1 truy vấn.

### 3.3. Các Endpoint API Mới
1. `GET /api/v1/setups/{setup_id}/quality`: Trả về chi tiết đánh giá chất lượng cho setup ID cụ thể (trả về 409 nếu revision thay đổi, 404 nếu không tìm thấy setup).
2. `GET /api/v1/quality/snapshots/{snapshot_id}`: Đọc snapshot bất biến theo ID.
3. `POST /api/v1/quality/preview`: Tính toán đánh giá tức thời cho kế hoạch tự vẽ (DRAFT) mà không lưu vào DB.

---

## 4. Giao Diện Người Dùng (UI)

### 4.1. Thẻ Đánh Giá Tinh Gọn 4 Dòng Trong QuickDecisionSidebar (Part 111, 112)
- **Dòng 1**: Huy hiệu trạng thái tiếng Việt chuẩn (`Đủ checklist`, `Đang chờ xác nhận`, `Thiếu dữ liệu`, `Đang bị chặn`, `Chưa đánh giá được`, `Chưa có đánh giá`) + Số lượng tiêu chí đạt (`x/y chuẩn`).
- **Dòng 2**: Căn cứ chính xác nhận từ cấu trúc thị trường (`primary_reason_vi`).
- **Dòng 3**: Điều kiện cần chờ tiếp theo hoặc lưu ý quan trọng (`next_condition_vi` / `caution_vi`).
- **Dòng 4**: Các nút thao tác nhanh:
  - `Chi tiết chất lượng`: Mở ngăn kéo `TradeDetailsDrawer` tại mục Chất Lượng SMC.
  - `Định vị trên Chart`: Căn chỉnh lại tầm nhìn biểu đồ bao quát vùng vào lệnh và các đỉnh đáy liên quan.

### 4.2. Ngăn Kéo Chi Tiết Đầy Đủ (TradeDetailsDrawer)
- Thêm tab `Chất Lượng SMC` bên cạnh các tab `Điều Kiện`, `Bài Học`, `Lệnh Dự Kiến`, `Rủi Ro`, `Phiên & Chính Sách`.
- Hiển thị bảng checklist chi tiết: mã tiêu chí, giải thích tiếng Việt, phân loại `BẮT BUỘC` vs `BỔ TRỢ`, trạng thái `MET` / `NOT_MET` / `UNKNOWN`.
- Liệt kê toàn bộ chuỗi mắt xích cấu trúc (Swings, Sweeps, BOS, CHOCH, FVG, Liquidity Targets) kèm mốc thời gian đã khóa.
- Thông báo minh bạch về chế độ `EXPLAIN_ONLY`.

---

## 5. Bằng Chứng Kiểm Thử & Nghiệm Thu

### 5.1. Backend Tests
Lệnh chạy:
```bash
PYTHONPATH=backend backend/venv/bin/python -m pytest backend/tests/test_setup_quality_*.py backend/tests/test_v13_8_*.py
```
Kết quả:
- `backend/tests/test_setup_quality_service_v14.py`: 5 passed (Precedence, Strict Causality, LONG Full Chain, SHORT Full Chain, Partial/Waiting setups).
- `backend/tests/test_setup_quality_api_v14.py`: 4 passed (404 handling, 409 revision guard, Draft quality preview calculation, Upcoming setup response schemas).
- `backend/tests/test_v13_8_*.py`: 32 passed (Toàn bộ suite hồi quy v13.8 không bị ảnh hưởng).
- **Tổng cộng: 41 passed (100%), 0 failed**.

### 5.2. Frontend Tests & Build
Lệnh chạy:
```bash
npm --prefix frontend test -- --run
npm --prefix frontend run build
```
Kết quả:
- **16 test suites passed (123 tests passed)**, bao gồm `src/v14_chart_evidence.test.ts`.
- **Vite production bundle built thành công** (`dist/assets/index-BbbebbOi.js` 909.79 kB) mà không có lỗi TypeScript hay Linting.

### 5.3. Bằng Chứng Chạy Trên Biểu Đồ Thật (Browser Subagent)
- **Browser Recording**: `file:///Users/macbook/.gemini/antigravity-ide/brain/82227e40-fd35-4325-98a8-53523222e5e3/v14_setup_quality_demo_1791697597255.webp`
- **Screenshots được lưu**:
  - `upcoming_setups_tab`: Màn hình danh sách lệnh dự kiến và các kịch bản phiên.
  - `after_view_on_chart`: Click "Xem Trên Chart", biểu đồ Lightweight Charts canvas hiển thị R:R overlay và sidebar hiển thị thẻ chất lượng 4 dòng.
  - `drawer_opened`: Click "Chi tiết chất lượng", mở `TradeDetailsDrawer` với đầy đủ các mục phân tích cấu trúc SMC.
  - `conditions_tab`: Kiểm tra danh sách điều kiện và các rào chắn kỷ luật.
