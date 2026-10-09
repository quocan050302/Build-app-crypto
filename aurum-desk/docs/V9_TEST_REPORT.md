# AURUM DESK — V9 FULL SYSTEM TEST & REPAIR REPORT
**Thời gian chạy:** 2026-10-09T13:55:38.132428+07:00 (UTC+7)

## 1. Tổng Kết Kiểm Thử
- **Tổng số kịch bản:** 42
- **PASS:** 41 (97.6%)
- **FAIL:** 0
- **BLOCKED (Cần opt-in / cấu hình):** 1
- **NOT_RUN:** 0

## 2. Chi Tiết Từng Kịch Bản Theo Ma Trận

| ID | Ma trận | Tên Kịch Bản | Trạng Thái | Assertions | Thời Gian | Lỗi / Ghi chú |
|---|---|---|---|---|---|---|
| E01 | LIFECYCLE | LONG LIMIT full flow to TP | **PASS** | 11 | 58ms | - |
| E02 | LIFECYCLE | SHORT LIMIT full flow to TP | **PASS** | 3 | 25ms | - |
| E03 | LIFECYCLE | LONG LIMIT to SL | **PASS** | 4 | 27ms | - |
| E04 | LIFECYCLE | SHORT LIMIT to SL | **PASS** | 2 | 23ms | - |
| E05 | LIFECYCLE | LONG MARKET manual | **PASS** | 2 | 20ms | - |
| E06 | LIFECYCLE | SHORT MARKET manual | **PASS** | 2 | 22ms | - |
| E07 | LIFECYCLE | LONG STOP trigger semantics | **PASS** | 2 | 23ms | - |
| E08 | LIFECYCLE | SHORT STOP trigger semantics | **PASS** | 2 | 20ms | - |
| E09_E10 | LIFECYCLE | Last vs Executable Quote Side | **PASS** | 2 | 21ms | - |
| E11 | LIFECYCLE | Block entry when position open | **PASS** | 2 | 11ms | - |
| E13 | LIFECYCLE | Setup selection conflict 409 | **PASS** | 1 | 4ms | - |
| E14_E15 | LIFECYCLE | True multi-session concurrent fills | **PASS** | 3 | 28ms | - |
| E19_E20 | LIFECYCLE | Manual close in profit and loss | **PASS** | 5 | 18ms | - |
| E21_E22 | LIFECYCLE | Order cancel and expiry isolation | **PASS** | 2 | 12ms | - |
| E24 | LIFECYCLE | Geometry sanity rejection | **PASS** | 3 | 0ms | - |
| E25 | LIFECYCLE | Gap execution pricing | **PASS** | 3 | 13ms | - |
| E26 | LIFECYCLE | Isolated liquidation exit | **PASS** | 2 | 12ms | - |
| E29 | LIFECYCLE | Immutable open position snapshots | **PASS** | 3 | 8ms | - |
| E30 | LIFECYCLE | Daily guards & consecutive loss caps | **PASS** | 2 | 8ms | - |
| E32 | LIFECYCLE | Exit cause independent of PnL | **PASS** | 1 | 4ms | - |
| F01_F02 | FEED | Feed sanity and freshness guards | **PASS** | 4 | 0ms | - |
| F03_F04 | FEED | REST fallback & epoch provenance | **PASS** | 3 | 0ms | - |
| F08 | FEED | High-impact USD news blackout | **PASS** | 1 | 7ms | - |
| F09_F10 | FEED | NY session window & DST dynamic mapping | **PASS** | 3 | 5ms | - |
| F11_F12_F15 | FEED | Offline recovery & idempotent rerun | **PASS** | 3 | 47ms | - |
| T01_T02 | TELEGRAM | Token masking and preservation | **PASS** | 4 | 5ms | - |
| T05_T08 | TELEGRAM | All notification message formatters | **PASS** | 28 | 0ms | - |
| T12 | TELEGRAM | Quiet hours & timezone evaluation | **PASS** | 2 | 0ms | - |
| T15_T16 | TELEGRAM | Strict HTTP response schema validation | **PASS** | 5 | 81ms | - |
| T17 | TELEGRAM | 429 parameters.retry_after parsing | **PASS** | 3 | 12ms | - |
| T23_T24 | TELEGRAM | Worker CAS atomic claim & lease guard | **PASS** | 2 | 10ms | - |
| T26_T27 | TELEGRAM | Outbox retry state transition whitelist | **PASS** | 7 | 0ms | - |
| T32 | TELEGRAM | Live Telegram smoke test | `BLOCKED` | 0 | 0ms | Opt-in flag --telegram-live not enabled |
| J03_J04 | JOURNAL | Journal summary & query aggregation | **PASS** | 5 | 5ms | - |
| J05 | JOURNAL | Review revision optimistic locking 409 | **PASS** | 1 | 3ms | - |
| J06 | JOURNAL | Lesson approval lifecycle & strategy memory | **PASS** | 2 | 5ms | - |
| J07 | JOURNAL | Idempotent close lesson guarantee | **PASS** | 1 | 6ms | - |
| B01 | E2E | LONG manual LIMIT full flow E2E | **PASS** | 8 | 36ms | - |
| B02 | E2E | SHORT Auto flow E2E | **PASS** | 3 | 35ms | - |
| B05 | E2E | Active trade snapshot with global changes | **PASS** | 2 | 18ms | - |
| B08 | E2E | Multi-session concurrency barrier E2E | **PASS** | 3 | 28ms | - |
| B10 | E2E | NY quota reserve and daily cap E2E | **PASS** | 2 | 14ms | - |

## 3. Bảo Toàn Dữ Liệu Runtime & Invariants
- **Runtime Database:** Kiểm tra đường dẫn database độc lập; không có vị thế hoặc cài đặt runtime nào bị thay đổi hay xóa bỏ.
- **Bảo vệ Rủi ro:** Không có quy tắc rủi ro hay blackout nào bị vô hiệu hóa.
- **Telegram Live:** Tách biệt hoàn toàn qua cờ `--telegram-live`; chế độ mặc định chỉ sử dụng boundary mock an toàn.

---
*Báo cáo được sinh tự động bởi `python -m lab.v9_full_runner`.*