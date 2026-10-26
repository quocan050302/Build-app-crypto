# V14.2 — Entry Guards: LONG/SHORT · R:R · Bài học · Tin đỏ · UI gọn

Branch `fix/v14-1-rr-flow` (V14.2 commit sits on top of the V14.1 branch). Base `a47c35b9be95533bdb82a83908dd7d513935aa9f`.
Implementation: **COMPLETED**. Acceptance: see `logs/acceptance/v14_2/acceptance_v14_2.json` (status is recorded there, separate from implementation).

## 1. Vấn đề → hành vi đã sửa → hàm → test

| Vấn đề | Hành vi sau V14.2 | Hàm chính | Test |
|---|---|---|---|
| Ảnh SHORT 4198.29 / SL 4203.29 / TP 4198.00 trông như cơ hội. Hình học **đúng** (TP < entry < SL); box lời nhỏ cũng **đúng** (reward 0.29). Vấn đề là kế hoạch tạm, net reward âm (≈ −0.92 USDT, net R:R ≈ −0.37), hiển thị giống cơ hội thực thi. | Kế hoạch tạm → `plan_kind=PROVISIONAL`, UI ghi *Điểm vào chưa xác nhận*; economic gate trả `NET_REWARD_NON_POSITIVE`/`INSUFFICIENT_RR`; **TP/qty/leverage không bị kéo cho đẹp**. | `entry_guard_service._check_financial`, `entryGate.deriveEntryDecisionView` | `test_short_photo_fixture_*`, `test_tight_target_plan_is_blocked_*` (LONG+SHORT), e2e #2 |
| Mỗi đường vào lệnh tự kiểm một kiểu; manual MARKET không qua news/policy chung. | Một guard chung cho ARM/FILL; manual MARKET kiểm **trong broker** (gọi trực tiếp cũng bị chặn) và **trước** khi ghi order (không có order mồ côi). | `evaluate_entry_guard`, `PaperBroker.execute_market_order` | `test_market_*`, `test_direct_broker_call_*` |
| Tin đỏ: `check_news_blackout` không lọc currency, trả event đầu query, policy inactive bỏ qua tin. | `NewsBlackoutView` có cấu trúc (USD High + Fed/FOMC không rõ currency; cửa sổ inclusive; remaining = ceil; chồng lấn → lấy end muộn nhất; known_at/received_at tương lai bị bỏ qua); wrapper tuple giữ nguyên; policy inactive vẫn chặn tin. | `news_blackout_service`, `trading_policy_service._news_gate` | `test_v14_2_news_blackout.py`, `test_policy_*` |
| Tin xuất hiện **sau** khi arm vẫn khớp. | Trước `execute_fill` kiểm lại tin/bài học đỏ/RR thực/tài khoản; nếu bị chặn vĩnh viễn → `execute_reject` đúng 1 lần (1 `order.rejected`, 0 `trade.opened`, `fills_count` không tăng). Quote stale/chưa chạm trigger → giữ pending, không ghi gì. | `execution_coordinator.evaluate_orders_sync` | `test_news_after_arm_*`, `test_stale_quote_*`, e2e #9 |
| Bài học đỏ bật sau arm / scope / shadow. | Kiểm lại BEFORE_FILL theo mode thật của order (AUTO/MANUAL, direction, family); rule disabled/unapproved/archived/invalid/shadow/tắt entry-rules không chặn; vàng cảnh báo nhưng vẫn khớp; trace BEFORE_FILL lưu rule id, tách khỏi trace BEFORE_ARM. | `EntryDecisionService.record_stage_decision` | `test_red_rule_*`, `test_rule_*`, `test_yellow_*` |
| WAITING_RETRACE suy ra "đã xác nhận" từ `h1_alignment`. | Chỉ arm khi có chuỗi sweep → displacement → FVG trong `StrategyEvidence`; thiếu → `EVIDENCE_UNAVAILABLE`; chỉ tạo LIMIT (client gửi MARKET vẫn thành LIMIT). | `eligibility_service` | `test_waiting_retrace_*` |
| Quote: `getattr(quote,'age_ms',0)` luôn 0 → quote cũ qua cổng; broker nhận bid/ask thô. | Tuổi quote = `freshness_sec*1000` (hữu hạn), broker nhận `validation.bid/ask`; stale/NaN/inverted không khớp. | `quote_age_ms_of`, `create_paper_order` | `test_market_rejects_invalid_quotes` |
| Sidebar có thể dùng eligibility cũ của setup khác. | Chỉ dùng eligibility khi binding khớp (setup, instance, version, direction, giá đã resolve); lệch → *STALE_ELIGIBILITY*, CTA tắt; R:R có dấu (không `||`/`>0` che số âm). | `entryGate.isEligibilityBoundToIntent`, `QuickDecisionSidebar` | `src/v14_2_entry_gate.test.ts` (22), e2e #1–#7 |
| Race giữa manual / auto / fill. | `entry_mutation_lock` (RLock) bọc manual arm, auto arm, market, fill; **không** bọc exit. | `serialize_entry_mutation` | `test_concurrent_market_orders_open_exactly_one_position` (2 session SQLite file độc lập + barrier), `test_two_pending_triggers_fill_only_one` |

Không đổi: công thức `calculate_risk_reward`, hình học LONG/SHORT, `resolve_plan_levels`, chart/primitive/drag/readOnly V14.1, exits (`process_exit_tick`, `execute_close`).
`test_open_position_still_exits_during_blackout_and_red_lesson` và `test_manual_close_still_works_during_blackout` chứng minh vị thế mở vẫn được bảo vệ.

## 2. Bằng chứng (môi trường của chính lần chạy này)

| Suite | Lệnh | Kết quả | Log |
|---|---|---|---|
| Backend baseline (a47c35b, worktree sạch) | `PYTHONPATH=backend backend/venv/bin/python -m pytest backend/tests -q` | **2 failed, 646 passed, 4 skipped**, 238.82 s, rc=1 | `baseline_backend_full.log` |
| Backend sau V14.2 | cùng lệnh, `--deselect` đúng 2 test baseline-fail | **778 passed, 2 deselected**, 197.35 s, rc=0 | `final_backend_full.log` |
| Frontend unit | `npm test` | **152 passed** (18 files) rc=0 (baseline 130) | `final_frontend_test.log` |
| Frontend build | `npm run build` (tsc -b && vite build) | rc=0 (chỉ cảnh báo kích thước chunk) | `final_frontend_build.log` |
| Frontend lint | `npm run lint` | 15 warnings, 0 errors (bằng baseline) rc=0 | `final_frontend_lint.log` |
| Browser toàn App | `npx playwright test` (Google Chrome thật, backend cách ly) | **9 passed**, rc=0 | `final_e2e.log`, `playwright_report.json` |

Hai test fail ở baseline (`test_v124_01_canonical_ledger_schema_and_export`: tiền tố `post-` vs `POST-`; `test_v13_5_integrity_acceptance::test_01_integrity_pass_on_valid_inputs`: integrity FAIL) thuộc nhánh replay đang tạm hoãn, cũng fail trên a47c35b sạch; V14.2 không sửa chúng.

### Fixture cũ phải sửa (đầu vào hợp lệ, không nới guard, không xóa assertion)
Guard yêu cầu quote tươi nên các test arm/eligibility không còn dựa vào `collector_service.latest_ticker` rò rỉ giữa test: thêm fixture `fresh_ticker` (conftest) cho `test_v5_1…::accepts_valid_short`, `test_v6_1…::shared_eligibility_valid_setup`, `test_v6_error…::accepts_custom_chart_levels`, `test_v10_1::a08`, `test_v10_2::e01–e04`, `test_v10_3::f01`. `test_v10_1::a07`: TP 2685→2682 để net R:R thật (≈2.42) qua min 2.0 nhưng vi phạm rule 2.5 → khối do rule bài học, không do cổng khác. Đã chạy từng file test riêng lẻ để phát hiện phụ thuộc rò rỉ.

## 3. Giới hạn (có bằng chứng)

* **Kéo-thả SL/TP trên canvas** chưa có trong e2e toàn App (không dựng được tọa độ ổn định); vẫn dựa vào test V14.1 và harness `rr-chart-harness.html` (chỉ kiểm primitive). Preview chậm và focus/copy không mutate **đã** được kiểm bằng browser.
* **Không có e2e cho drawer bài học/tin** (chip và nút có, nhưng test không click mở drawer). Màn hình thấp đã kiểm bằng resize 1280×640, không phải ảnh chụp đối chiếu.
* **Khóa chỉ là process-local** (`threading.RLock`): an toàn cho 1 worker uvicorn; nhiều worker cần ràng buộc DB-level (chưa làm). Test race dùng SQLite file + 2 session độc lập.
* **Migration**: thêm 2 cột nullable `watch_setups.plan_kind`, `direction_source` bằng `ALTER TABLE` khi import `database`; app đang chạy (uvicorn `--reload`) sẽ tự thêm vào DB runtime. Backward compatible, chưa được test trên bản sao DB thật.
* Trace `entry_gate` được lưu additive trong `fill/arm_decision_snapshot` (history entry), không có cột riêng; lệnh cũ không có trace → UI không dựng lại.
* Macro/NewsResearch **không** dự đoán hướng; blackout là safety rule. Calendar rỗng hiển thị `Chưa xác nhận dữ liệu lịch` (cảnh báo), không hard-fail (`require_calendar_coverage` chưa thêm).
* `backend/lab/*` seed order trực tiếp, không được nối vào luồng vào lệnh live và không bị sửa; cách ly DB của Lab không được kiểm lại ở V14.2.
* Playwright dùng Chrome cài sẵn (`channel: chrome`) và `../backend/venv/bin/python` (mac); đổi bằng `AURUM_E2E_CHANNEL/BROWSER_PATH/PYTHON`.
* Nhà cung cấp phân tích thật không được dùng trong e2e (backend cách ly không có vòng lặp nền, không mạng, không Telegram); không có khẳng định "no-write" cho pipeline chất lượng với provider thật.
* Không đo/claim winrate hay lợi nhuận.
