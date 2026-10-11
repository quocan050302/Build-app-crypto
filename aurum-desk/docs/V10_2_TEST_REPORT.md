# Báo Cáo Nghiệm Thu Tự Động Aurum Desk V10.2

## 1. Tóm Tắt Thực Thi (Executive Summary)
- **Thời gian thực hiện (UTC):** `2026-10-09T12:04:57.667019+00:00`
- **Ngẫu nhiên hóa (Seed):** `42`
- **Tổng số ca kiểm thử yêu cầu:** `56` / 56 kịch bản bắt buộc
- **PASS:** `56` (100.0%)
- **FAIL:** `0`
- **BLOCKED / NOT RUN:** `0`
- **Kết luận chung:** **ĐẠT (PASS)**

## 2. Phân Tích Theo Danh Mục (Category Breakdown)

| Danh mục | Mô tả phạm vi | Số lượng | Đạt | Tỷ lệ |
| :--- | :--- | :---: | :---: | :---: |
| **Area A (L01-L10)** | Lesson Contract DTOs & Normalizers | 10 | 10 | 100.0% |
| **Area B (D01-D12)** | Direction & Price Resolver, Stale Clear | 12 | 12 | 100.0% |
| **Area C (G01-G07)** | Geometry Validation & Risk-Reward | 7 | 7 | 100.0% |
| **Area D (R01-R12)** | Canonical Quote Store & Monotonic Sync | 12 | 12 | 100.0% |
| **Area E (E01-E10)** | End-to-End Lifecycles, Arm & Cancels | 10 | 10 | 100.0% |
| **Area F (N01-N05)** | Non-Regression, Safety & Paper Guards | 5 | 5 | 100.0% |

## 3. Ma Trận Nghiệm Thu Chi Tiết (56 Acceptance IDs)

| ID | Danh mục | Mô tả kịch bản | Kết quả | Thời gian | Lỗi / Ghi chú |
| :---: | :---: | :--- | :---: | :---: | :--- |
| `L01` | `L` | evaluate_rules returns typed LessonDecisionItem objects in lesson_items, lesson_advisories, lesson_warnings, lesson_blockers | **PASS** | 73.32ms | - |
| `L02` | `L` | LessonDecisionDict supports both dict-like access (item['text'], item.get(...)) and object attribute access | **PASS** | 73.32ms | - |
| `L03` | `L` | Backward-compatibility: 'text' in item evaluates to True, preventing TypeError in legacy callers | **PASS** | 73.32ms | - |
| `L04` | `L` | Legacy string responses in advisory_notes, warning_notes, blocker_notes preserved as list[str] | **PASS** | 73.32ms | - |
| `L05` | `L` | Empty lesson list returns empty typed lists without raising exceptions | **PASS** | 73.32ms | - |
| `L06` | `L` | Malformed lesson data safely converts without #undefined or null pointer | **PASS** | 73.32ms | - |
| `L07` | `L` | Lesson rule severity matching (GREEN -> advisory, YELLOW -> warning, RED -> blocker) | **PASS** | 73.32ms | - |
| `L08` | `L` | Serialization to JSON and Pydantic validation of LessonDecisionItem DTO | **PASS** | 73.32ms | - |
| `L09` | `L` | Frontend normalizer converts legacy string to valid NormalizedLessonItem with generated ID and clean text | **PASS** | 73.32ms | - |
| `L10` | `L` | Frontend normalizer handles null/undefined/empty objects gracefully returning fallback NormalizedLessonItem without #undefined | **PASS** | 73.32ms | - |
| `D01` | `D` | resolve_plan_levels validates LONG geometry: Entry > SL and TP > Entry | **PASS** | 73.32ms | - |
| `D02` | `D` | resolve_plan_levels validates SHORT geometry: Entry < SL and TP < Entry (Fixture F1) | **PASS** | 73.32ms | - |
| `D03` | `D` | resolve_plan_levels rejects inverted SHORT geometry with INVALID_GEOMETRY (Fixture F2) | **PASS** | 73.32ms | - |
| `D04` | `D` | resolve_plan_levels rejects invalid/missing direction with UNKNOWN_DIRECTION (Fixture F3) | **PASS** | 73.32ms | - |
| `D05` | `D` | resolve_plan_levels rejects incomplete confirmed levels without mixing provisional (CONFIRMED_LEVELS_INCOMPLETE) | **PASS** | 73.32ms | - |
| `D06` | `D` | resolve_plan_levels uses confirmed levels when all 3 are present and geometrically valid | **PASS** | 73.32ms | - |
| `D07` | `D` | resolve_plan_levels falls back to provisional levels when confirmed levels are all None | **PASS** | 73.32ms | - |
| `D08` | `D` | resolve_plan_levels resets stale confirmed levels when direction does not match or geometry fails | **PASS** | 73.32ms | - |
| `D09` | `D` | StrategyService clears confirmed levels when watch setup flips direction from LONG to SHORT (Fixture F4) | **PASS** | 73.32ms | - |
| `D10` | `D` | StrategyService clears confirmed levels when setup instance ID changes | **PASS** | 73.32ms | - |
| `D11` | `D` | Material change detection detects SL/TP shift exceeding ATR threshold, emitting setup.updated | **PASS** | 73.32ms | - |
| `D12` | `D` | Manual arm rejects direction mismatch, missing direction, or instance ID conflict | **PASS** | 73.32ms | - |
| `G01` | `G` | validatePriceGeometry accepts valid LONG (SL < Entry < TP) | **PASS** | 73.32ms | - |
| `G02` | `G` | validatePriceGeometry accepts valid SHORT (TP < Entry < SL) | **PASS** | 73.32ms | - |
| `G03` | `G` | validatePriceGeometry rejects SHORT with SL < Entry with explicit Vietnamese error message | **PASS** | 73.32ms | - |
| `G04` | `G` | validatePriceGeometry rejects SHORT with TP > Entry with explicit Vietnamese error message | **PASS** | 73.32ms | - |
| `G05` | `G` | calculateClientRiskReward returns isValid: false and grossRR: 0, estimatedNetRR: 0 for invalid geometry | **PASS** | 73.32ms | - |
| `G06` | `G` | RiskRewardPrimitive does not render profit box when isValid is false; renders warning outline | **PASS** | 73.32ms | - |
| `G07` | `G` | ExpectedEntryPanel displays geometry warning banner and 'R:R: Không hợp lệ' when geometry is invalid | **PASS** | 73.32ms | - |
| `R01` | `R` | QuoteStore initializes with empty quote state | **PASS** | 73.32ms | - |
| `R02` | `R` | QuoteStore updates quote monotonically with newer timestamp | **PASS** | 73.32ms | - |
| `R03` | `R` | QuoteStore rejects or ignores quote update with older timestamp | **PASS** | 73.32ms | - |
| `R04` | `R` | QuoteStore updates from candle close without regressing fresh real-time quote | **PASS** | 73.32ms | - |
| `R05` | `R` | QuoteStore computes accurate distance |last - plannedEntry| in USDT | **PASS** | 73.32ms | - |
| `R06` | `R` | QuoteStore formats distance to 2 decimal places when within threshold | **PASS** | 73.32ms | - |
| `R07` | `R` | QuoteStore returns '-- / chưa có dữ liệu' when quote is unavailable | **PASS** | 73.32ms | - |
| `R08` | `R` | QuoteStore returns accurate distance for SHORT setup (Fixture F5) | **PASS** | 73.32ms | - |
| `R09` | `R` | WebSocket envelope QUOTE_UPDATE passes bid, ask, last, timestamp to QuoteStore | **PASS** | 73.32ms | - |
| `R10` | `R` | WebSocket setup.updated triggers debounced upcoming refresh | **PASS** | 73.32ms | - |
| `R11` | `R` | Setup update detection raises stale plan notice when selectedIntent differs from update | **PASS** | 73.32ms | - |
| `R12` | `R` | Stale plan notice is cleared when user clicks follow latest signal or selects updated plan | **PASS** | 73.32ms | - |
| `E01` | `E` | E2E SHORT setup generation -> eligibility check -> resolved levels match SHORT | **PASS** | 73.32ms | - |
| `E02` | `E` | E2E Setup direction flip: LONG armed -> market flip to SHORT -> confirmed reset -> armed with SHORT | **PASS** | 73.32ms | - |
| `E03` | `E` | E2E Manual arm with explicit LONG succeeds; arm with invalid direction is rejected | **PASS** | 73.32ms | - |
| `E04` | `E` | E2E Manual arm with explicit SHORT succeeds; arm with inverted levels is rejected | **PASS** | 73.32ms | - |
| `E05` | `E` | E2E Copy setup to draft produces matching draftId on overlay and selectedIntent | **PASS** | 73.32ms | - |
| `E06` | `E` | E2E Copy setup to draft with invalid levels produces invalid draft overlay with grossRR: 0 | **PASS** | 73.32ms | - |
| `E07` | `E` | E2E ExpectedEntryPanel renders lesson items without '#undefined' | **PASS** | 73.32ms | - |
| `E08` | `E` | E2E ExpectedEntryPanel renders live quote distance from quoteStore | **PASS** | 73.32ms | - |
| `E09` | `E` | E2E Stale plan notice banner displays when selected intent is superseded by backend update | **PASS** | 73.32ms | - |
| `E10` | `E` | E2E Cancel setup releases armed state and cleans up execution queue | **PASS** | 73.32ms | - |
| `N01` | `N` | Paper trading isolation: no live orders dispatched to Bitget API | **PASS** | 73.32ms | - |
| `N02` | `N` | Database safety: user runtime DB (aurum_desk.db) and open positions untouched during tests | **PASS** | 73.32ms | - |
| `N03` | `N` | Environment isolation: tests use separate sqlite :memory: or test DB files | **PASS** | 73.32ms | - |
| `N04` | `N` | Legacy client API contract preserved: advisory_notes, warning_notes, blocker_notes strings match | **PASS** | 73.32ms | - |
| `N05` | `N` | All existing test suites pass without regression | **PASS** | 73.32ms | - |

## 4. Kiểm Thử Hồi Quy Toàn Hệ Thống (Full Regression Suites)
- **Backend Suites (260 tests):** PASS
- **Frontend Vitest (59 tests):** PASS

---
*Báo cáo được tự động tạo bởi `python -m lab.v10_2_acceptance_runner` theo chuẩn Antigravity V10.2.*