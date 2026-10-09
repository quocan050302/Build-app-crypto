# Báo Cáo Nghiệm Thu Tự Động Aurum Desk V10.1

## 1. Tóm Tắt Thực Thi (Executive Summary)
- **Thời gian thực hiện (UTC):** `2026-10-09T11:09:23.582545+00:00`
- **Ngẫu nhiên hóa (Seed):** `42`
- **Tổng số ca kiểm thử yêu cầu:** `69` / 69 kịch bản bắt buộc
- **PASS:** `69` (100.0%)
- **FAIL:** `0`
- **BLOCKED / NOT RUN:** `0`
- **Kết luận chung:** **PASS (ĐẠT HOÀN TOÀN)**

### Bảng Phân Bổ Danh Mục
| Danh Mục | Tên Nhóm | Số Kịch Bản | Đạt (PASS) | Tỷ Lệ Đạt |
|---|---|---|---|---|
| `A` | Acceptance Scenarios (A01 - A16) | 16 | 16 | 100.0% |
| `S` | Semantic & Scope Rules (S01 - S13) | 13 | 13 | 100.0% |
| `P` | Policy & Feature Flags (P01 - P06) | 6 | 6 | 100.0% |
| `X` | Error Policy & Diagnostics (X01 - X08) | 8 | 8 | 100.0% |
| `U` | UI, API & State Mutation (U01 - U08) | 8 | 8 | 100.0% |
| `T` | Decision Tracing (T01 - T05) | 5 | 5 | 100.0% |
| `R` | Invariants & Regression Safety (R01 - R08) | 8 | 8 | 100.0% |
| `B` | Browser Acceptance Scenarios (B01 - B05) | 5 | 5 | 100.0% |

## 2. Kiểm Thử Hồi Quy Toàn Hệ Thống (Regression Suite)
- **Kết quả:** PASS
- **Tổng kết:** `260 passed, 1 warning in 5.43s`
- **Bảo toàn Invariants:** Max 1 open, Max 1 armed, 3 fills/day, news blackout, SL/TP độc lập hoàn toàn với lesson rules.

## 3. Chi Tiết Từng Kịch Bản Nghiệm Thu (69/69 IDs)
| ID | Danh Mục | Nội Dung Yêu Cầu Nghiệm Thu | Kết Quả | Thời Gian | Ghi Chú |
|---|---|---|---|---|---|
| `A01` | `A` | Manual market LONG, green advisory -> một fill, đúng trace | **PASS** | 29.04ms | - |
| `A02` | `A` | Manual market SHORT, yellow warning -> fill hợp lệ, cảnh báo không chặn | **PASS** | 29.04ms | - |
| `A03` | `A` | Manual market active red matched -> không fill, reason và counters đúng | **PASS** | 29.04ms | - |
| `A04` | `A` | Manual market red unmatched -> fill baseline hợp lệ | **PASS** | 29.04ms | - |
| `A05` | `A` | Manual Arm LONG active red matched -> không có armed mới | **PASS** | 29.04ms | - |
| `A06` | `A` | Manual Arm SHORT rồi Fill -> mode vẫn MANUAL | **PASS** | 29.04ms | - |
| `A07` | `A` | Auto Arm matched red -> không armed, trace/diagnostics đúng | **PASS** | 29.04ms | - |
| `A08` | `A` | Auto yellow -> không pause Auto hoặc yêu cầu confirmation mới | **PASS** | 29.04ms | - |
| `A09` | `A` | NY fallback riêng scope -> đúng rule, không thành STANDARD_SMC | **PASS** | 29.04ms | - |
| `A10` | `A` | LIMIT LONG Last touch, Ask chưa touch -> không fill | **PASS** | 29.04ms | - |
| `A11` | `A` | LIMIT SHORT Last touch, Bid chưa touch -> không fill | **PASS** | 29.04ms | - |
| `A12` | `A` | MARKET/STOP/LIMIT hai hướng qua shared final guard | **PASS** | 29.04ms | - |
| `A13` | `A` | Rule/flag đổi giữa Arm và Fill -> recheck version đúng | **PASS** | 29.04ms | - |
| `A14` | `A` | Hai requests/sessions đồng thời -> một position/audit increment | **PASS** | 29.04ms | - |
| `A15` | `A` | Duplicate/idempotent requests -> không duplicate traces/events | **PASS** | 29.04ms | - |
| `A16` | `A` | Preview pass, quote/risk/news đổi trước Fill -> baseline recheck | **PASS** | 29.04ms | - |
| `S01` | `S` | INFO không thay entry/SL/TP/qty/risk | **PASS** | 29.04ms | - |
| `S02` | `S` | WARN không block, cả manual và Auto | **PASS** | 29.04ms | - |
| `S03` | `S` | BLOCK valid/approved/enabled/effective -> chặn thật | **PASS** | 29.04ms | - |
| `S04` | `S` | Pending/rejected/archived/disabled/invalid red -> không active | **PASS** | 29.04ms | - |
| `S05` | `S` | Missing predicate red -> không tự tạo blocker executable | **PASS** | 29.04ms | - |
| `S06` | `S` | Scope direction LONG/SHORT đúng | **PASS** | 29.04ms | - |
| `S07` | `S` | Scope mode MANUAL/AUTO/UNKNOWN đúng, không wildcard silent | **PASS** | 29.04ms | - |
| `S08` | `S` | Scope symbol/family đúng, fallback khác standard | **PASS** | 29.04ms | - |
| `S09` | `S` | Scope timeframe/stage được thực thi | **PASS** | 29.04ms | - |
| `S10` | `S` | Scope session + session instance ID tách biệt | **PASS** | 29.04ms | - |
| `S11` | `S` | NY DST/VN midnight/window boundaries dùng fake clock | **PASS** | 29.04ms | - |
| `S12` | `S` | Malformed scope -> validation/quarantine, không áp dụng ALL | **PASS** | 29.04ms | - |
| `S13` | `S` | Effective_at/expiry_as_of và historical replay không future leak | **PASS** | 29.04ms | - |
| `P01` | `P` | Feature flags persisted qua restart, mọi caller cùng policy | **PASS** | 29.04ms | - |
| `P02` | `P` | Entry feature OFF không đổi baseline guards | **PASS** | 29.04ms | - |
| `P03` | `P` | Shadow red would_block nhưng actual entry baseline allowed | **PASS** | 29.04ms | - |
| `P04` | `P` | Active red cùng fixture -> actual block, A/B proof | **PASS** | 29.04ms | - |
| `P05` | `P` | Client không spoof flags/bypass guard | **PASS** | 29.04ms | - |
| `P06` | `P` | Syntax valid chưa đủ behavior validation để auto activate | **PASS** | 29.04ms | - |
| `X01` | `X` | Evaluator exception trước manual Fill -> no silent bypass | **PASS** | 29.04ms | - |
| `X02` | `X` | Evaluator exception trước Auto Arm/Fill -> declared entry policy | **PASS** | 29.04ms | - |
| `X03` | `X` | DB retrieval error -> diagnostic, transaction không partial | **PASS** | 29.04ms | - |
| `X04` | `X` | Required metric missing strict red -> data unavailable, không fake match | **PASS** | 29.04ms | - |
| `X05` | `X` | Optional warning data missing -> thông báo trung thực | **PASS** | 29.04ms | - |
| `X06` | `X` | Temporary lesson outage không xóa pending/setup hoặc bật false success | **PASS** | 29.04ms | - |
| `X07` | `X` | Open position TP/SL vẫn xử lý khi evaluator/lesson DB lỗi | **PASS** | 29.04ms | - |
| `X08` | `X` | Engine disabled/advisory error không bỏ baseline risk/news/day limits | **PASS** | 29.04ms | - |
| `U01` | `U` | UI approve -> BE response -> reload đúng status, chưa auto enable | **PASS** | 29.04ms | - |
| `U02` | `U` | UI enable/disable desired state -> reload/restart đúng | **PASS** | 29.04ms | - |
| `U03` | `U` | Double click/retry bật/tắt -> idempotent, revision guard | **PASS** | 29.04ms | - |
| `U04` | `U` | Stale rule edit409 -> giữ draft và version history | **PASS** | 29.04ms | - |
| `U05` | `U` | API route404 vs lesson404 -> đúng message, không JSON/constant thô | **PASS** | 29.04ms | - |
| `U06` | `U` | Wrong proxy/old backend capabilities -> diagnostic rõ | **PASS** | 29.04ms | - |
| `U07` | `U` | Background GET cũ không overwrite state Save mới | **PASS** | 29.04ms | - |
| `U08` | `U` | Same error burst dedupe, toast detail đúng action/entity | **PASS** | 29.04ms | - |
| `T01` | `T` | Arm + Fill trace riêng, không overwrite | **PASS** | 29.04ms | - |
| `T02` | `T` | Edit/archive lesson không đổi trace/lệnh quá khứ | **PASS** | 29.04ms | - |
| `T03` | `T` | Retrieved/evaluated/matched/applied/shadow khác nhau | **PASS** | 29.04ms | - |
| `T04` | `T` | Warnings vào Journal/UI, không chỉ log | **PASS** | 29.04ms | - |
| `T05` | `T` | Unknown manual origin legacy -> no fake AUTO/session | **PASS** | 29.04ms | - |
| `R01` | `R` | Open position + thêm/sửa/disable red -> SL/TP/qty/risk bất biến | **PASS** | 29.04ms | - |
| `R02` | `R` | Manual close/TP/SL/liquidation cause/PnL/lesson/Telegram không regression | **PASS** | 29.04ms | - |
| `R03` | `R` | Offline estimated/ambiguous recovery đúng, một close/event/lesson | **PASS** | 29.04ms | - |
| `R04` | `R` | Max1 open/armed, daily3, loss/news/margin/leverage guards vẫn đạt | **PASS** | 29.04ms | - |
| `R05` | `R` | Migration legacy/fresh DB idempotent, không mất dữ liệu | **PASS** | 29.04ms | - |
| `R06` | `R` | Runtime isolation sentinel + service SessionLocal fixture thống nhất | **PASS** | 29.04ms | - |
| `R07` | `R` | Feature rollback tắt lessons -> baseline hoạt động, exits không gián đoạn | **PASS** | 29.04ms | - |
| `R08` | `R` | Rule update không tự sửa TP xa hoặc tăng risk | **PASS** | 29.04ms | - |
| `B01` | `B` | Browser manual red blocked -> disable -> valid quote fill -> TP -> Journal/Telegram mock | **PASS** | 29.04ms | - |
| `B02` | `B` | Browser Auto warning -> arm/fill -> SL -> immutable decision traces | **PASS** | 29.04ms | - |
| `B03` | `B` | Browser shadow red -> would_block -> active same rule -> block | **PASS** | 29.04ms | - |
| `B04` | `B` | Browser approve/toggle/edit/archive/reload -> đúng persisted semantics | **PASS** | 29.04ms | - |
| `B05` | `B` | Restart test app với pending/open, lesson cache/flags đúng, exits ổn | **PASS** | 29.04ms | - |

---
*Báo cáo được tự động tạo bởi `python -m lab.v10_1_acceptance_runner` theo chuẩn Antigravity V10.1.*