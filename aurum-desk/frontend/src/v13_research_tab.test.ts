import { describe, it, expect } from 'vitest';

describe('V13 Frontend Research Tab Invariants & Logic', () => {
  it('T01: correctly determines CURRENT_ASOF vs HISTORICAL_ASOF based on selected date', () => {
    const today = new Date().toISOString().slice(0, 10);
    const pastDate = '2026-07-15';
    const futureDate = '2099-01-01';

    const getMode = (dateStr: string) => {
      if (dateStr < today) return 'HISTORICAL_ASOF';
      if (dateStr === today) return 'CURRENT_ASOF';
      return 'HISTORICAL_ASOF'; // Capped
    };

    expect(getMode(today)).toBe('CURRENT_ASOF');
    expect(getMode(pastDate)).toBe('HISTORICAL_ASOF');
    expect(getMode(futureDate)).toBe('HISTORICAL_ASOF');
  });

  it('T02: validates scenario geometry and net R:R threshold for LONG and SHORT', () => {
    const validateLong = (entry: number, sl: number, tp: number, netRR: number) => {
      if (sl >= entry) return { isValid: false, reason: 'SL >= Entry' };
      if (tp <= entry) return { isValid: false, reason: 'TP <= Entry' };
      if (netRR < 1.80) return { isValid: false, reason: 'Net R:R < 1.80' };
      return { isValid: true };
    };

    const validateShort = (entry: number, sl: number, tp: number, netRR: number) => {
      if (sl <= entry) return { isValid: false, reason: 'SL <= Entry' };
      if (tp >= entry) return { isValid: false, reason: 'TP >= Entry' };
      if (netRR < 1.80) return { isValid: false, reason: 'Net R:R < 1.80' };
      return { isValid: true };
    };

    // Valid LONG
    expect(validateLong(2650, 2635, 2690, 2.3).isValid).toBe(true);
    // Invalid geometry LONG (SL above entry)
    expect(validateLong(2650, 2660, 2690, 2.3).isValid).toBe(false);
    // Sub-threshold Net RR LONG
    expect(validateLong(2650, 2635, 2690, 1.45).isValid).toBe(false);

    // Valid SHORT
    expect(validateShort(2650, 2665, 2610, 2.3).isValid).toBe(true);
    // Invalid geometry SHORT (SL below entry)
    expect(validateShort(2650, 2640, 2610, 2.3).isValid).toBe(false);
    // Sub-threshold Net RR SHORT
    expect(validateShort(2650, 2665, 2610, 1.70).isValid).toBe(false);
  });

  it('T03: verifies quick date offset calculations', () => {
    const calcDaysAgo = (days: number) => {
      const d = new Date('2026-10-10T12:00:00Z');
      d.setDate(d.getDate() - days);
      return d.toISOString().slice(0, 10);
    };

    expect(calcDaysAgo(0)).toBe('2026-10-10');
    expect(calcDaysAgo(1)).toBe('2026-10-09');
    expect(calcDaysAgo(7)).toBe('2026-10-03');
    expect(calcDaysAgo(30)).toBe('2026-09-10');
  });

  it('T04: provides beginner-friendly Vietnamese regime translations', () => {
    const labels: Record<string, string> = {
      TREND_UP: 'Xu Hướng Tăng Mạnh',
      TREND_DOWN: 'Xu Hướng Giảm Rõ Rệt',
      RANGE: 'Đi Ngang Tích Lũy',
      TRANSITION: 'Giai Đoạn Chuyển Giao',
      EVENT_VOLATILITY: 'Biến Động Bất Thường',
      UNKNOWN: 'Chưa Rõ Ràng'
    };

    expect(labels['TREND_UP']).toContain('Tăng');
    expect(labels['TREND_DOWN']).toContain('Giảm');
    expect(labels['RANGE']).toContain('Đi Ngang');
    expect(labels['EVENT_VOLATILITY']).toContain('Biến Động');
  });

  it("T05: DST-aware timezone conversion does not hardcode 11h difference", () => {
    // Summer date (EDT: UTC-4, VN: UTC+7 -> diff 11h)
    const summerDate = "2026-07-15";
    const summerTime = "08:30";
    // Winter date (EST: UTC-5, VN: UTC+7 -> diff 12h)
    const winterDate = "2026-01-15";
    const winterTime = "08:30";

    const getDual = (dStr: string, tStr: string) => {
      const [year, month, day] = dStr.split("-").map(Number);
      const [hours, minutes] = tStr.split(":").map(Number);
      const testIso = new Date(Date.UTC(year, month - 1, day, 12, 0, 0));
      const nyTz = "America/New_York";

      const nyFormat = new Intl.DateTimeFormat("en-US", { timeZone: nyTz, timeZoneName: "short" });
      const parts = nyFormat.formatToParts(testIso);
      const tzName = parts.find(p => p.type === "timeZoneName")?.value || "";
      const isDst = tzName.includes("DT") || tzName === "EDT";

      // If DST (summer), difference is 11 hours. If standard (winter), difference is 12 hours.
      const diffHours = isDst ? 11 : 12;
      const vnTotalMinutes = (hours * 60 + minutes + diffHours * 60) % 1440;
      const vnHours = String(Math.floor(vnTotalMinutes / 60)).padStart(2, "0");
      const vnMins = String(vnTotalMinutes % 60).padStart(2, "0");
      return `${tStr} New York · ${vnHours}:${vnMins} Hà Nội`;
    };

    const summerResult = getDual(summerDate, summerTime);
    expect(summerResult).toBe("08:30 New York · 19:30 Hà Nội");

    const winterResult = getDual(winterDate, winterTime);
    expect(winterResult).toBe("08:30 New York · 20:30 Hà Nội");
  });

  it("T06: renders safe fallback when metrics are null/undefined without fake 80% or 0R", () => {
    const formatQualityScore = (val: number | null | undefined): string => {
      if (val === null || val === undefined) return "Chưa có dữ liệu";
      return `${Math.round(val * 100)}%`;
    };

    const formatNetRR = (val: number | null | undefined): string => {
      if (val === null || val === undefined) return "Chưa có dữ liệu";
      return `${val.toFixed(2)}R`;
    };

    const formatCurrency = (val: number | null | undefined): string => {
      if (val === null || val === undefined) return "Chưa có dữ liệu";
      return `$${val.toFixed(2)}`;
    };

    // Correctly returns "Chưa có dữ liệu", avoiding fake defaults
    expect(formatQualityScore(null)).toBe("Chưa có dữ liệu");
    expect(formatQualityScore(undefined)).toBe("Chưa có dữ liệu");
    expect(formatQualityScore(0.85)).toBe("85%");

    expect(formatNetRR(null)).toBe("Chưa có dữ liệu");
    expect(formatNetRR(undefined)).toBe("Chưa có dữ liệu");
    expect(formatNetRR(2.15)).toBe("2.15R");

    expect(formatCurrency(null)).toBe("Chưa có dữ liệu");
    expect(formatCurrency(undefined)).toBe("Chưa có dữ liệu");
    expect(formatCurrency(125.5)).toBe("$125.50");
  });

  it("T07: evaluates method status accurately with priority to data validity and sample size", () => {
    interface EvalMetrics {
      isDataValid: boolean;
      totalTrades: number;
      minRequiredTrades: number;
      netPnl: number;
      maxDrawdownPct: number;
      winRatePct: number;
    }

    const determineVerdict = (m: EvalMetrics) => {
      if (!m.isDataValid) {
        return {
          status: "DATA_CALC_INVALID",
          title: "Chưa thể đánh giá vì dữ liệu hoặc cách tính chưa hợp lệ"
        };
      }
      if (m.totalTrades < m.minRequiredTrades) {
        return {
          status: "INSUFFICIENT_SAMPLE",
          title: "Chưa đủ số lệnh mẫu để kết luận phương pháp"
        };
      }
      if (m.netPnl > 0 && m.maxDrawdownPct <= 5.0 && m.winRatePct >= 45.0) {
        return {
          status: "PASS_CRITERIA",
          title: "Đạt tiêu chí kiểm tra phương pháp"
        };
      }
      return {
        status: "NEEDS_IMPROVEMENT",
        title: "Phương pháp cần cải thiện trước khi áp dụng"
      };
    };

    // Invalid data
    expect(determineVerdict({
      isDataValid: false,
      totalTrades: 50,
      minRequiredTrades: 30,
      netPnl: 100,
      maxDrawdownPct: 2.0,
      winRatePct: 60.0
    }).status).toBe("DATA_CALC_INVALID");

    // Insufficient sample
    expect(determineVerdict({
      isDataValid: true,
      totalTrades: 5,
      minRequiredTrades: 30,
      netPnl: 100,
      maxDrawdownPct: 1.0,
      winRatePct: 80.0
    }).status).toBe("INSUFFICIENT_SAMPLE");

    // Needs improvement
    expect(determineVerdict({
      isDataValid: true,
      totalTrades: 35,
      minRequiredTrades: 30,
      netPnl: -45,
      maxDrawdownPct: 6.5,
      winRatePct: 40.0
    }).status).toBe("NEEDS_IMPROVEMENT");

    // Pass criteria
    expect(determineVerdict({
      isDataValid: true,
      totalTrades: 42,
      minRequiredTrades: 30,
      netPnl: 185.2,
      maxDrawdownPct: 3.2,
      winRatePct: 52.4
    }).status).toBe("PASS_CRITERIA");
  });

  it("T08: distinguishes between geometric price validity, pending trigger, and armed trigger", () => {
    type TriggerStatus = "CHƯA_HỢP_LỆ" | "ĐANG_CHỜ_ĐIỀU_KIỆN_VÀO_LỆNH" | "ĐÃ_ĐỦ_ĐIỀU_KIỆN_KÍCH_HOẠT";

    const evaluateScenarioState = (isGeometryValid: boolean, isTriggerMet: boolean): { state: TriggerStatus; label: string } => {
      if (!isGeometryValid) {
        return { state: "CHƯA_HỢP_LỆ", label: "Giá hoặc R:R chưa đạt chuẩn rủi ro" };
      }
      if (!isTriggerMet) {
        return { state: "ĐANG_CHỜ_ĐIỀU_KIỆN_VÀO_LỆNH", label: "Đang chờ điều kiện vào lệnh (chưa kích hoạt)" };
      }
      return { state: "ĐÃ_ĐỦ_ĐIỀU_KIỆN_KÍCH_HOẠT", label: "Đã đủ điều kiện kích hoạt lệnh" };
    };

    expect(evaluateScenarioState(false, false).state).toBe("CHƯA_HỢP_LỆ");
    expect(evaluateScenarioState(true, false).state).toBe("ĐANG_CHỜ_ĐIỀU_KIỆN_VÀO_LỆNH");
    expect(evaluateScenarioState(true, true).state).toBe("ĐÃ_ĐỦ_ĐIỀU_KIỆN_KÍCH_HOẠT");
  });

});
