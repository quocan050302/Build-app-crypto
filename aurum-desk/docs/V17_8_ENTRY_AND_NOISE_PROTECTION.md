# AURUM DESK V17.8 — ENTRY QUALITY & MARKET NOISE PROTECTION

## 1. Problem Definition: The Microstructure Noise Barrier
Analysis of historical M1 trades revealed that orders placed within 1 standard deviation of 1-minute market volatility (`stop_distance < ATR_M1`) were frequently stopped out within 10 to 60 seconds after execution by momentary Bid/Ask spread expansion or minor retests.

To protect against entering trades already doomed by microstructure noise, Aurum Desk V17.8 introduces two distinct defense mechanisms into `entry_guard_service.py`.

---

## 2. Pre-ARM and Pre-FILL Executable Quote Distance Guard

### 2.1 SHORT Direction Protection
For SHORT orders, the stop loss is triggered on market **Ask**. Therefore, the executable quote distance is:
```text
remaining_distance = selected_sl - current_ask
```

1. **Stop Penetration Guard:**
   - If `current_ask >= selected_sl`:
   - Fails closed with `INVALID_GEOMETRY_STOP_BREACHED`.
   - The market has already pierced the stop loss; entering is physically invalid.
2. **Noise Cushion Guard:**
   - If `remaining_distance < min_protective_distance`:
   - Fails closed with `STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`.
   - Entering with less than `min_protective_distance` guarantees extreme vulnerability to noise.

### 2.2 LONG Direction Protection
For LONG orders, the stop loss is triggered on market **Bid**. The executable quote distance is:
```text
remaining_distance = current_bid - selected_sl
```

1. **Stop Penetration Guard:**
   - If `current_bid <= selected_sl`:
   - Fails closed with `INVALID_GEOMETRY_STOP_BREACHED`.
2. **Noise Cushion Guard:**
   - If `remaining_distance < min_protective_distance`:
   - Fails closed with `STOP_TOO_CLOSE_TO_EXECUTABLE_QUOTE`.

---

## 3. Unicorn Setup Invalidation Guard
Before ARM and FILL, the guard inspects the attached `unicorn_evidence`:
- If `validity_status` is `UNICORN_INVALIDATED` or `BREACHED`:
- Emits `UNICORN_INVALIDATED` and blocks entry execution.
- If the retest window has expired or price has penetrated the Breaker distal extreme, the setup is dead and cannot be traded.

---

## 4. Spread and Quote Feed Health
- If `quote.is_stale == True`: blocks with `QUOTE_STALE`.
- If `quote.bid > quote.ask`: blocks with `NEGATIVE_SPREAD`.
- If `quote.spread > max_allowed_spread`: blocks with `SPREAD_EXCESSIVE`.
