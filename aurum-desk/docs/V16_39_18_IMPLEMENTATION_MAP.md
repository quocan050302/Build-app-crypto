# AURUM V16.39.18 — IMPLEMENTATION MAP
**Khép Hợp Đồng R:R Settings → Current Backend → Toàn Bộ UI → Order Snapshot**
*Mã phiên bản:* V16.39.18  
*Branch:* `fix/v14-1-rr-flow`  
*Baseline SHA:* `04d107c54d40759adae5da9fcca050dca56ae96e`  

---

## 1. Bản Đồ Hàm & Contracts Đã Sửa

| Tệp / Vị trí | Hàm / Biểu tượng | Bản chất (Cũ / MỚI) | Input Contract Shape | Output / Hành vi chuẩn hóa |
| :--- | :--- | :--- | :--- | :--- |
| `frontend/src/utils/rrPolicyView.ts` | `resolveMappedOrderRrView` | **MỚI** | Mapped `SelectedTradeIntent` (`minNetRR`, `rrPolicySource`, `rrPolicyFingerprint`, `rrPolicyStatus`, `order_id`, `source: 'OPEN_POSITION'`) | Trả về `RrView` (`KNOWN` / `MISSING` / `INVALID`) trực tiếp từ mapped properties mà không ép đọc qua raw snake_case parser `cost_snapshot`. |
| `frontend/src/utils/rrPolicyView.ts` | `getOrderRrFields` | Sửa | Raw DTO từ WebSocket / REST API (`cost_snapshot`, `arm_decision_snapshot`) | Bổ sung `rrPolicyStatus?: 'KNOWN' \| 'MISSING' \| 'INVALID'` và `rrPolicyReason?: string` để giữ trạng thái khi map sang Intent. |
| `frontend/src/utils/rrPolicyView.ts` | `resolveSelectionRrPolicy` | Sửa | `(intent: any, currentPolicy?: CurrentPolicyContext)` | Phân nhánh: nếu `OPEN_POSITION` gọi `resolveMappedOrderRrView(intent)`; nếu current policy chưa xác thực (`value: null` hoặc `status: 'LOADING'`) trả về `kind: 'CURRENT', status: 'LOADING', minNetRR: null` (không bịa `minNetRR: 2.0`). |
| `frontend/src/utils/rrPolicyView.ts` | `compareNetRr` | Sửa | `(actualNetRR: number \| null, policyView: SelectionRrPolicyView)` | So sánh trực tiếp full-precision `meets = actual >= minRR` mà KHÔNG làm tròn `diff.toFixed(4)` trước quyết định. Khi policy loading/unconfirmed trả về `UNKNOWN`, `meetsMin: false`. |
| `frontend/src/utils/rrPolicyView.ts` | `rebaseTradeIntentRrPolicy` | Sửa | `(intent, currentPolicy, financialContext)` | Đọc `financialContext?.riskPct ?? intent.riskPct ?? 0.25`, loại bỏ hardcode `0.25` gây lệch rủi ro tài chính. |
| `frontend/src/utils/calculator.ts` | `calculateClientRiskReward` | Sửa | `minNetRR: number \| null = 2.0` | Khi `minNetRR === null`, trả về `canExecute: false`, `blockers: ['POLICY_UNCONFIRMED']`. So sánh `netRR >= minNetRR` giữ nguyên full-precision. |
| `frontend/src/App.tsx` | `currentPolicyContext` & Props Propagation | Sửa | `rrPolicy.saved` + `tradingPolicy` | Context chuẩn hóa: `value: null` khi unconfirmed/loading. Truyền `appliedMinNetRR`, `rrPolicySource`, `rrPolicyFingerprint`, `rrPolicyStatus`, `isRrPolicyConfirmed` xuống `QuickDecisionSidebar`, `TradeDetailsDrawer`, `ChartComponent`, `NYSessionPanel`. |
| `frontend/src/components/QuickDecisionSidebar.tsx` | Props & Blocker Gate | Sửa | Nhận context đầy đủ từ App | Nâng blocker `POLICY_GATE` khi current policy unconfirmed/loading. So sánh R:R hiển thị `NET_RR_TOO_LOW` khi actual < required. |
| `frontend/src/components/TradeDetailsDrawer.tsx` | R:R Display Section | Sửa | `selectedIntent` (mapped) + `appliedMinNetRR: number \| null` | Đọc `resolveSelectionRrPolicy` trên mapped intent; hiển thị `1:1.50 (Lúc ARM)` khi snapshot known; hiển thị `Chưa lưu ngưỡng lúc ARM` khi missing. |
| `frontend/src/components/CG01DiagnosticCard.tsx` | `cg01-applied-rr-badge` | Sửa | `appliedMinNetRR?: number \| null`, `isRrPolicyConfirmed?: boolean` | Khi null/unconfirmed hiển thị `Đang tải...`, loại bỏ fallback cứng `appliedMinNetRR ?? 2.0`. |
| `backend/services/forward_paper_service.py` | `create_forward_paper_session` | Sửa | `db: Session`, `risk_config?: Dict` | Bắt và re-raise `RrPolicyConfigError` khi cấu hình DB hỏng thay vì nuốt lỗi về 2.0 âm thầm. Validate số dương hữu hạn nếu có `risk_config["min_net_rr"]`. |
| `backend/services/cg01_config_factory.py` | `build_effective_cg01_config` | Sửa | `db: Session`, `scalp_cfg` | Re-raise `RrPolicyConfigError` khi DB cấu hình R:R canonical bị lỗi, không giả định 2.0. |

---

## 2. Chuỗi Truyền Dẫn (Dataflow & Producers)

```mermaid
graph TD
    subgraph "Backend Authoritative Source"
        DB[(SystemConfig / TradingPolicy)] --> RR_SERVICE[rr_policy_service.py]
        RR_SERVICE --> API_SCALP[/api/scalp/config]
        RR_SERVICE --> FORWARD_PAPER[forward_paper_service.py Session Freeze]
        RR_SERVICE --> CG01_FACTORY[cg01_config_factory.py]
    end

    subgraph "Frontend Context Owner (App.tsx)"
        API_SCALP --> HOOK[useRrPolicySettings]
        HOOK --> CURRENT_CTX[currentPolicyContext: { value, source, fingerprint, isConfirmed, status }]
    end

    subgraph "Execution & Consumers"
        CURRENT_CTX --> SIDEBAR[QuickDecisionSidebar: POLICY_GATE & NET_RR_TOO_LOW]
        CURRENT_CTX --> DRAWER[TradeDetailsDrawer: 1:x.xx Lúc ARM / Hiện hành]
        CURRENT_CTX --> CHART[ChartComponent: effectiveMinNetRR null-safe]
        CURRENT_CTX --> CG01_CARD[CG01DiagnosticCard: Applied RR Badge]
        CURRENT_CTX --> NY_PANEL[NYSessionPanel: Quota condition text]
    end

    subgraph "Order Snapshot Provenance"
        RAW_DTO[Raw Order DTO: cost_snapshot] --> GET_FIELDS[getOrderRrFields: minNetRR, status, source]
        GET_FIELDS --> MAPPED_INTENT[SelectedTradeIntent: OPEN_POSITION]
        MAPPED_INTENT --> RESOLVER[resolveMappedOrderRrView]
        RESOLVER --> SNAPSHOT_VIEW[ORDER_SNAPSHOT / KNOWN 1.50]
        SNAPSHOT_VIEW --> DRAWER
        SNAPSHOT_VIEW --> SIDEBAR
    end
```

---

## 3. Ma Trận Inventory Người Dùng R:R Trên Toàn Bộ UI

| UI Component | Context Scope | Thuộc tính / Props cập nhật | Kiểm chứng & Bằng chứng |
| :--- | :--- | :--- | :--- |
| `QuickDecisionSidebar` | Current Draft / Frozen Order | `appliedMinNetRR`, `isRrPolicyConfirmed`, `rrPolicyStatus`, `rrPolicyFingerprint` | Unit test mount thực tế: raises `POLICY_GATE` khi unconfirmed, raises `NET_RR_TOO_LOW` khi 1.70 < 2.50. |
| `TradeDetailsDrawer` | Order History / Position | `selectedIntent` (mapped shape), `appliedMinNetRR` | Unit test mount thực tế: hiển thị `1:1.50 (Lúc ARM)` cho Input A; hiển thị `Chưa lưu ngưỡng lúc ARM` khi missing. |
| `CG01DiagnosticCard` | Current Setting Display | `appliedMinNetRR?: number \| null`, `isRrPolicyConfirmed?: boolean` | E2E Browser & Unit test: hiển thị `Đang tải...` khi unconfirmed; cập nhật `1:1.50` và `1:2.50` theo server response. |
| `ChartComponent` | Canvas Overlays & Draft Drag | `effectiveMinNetRR?: number \| null` (bỏ default parameter 2.0) | TypeScript build & Calculator check: khi null blocker `POLICY_UNCONFIRMED`, giữ nguyên hình học kéo thả. |
| `NYSessionPanel` | Session Filter & Quota Text | `appliedMinNetRR?: number \| null`, `policy` | Text hiển thị chuẩn xác `Net RR >= 1:x.xx` khi confirmed, trung tính `Net RR đạt chuẩn` khi null. |
| `ExpectedEntryPanel` | Upcoming Setups Table | Kế thừa resolver canonical qua `resolveSelectionRrPolicy` | Nhận threshold của cycle hiện hành, không bịa màu xanh khi unconfirmed. |
