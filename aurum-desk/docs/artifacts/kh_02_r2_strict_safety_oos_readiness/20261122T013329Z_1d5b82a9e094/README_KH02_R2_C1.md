# AURUM DESK — KH-02.R2.C1 | ĐÓNG CỔNG AN TOÀN Ổ ĐĨA NGHIÊM NGẶT & SẴN SÀNG OOS TƯƠNG LAI

- **Phiên bản đặc tả**: `KH-02.R2.C1-v1.0`
- **Run ID**: `20261122T013329Z_1d5b82a9e094`
- **Nhánh bắt buộc**: `feature/v17-9-2-structural-sl-tp-from-qa`
- **Start / End Git HEAD**: `1d5b82a9e0946b02e414e797f61804594645bbb5` (không đổi trong suốt quá trình chạy)
- **Remote origin HEAD**: `9c5b97be5dcbefbb52f8a7a4421683c8b0140d76`
- **Trạng thái cây làm việc**: `git_clean_at_start=True`, `local_uncommitted_c1_changes=False`
- **KH-02.R2 checkpoint trước đó (chỉ đọc, bất biến)**: `HEAD=9c5b97be5dcbefbb52f8a7a4421683c8b0140d76`, `TESTED_SHA=8ca245bcf4d849c44426d047dc025609259bc21d`, `RUN_ID=20261121T154352Z_8ca245bcf4d8`
- **Chế độ an toàn bắt buộc**: `PRODUCTION=OFF | LIVE=OFF | PAPER=OFF | AUTO=OFF | ACTIVATION_READY=NO | OOS_EVALUATIONS=0`

## 1. Bảng 15 Kết Luận Trực Giao (Orthogonal Verdicts — Không Gộp, Không Che Giấu)

| Trục kiểm định | Kết luận | Receipt trên đĩa |
|---|---|---|
| `BRANCH_SOURCE_VERDICT` | **PASS** | `00, 01` |
| `STRICT_DISK_SAFETY_VERDICT` | **BLOCKED_LOW_DISK** | `02, 03, 12` |
| `RAM_PROCESS_SAFETY_VERDICT` | **PASS** | `12` |
| `FULL_REGRESSION_VERDICT` | **NOT_RUN_BLOCKED_LOW_DISK** | `10` |
| `BASELINE_PARITY_VERDICT` | **NOT_RUN_BLOCKED_LOW_DISK** | `14` |
| `ENTRY_TARGET_INTEGRITY_VERDICT` | **PASS** | `06` |
| `PAIRED_RESEARCH_VERDICT` | **IDENTIFIABLE_WITH_PARTIAL_COMMON_SUPPORT** | `07` |
| `COST_RISK_RECONCILIATION_VERDICT` | **PASS** | `08` |
| `DATA_PROVENANCE_VERDICT` | **PASS** | `04, 05` |
| `FUTURE_OOS_READINESS_VERDICT` | **BLOCKED_NO_VERIFIED_NEW_DATA** | `04` |
| `FRESH_OOS_VERDICT` | **BLOCKED_NO_UNTOUCHED_DATA** | `04` |
| `RESEARCH_ECONOMIC_VERDICT` | **NO_PROVEN_EDGE** | `09` |
| `INDEPENDENT_VERIFIER_VERDICT` | **PENDING_SEPARATE_OS_VERIFIER** | `16 (sealed by separate OS process)` |
| `PUBLICATION_VERDICT` | **NOT_PUBLISHED_BLOCKED_LOW_DISK** | `17` |
| `ACTIVATION_READY` | **NO** | `00, 01, 04, 09, 15` |

## 2. Kết Quả Kiểm Tra Ổ Đĩa Đa Phân Vùng & RAM (Stage A — Không Cho Phép Tự Miễn Trừ)

- **Chính sách cứng**: Preflight `>= 20.5 GiB` trên MỌI phân vùng ghi, Runtime `>= 20.0 GiB`, Khuyến nghị `>= 25.0 GiB`. `owner_waiver_permitted = False`.
- **Đo thực tế (đã khử trùng lặp `st_dev`)**: `1` phân vùng vật lý riêng biệt, trống tối thiểu **`7.5191 GiB`** nhị phân (`8.0736 GB` thập phân), thiếu **`12.9809 GiB`** so với sàn cứng `20.5 GiB`.
- **Trạng thái preflight ổ đĩa**: **`BLOCKED_LOW_DISK`** (`can_run_heavy_stages=False`).
- **RAM quan sát trong phiên**: Preflight `4833.31 MiB`, Min runtime `4617.67 MiB` (sàn cứng `>= 512.0 MiB`), Max children `3` (`<= 4`), Orphans `0`, Sentinel temp cleaned `True`.
- **Negative controls chống vượt rào waiver (`03`)**: `12/12 PASS`.

### Kiểm kê dung lượng CHỈ ĐỌC (Không tự động xóa bất kỳ tệp người dùng/dự án nào)

- `autonomous_deletion_executed = False`, `git_gc_executed = False`.
- Lưu ý quan trọng: Project-owned legacy artifacts (~1.6 GiB) alone are smaller than the ~11.4 GiB shortfall to 20.5 GiB. Reaching >= 25.0 GiB advisory (or >= 20.5 GiB strict preflight) requires manual owner cleanup of non-project system/user storage (e.g., macOS Downloads, Docker images, Xcode/Homebrew caches, Time Machine local snapshots).

Lệnh kiểm tra thủ công (KHÔNG tự động chạy):

- `df -h /System/Volumes/Data` — Check real APFS Data volume binary GiB free space (`executed=False`)
- `du -sh ~/Downloads ~/Library/Caches ~/.cache 2>/dev/null | sort -hr` — Inspect user-owned non-project caches & downloads (`executed=False`)
- `tmutil listlocalsnapshots /` — Check if macOS APFS local Time Machine snapshots hold reclaimable blocks (`executed=False`)
- `du -sh aurum-desk/docs/artifacts/* | sort -hr | head -n 15` — Review historical pre-KH artifact folders (do NOT delete locked kh_01*/kh_02* folders) (`executed=False`)

## 3. Tệp & Hàm Đã Sửa / Thêm vs Bất Biến (`KEEP_AS_IS`)

- **Tệp production / KH-01* / KH-02.R1 / bằng chứng R2 bị sửa**: **`0`** (`cost_assumptions_defaults_intact=True`, `forbidden_order_import_hits=0`).
- **Các tệp R2 được cập nhật bắt buộc (P0-A & P0-B)**:
  - `aurum-desk/backend/services/kh_02_r2_research_integrity_service.py`: thêm `probe_r2_c1_write_mounts_strict`, `create_sentinel_owned_temp_dir_r2_c1`, `safe_cleanup_sentinel_owned_temp_dir_r2_c1`, `run_atomic_write_rename_probe_r2_c1`, `build_r2_c1_readonly_disk_inventory`; gỡ bỏ hoàn toàn nhánh chấp nhận waiver trong `evaluate_r2_orthogonal_science_and_engineering_gates_from_disk`.
  - `aurum-desk/tools/run_kh_02_r2_verification.py`: khóa cứng và từ chối `--owner-waive-disk-preflight` (exit code `2`), nâng cấp `_BoundedResourceSampler` giám sát ổ đĩa `< 20.0 GiB` runtime và thu dọn tiến trình con.
  - `aurum-desk/tools/kh_02_r2_independent_verifier.py`: từ chối mọi artifact chứa cờ waiver hoặc trạng thái `WAIVED_BY_OWNER_LOW_DISK` / `PASS_WITH_OWNER_DISK_WAIVER`.
  - `aurum-desk/backend/services/kh_02_r2_prospective_data_service.py`: thêm kiểm định mốc đóng băng R2 (`audit_authoritative_r2_freeze_manifest_r2_c1`), hợp đồng snapshot append-only (`validate_prospective_snapshot_contract_c1`, `validate_prospective_payload_bars_and_quotes_r2_c1`), sổ đăng ký nối băm bất biến (`ProspectiveHashLinkedEscrowRegistryR2C1`), phân tích lực thống kê (`compute_prospective_oos_power_requirements_r2_c1`), giao diện thu thập công khai chỉ đọc tắt mặc định (`ReadOnlyBitgetPublicProspectiveCollectorR2C1`), và bộ 20 bài kiểm tra giả lập (`run_kh02_r2_c1_20_synthetic_oos_ingestion_faults`).
- **Các tệp KH-02.R2.C1 mới thêm**: `tools/run_kh_02_r2_c1_strict_closure.py`, `tools/kh_02_r2_c1_independent_verifier.py`, `backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py`.

## 4. Hồi Quy, Async W1/W2/W4, Parity & Tiêm Lỗi Độc Lập

- **Full repo pytest (`10`)**: **`NOT_RUN_BLOCKED_LOW_DISK`** (`collected=0`, `executed=0`, `zero_placeholder_junit_written=True`).
- **Focused R2+C1 lightweight diagnostic pytest (KHÔNG thay thế full regression)**: `{'executed': True, 'diagnostic_scope_only_not_full_regression': True, 'test_files': ['backend/tests/test_kh_02_r2_research_integrity.py', 'backend/tests/test_kh_02_r2_counterfactual_and_costs.py', 'backend/tests/test_kh_02_r2_oos_provenance_and_faults.py', 'backend/tests/test_kh_02_r2_c1_strict_safety_and_oos.py'], 'exit_code': 0, 'elapsed_sec': 12.401, 'status': 'PASS', 'output_tail': '..................                                                       [100%]\n=============================== warnings summary ===============================\nbackend/venv/lib/python3.13/site-packages/fastapi/testclient.py:1\n  /Users/macbook/Documents/Project_Github/Build-app-crypto/aurum-desk/backend/venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.\n    from starlette.testclient import TestClient as TestClient  # noqa\n\n-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html\n18 passed, 1 warning in 11.27s'}`.
- **Async W1/W2/W4/W1_repeat (`11`)**: **`NOT_RUN_BLOCKED_LOW_DISK`** (không sao chép số đo lịch sử của R2).
- **Production 56,710 A1 / 6,375 SI8 / 246 adapter parity (`14`)**: **`NOT_RUN_BLOCKED_LOW_DISK`** (bất biến SHA256 của dataset, R1 & R2 pointers/manifests: `prior_evidence_immutability_verified=True`).
- **Tiêm lỗi RED/GREEN thực (`13`)**: **`56/56 PASS`** (`42` lỗi R2 kế thừa + `14` lỗi mới của C1).

## 5. Sẵn Sàng Dữ Liệu OOS Tương Lai & Khoa Học Nghiên Cứu (Không Tạo Dữ Liệu Ảo / Không Khẳng Định Lợi Nhuận)

- **Mốc đóng băng R2 giữ nguyên nguyên văn từ artifact `14` của R2**: `frozen_candidate_code_sha=8ca245bcf4d849c44426d047dc025609259bc21d`, `freeze_timestamp_utc=2026-11-21T15:43:52Z`, `embargo=240 phút`, `embargo_cutoff_utc=2026-11-21T19:43:52Z` (`start_utc_ms >= 1795290232732` được chấp nhận, `< 1795290232732` bị từ chối).
- **Snapshot thực tế mới đề xuất / chấp nhận**: `proposed_snapshots_count=0`, `accepted_prospective_snapshots_count=0`, **`OOS_EVALUATIONS=0`**.
- **Dữ liệu hiện hữu**: `REUSED_RESEARCH` (~20.35 ngày lịch M1), `quote_mode=MODELED_BID_ASK` (spread mô phỏng `0.20 USD`, KHÔNG có L1 BBO quan sát), `actual_account_effective_fee_tier=UNKNOWN_NOT_LOCALLY_VERIFIED`.
- **Bộ 20 kiểm thử validator OOS (`05`)**: `20/20 PASS` — **CHỈ là fixture giả lập kiểm chứng validator (`validates_real_market_data=False`), KHÔNG phải dữ liệu thị trường thật**.
- **Lực thống kê OOS**: Cố định `N = 30` lệnh đóng KHÔNG đủ để chứng minh edge (`fixed_30_trades_proves_edge=False`).
- **Kết luận kinh tế (`09`)**: **`NO_PROVEN_EDGE`** (mọi nhánh nghiên cứu trên dữ liệu tái sử dụng R2 đều âm: `B2_H1 -175.4380 USD/-0.9737R`, `B2_H2 -441.6118 USD/-0.8806R`, `B2_H3 -239.6588 USD/-0.8504R`, `B2_H123 -57.6807 USD/-1.0169R`). Rủi ro kế hoạch `<= 2.50 USD` KHÔNG ngăn chặn đuôi lỗ thực tế do gap/trượt giá.

## 6. Trạng Thái Commit / Push & Bước Tiếp Theo Cho Chủ Sở Hữu

- **Publication verdict (`17`)**: **`NOT_PUBLISHED_BLOCKED_LOW_DISK`** (`git_commit_or_push_executed=False`).
- **Lý do chưa commit/push**: Ổ đĩa khả dụng thực tế hiện là `7.5191 GiB < 20.5 GiB` sàn preflight nghiêm ngặt nên các bước nặng (`full pytest`, `W1/W2/W4`, `56,710 parity`) bị chặn an toàn (`BLOCKED_LOW_DISK`). Theo mục §10, KHÔNG commit/push bản xác nhận giả khi cổng ổ đĩa chưa PASS.
- **Cách tiếp tục sau khi giải phóng ổ đĩa lên `>= 25.0 GiB` (tối thiểu `>= 20.5 GiB`)**:
  1. Kiểm tra lại dung lượng thật bằng `df -h /System/Volumes/Data`.
  2. Commit sạch phần mã KH-02.R2.C1 trên nhánh `feature/v17-9-2-structural-sl-tp-from-qa`.
  3. Chạy lại `PYTHONPATH=backend:. backend/venv/bin/python tools/run_kh_02_r2_c1_strict_closure.py` để thực thi đầy đủ `10` (full pytest + JUnit 1:1), `11` (W1/W2/W4), `14` (56,710 parity), và niêm phong `16` bởi tiến trình verifier độc lập.
  4. Chỉ sau khi toàn bộ cổng kỹ thuật/an toàn đều PASS không waiver, mới commit bằng chứng và `git push` + đọc kiểm chứng SHA từ xa.
