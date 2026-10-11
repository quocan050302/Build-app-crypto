import { describe, it, expect } from "vitest";
import { deriveResearchVerdict } from "./utils/deriveResearchVerdict";
import { getResearchReasonText } from "./utils/researchReasonText";

describe("V13.5 Research Acceptance Tests", () => {
  it("verifies breakeven economic status when net PnL is 0.00", () => {
    const mockResult: any = {
      total_trades: 10,
      total_net_pnl: 0.0,
      max_drawdown_pct: 2.5,
      total_fees: 5.0,
      effective_config: { entry_cadence: "DAILY_PAPER" },
      cadence_summary: { unmet_sessions: 0, coverage_pct: 100 },
      integrity_summary: { status: "PASS" }
    };
    const verdict = deriveResearchVerdict(mockResult);
    expect(verdict!.economicStatus).toBe("BREAKEVEN");
    expect(verdict!.technicalStatus).toBe("PASS");
    expect(verdict!.cadenceStatus).toBe("PASS");
    expect(verdict!.badge).toContain("HÒA VỐN");
  });

  it("verifies technical FAIL when integrity summary fails", () => {
    const mockResult: any = {
      total_trades: 10,
      total_net_pnl: 15.0,
      max_drawdown_pct: 3.0,
      effective_config: { entry_cadence: "DAILY_PAPER" },
      integrity_summary: { status: "FAIL" }
    };
    const verdict = deriveResearchVerdict(mockResult);
    expect(verdict!.technicalStatus).toBe("FAIL");
    expect(verdict!.badge).toContain("CHƯA THỂ ĐÁNH GIÁ");
  });

  it("verifies cadence UNMET when unmet sessions > 0", () => {
    const mockResult: any = {
      total_trades: 20,
      total_net_pnl: 10.0,
      max_drawdown_pct: 3.0,
      total_fees: 8.0,
      effective_config: { entry_cadence: "DAILY_PAPER" },
      cadence_summary: { unmet_sessions: 2, coverage_pct: 96.5 },
      integrity_summary: { status: "PASS" }
    };
    const verdict = deriveResearchVerdict(mockResult);
    expect(verdict!.cadenceStatus).toBe("UNMET");
    expect(verdict!.technicalStatus).toBe("PASS");
  });

  it("verifies friendly reason translations for new traders", () => {
    expect(getResearchReasonText("COOLDOWN_ACTIVE")).toContain("Đang trong thời gian nghỉ");
    expect(getResearchReasonText("NO_VALID_STRUCTURAL_TARGET")).toContain("Chưa tìm được mục tiêu giá");
    expect(getResearchReasonText("OPEN_POSITION")).toContain("vị thế đang mở");
  });
});
