import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
import models, crud
from services.clock import live_clock, IClock

class TradingPolicyService:
    """
    Pure Business Trading Policy & Quota Service:
    - Enforces max 3 fills per day UTC+7 across all routes (manual, standard, fallback).
    - Enforces mandatory NY session policy (08:00 - 11:00 NY) with minimum 1 fill target.
    - Manages NY slot reservation (ALL_SESSIONS_WITH_NY_RESERVE reserves 3rd slot for NY).
    - Manages NY SessionQuota lifecycle: NOT_STARTED, SEEKING_STANDARD, SEEKING_FALLBACK,
      ARMED_PENDING_FILL, FULFILLED, BLOCKED, MISSED.
    - Injected clock support for deterministic replay and testing.
    """

    @staticmethod
    def get_default_policy() -> models.TradingPolicy:
        return models.TradingPolicy(
            id="default",
            version=1,
            mode="PAPER",
            symbol="XAUUSDT",
            max_daily_fills=3,
            daily_timezone="Asia/Ho_Chi_Minh",
            entry_session_policy="ALL_SESSIONS_WITH_NY_RESERVE",
            ny_timezone="America/New_York",
            ny_entry_start="08:00",
            ny_entry_end="11:00",
            ny_min_fills=1,
            reserve_ny_slot=True,
            ny_fallback_enabled=True,
            ny_fallback_start="10:30",
            ny_fallback_risk_pct_cap=0.10,
            min_net_rr=2.0,
            max_open_positions=1,
            max_armed_orders=1,
            updated_at=int(time.time() * 1000)
        )

    @staticmethod
    def get_policy(db: Session) -> Optional[models.TradingPolicy]:
        return db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()

    @staticmethod
    def get_or_create_policy(db: Session) -> models.TradingPolicy:
        policy = db.query(models.TradingPolicy).filter(models.TradingPolicy.id == "default").first()
        if not policy:
            policy = TradingPolicyService.get_default_policy()
            db.add(policy)
            db.commit()
            db.refresh(policy)
        return policy

    @staticmethod
    def update_policy(db: Session, update_data: Dict[str, Any], clock: Optional[IClock] = None) -> models.TradingPolicy:
        c = clock or live_clock
        now_ms = c.now_ms()
        policy = TradingPolicyService.get_policy(db)
        if not policy:
            policy = TradingPolicyService.get_default_policy()
            db.add(policy)
            db.flush()

        # Validation
        if "max_daily_fills" in update_data and update_data["max_daily_fills"] != 3:
            raise ValueError("Chính sách yêu cầu max_daily_fills cố định là 3.")
        if "min_net_rr" in update_data and float(update_data["min_net_rr"]) < 2.0:
            raise ValueError("min_net_rr không được thấp hơn 2.0 theo quy tắc SMC/ICT.")
        if "ny_fallback_risk_pct_cap" in update_data and float(update_data["ny_fallback_risk_pct_cap"]) > 0.10:
            raise ValueError("ny_fallback_risk_pct_cap không được vượt quá 0.10% rủi ro.")

        # Time validation: ny_entry_start < ny_fallback_start < ny_entry_end
        start_str = update_data.get("ny_entry_start", policy.ny_entry_start)
        fb_str = update_data.get("ny_fallback_start", policy.ny_fallback_start)
        end_str = update_data.get("ny_entry_end", policy.ny_entry_end)
        try:
            h_s, m_s = map(int, start_str.split(":"))
            h_f, m_f = map(int, fb_str.split(":"))
            h_e, m_e = map(int, end_str.split(":"))
            t_s = dtime(h_s, m_s)
            t_f = dtime(h_f, m_f)
            t_e = dtime(h_e, m_e)
            if not (t_s < t_f < t_e):
                raise ValueError("Cửa sổ phiên Mỹ không hợp lệ: Yêu cầu ny_entry_start < ny_fallback_start < ny_entry_end.")
        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            raise ValueError(f"Định dạng giờ không hợp lệ: {e}")

        # Update attributes
        for k, v in update_data.items():
            if hasattr(policy, k) and k not in ("id", "version", "updated_at"):
                setattr(policy, k, v)

        policy.version += 1
        policy.updated_at = now_ms
        db.commit()
        db.refresh(policy)
        return policy

    @staticmethod
    def get_ny_session_window(
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> Dict[str, Any]:
        """
        Calculates authoritative epoch ms boundaries for current NY trading window.
        Uses standard IANA timezones (ZoneInfo) to accurately handle Daylight Saving Time (EDT vs EST).
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy if policy is not None else TradingPolicyService.get_default_policy()

        ny_tz = ZoneInfo(p.ny_timezone or "America/New_York")
        vn_tz = ZoneInfo(p.daily_timezone or "Asia/Ho_Chi_Minh")

        now_ny = datetime.fromtimestamp(current_time / 1000.0, tz=ny_tz)
        now_vn = datetime.fromtimestamp(current_time / 1000.0, tz=vn_tz)

        # Parse start and end hours/minutes
        h_start, m_start = map(int, p.ny_entry_start.split(":"))
        h_end, m_end = map(int, p.ny_entry_end.split(":"))
        h_fb, m_fb = map(int, p.ny_fallback_start.split(":"))

        # Baseline NY date for the current/most relevant session
        session_ny_date = now_ny.date()
        session_instance_id = f"NY-{session_ny_date.strftime('%Y-%m-%d')}"

        dt_start_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_start, m_start, tzinfo=ny_tz)
        dt_end_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_end, m_end, tzinfo=ny_tz)
        dt_fb_ny = datetime(session_ny_date.year, session_ny_date.month, session_ny_date.day, h_fb, m_fb, tzinfo=ny_tz)

        start_ms = int(dt_start_ny.timestamp() * 1000)
        end_ms = int(dt_end_ny.timestamp() * 1000)
        fb_ms = int(dt_fb_ny.timestamp() * 1000)

        dt_start_vn = dt_start_ny.astimezone(vn_tz)
        dt_end_vn = dt_end_ny.astimezone(vn_tz)

        # Determine phase
        if current_time < start_ms:
            phase = "BEFORE_WINDOW"
        elif start_ms <= current_time < fb_ms:
            phase = "IN_STANDARD_WINDOW"
        elif fb_ms <= current_time < end_ms:
            phase = "IN_FALLBACK_WINDOW"
        else:
            phase = "AFTER_WINDOW"

        is_in_window = (start_ms <= current_time < end_ms)

        return {
            "session_instance_id": session_instance_id,
            "session_date_ny": session_ny_date.strftime("%Y-%m-%d"),
            "session_date_vn": dt_start_vn.strftime("%Y-%m-%d"),
            "window_start_ms": start_ms,
            "window_end_ms": end_ms,
            "fallback_start_ms": fb_ms,
            "ny_time_str": now_ny.strftime("%H:%M:%S"),
            "vn_time_str": now_vn.strftime("%H:%M:%S"),
            "local_ny_time": now_ny.strftime("%H:%M:%S"),
            "local_vn_time": now_vn.strftime("%H:%M:%S"),
            "ny_window_str": f"{policy.ny_entry_start} - {policy.ny_entry_end} NY",
            "vn_window_str": f"{dt_start_vn.strftime('%H:%M')} - {dt_end_vn.strftime('%H:%M')} VN",
            "phase": phase,
            "is_in_window": is_in_window,
            "is_fallback_active": (fb_ms <= current_time < end_ms) and policy.ny_fallback_enabled
        }

    @staticmethod
    def get_or_create_session_quota(
        db: Session,
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> models.SessionQuota:
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy or TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()

        window = TradingPolicyService.get_ny_session_window(p, now_ms=current_time, clock=c)
        instance_id = window["session_instance_id"]

        quota = db.query(models.SessionQuota).filter(models.SessionQuota.session_instance_id == instance_id).first()
        if not quota:
            utc_date_str = datetime.fromtimestamp(current_time / 1000.0, tz=ZoneInfo("UTC")).strftime("%Y-%m-%d")
            quota = models.SessionQuota(
                account_namespace="default",
                symbol=p.symbol or "XAUUSDT",
                session_instance_id=instance_id,
                session_date_utc=utc_date_str,
                session_date_vn=window["session_date_vn"],
                session_date_ny=window["session_date_ny"],
                window_start_ms=window["window_start_ms"],
                window_end_ms=window["window_end_ms"],
                fallback_start_ms=window["fallback_start_ms"],
                target_fills=policy.ny_min_fills or 1,
                standard_fills=0,
                fallback_fills=0,
                total_fills=0,
                quota_status="NOT_STARTED" if window["phase"] == "BEFORE_WINDOW" else "SEEKING_STANDARD",
                last_status_reason="Khởi tạo phiên theo dõi chỉ tiêu phiên Mỹ",
                last_evaluated_at=current_time,
                created_at=current_time,
                updated_at=current_time
            )
            db.add(quota)
            db.commit()
            db.refresh(quota)
        return quota

    @staticmethod
    def update_quota_lifecycle_state(
        db: Session,
        policy: Optional[models.TradingPolicy] = None,
        now_ms: Optional[int] = None,
        clock: Optional[IClock] = None
    ) -> models.SessionQuota:
        """
        Transitions the quota state machine deterministically based on time,
        active position, armed orders, and day audit limiters.
        """
        c = clock or live_clock
        current_time = now_ms if now_ms is not None else c.now_ms()
        p = policy or TradingPolicyService.get_policy(db)
        quota = TradingPolicyService.get_or_create_session_quota(db, p, now_ms=current_time, clock=c)
        window = TradingPolicyService.get_ny_session_window(p, now_ms=current_time, clock=c)

        # 1. If quota already fulfilled
        if quota.total_fills >= quota.target_fills:
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ: {quota.total_fills}/{quota.target_fills} lệnh khớp"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            db.commit()
            return quota

        # 2. Before entry window
        if window["phase"] == "BEFORE_WINDOW":
            quota.quota_status = "NOT_STARTED"
            quota.last_status_reason = f"Chưa tới cửa sổ phiên Mỹ ({window['vn_window_str']})"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            db.commit()
            return quota

        # 3. After entry window has closed
        if window["phase"] == "AFTER_WINDOW":
            if quota.total_fills == 0:
                quota.quota_status = "MISSED"
                quota.last_status_reason = "Hết cửa sổ phiên Mỹ nhưng không có lệnh PAPER nào được khớp"
            quota.last_evaluated_at = current_time
            quota.updated_at = current_time
            db.commit()
            return quota

        # 4. In window (Standard or Fallback): check block conditions
        day_audit = crud.get_or_create_today_audit(db, clock=c)
        active_pos = crud.get_active_position(db)
        armed_order = db.query(models.PaperOrder).filter(models.PaperOrder.state == "armed").first()
        is_blackout, blackout_reason, _ = crud.check_news_blackout(db, current_time)

        if day_audit.fills_count >= 3:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = "Đã chạm trần 3 lệnh tối đa trong ngày (UTC+7)"
        elif day_audit.consecutive_losses >= 2:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = "Đã dừng giao dịch sau 2 lệnh lỗ liên tiếp"
        elif day_audit.is_blocked:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Tạm dừng giao dịch: {day_audit.block_reason}"
        elif is_blackout:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Đang trong khung Blackout tin tức: {blackout_reason}"
        elif active_pos:
            quota.quota_status = "BLOCKED"
            quota.last_status_reason = f"Đang duy trì vị thế mở ({active_pos.direction} tại {active_pos.actual_entry:.2f})"
        elif armed_order:
            quota.quota_status = "ARMED_PENDING_FILL"
            quota.last_status_reason = f"Đang có lệnh chờ khớp ({armed_order.direction} tại {armed_order.planned_entry:.2f})"
        elif window["phase"] == "IN_FALLBACK_WINDOW":
            quota.quota_status = "SEEKING_FALLBACK"
            quota.last_status_reason = "Đang kích hoạt tìm cơ hội Fallback nghiên cứu (sau 10:30 NY)"
        else:
            quota.quota_status = "SEEKING_STANDARD"
            quota.last_status_reason = "Đang quét cơ hội SMC chuẩn trong phiên Mỹ"

        quota.last_evaluated_at = current_time
        quota.updated_at = current_time
        db.commit()
        return quota

    @staticmethod
    def get_active_policy(db: Session, symbol: str = "XAUUSDT") -> models.TradingPolicy:
        return TradingPolicyService.get_or_create_policy(db)

    @staticmethod
    def evaluate_entry_policy(
        db: Session,
        order_type_or_symbol: str = "MARKET",
        strategy_family_or_time: Any = "STANDARD_SMC",
        clock: Optional[IClock] = None,
        now_ms: Optional[int] = None,
        order_type: str = "MARKET",
        strategy_family: str = "STANDARD_SMC"
    ) -> Dict[str, Any]:
        """
        Unified gatekeeper for ALL entry paths (manual, auto standard, auto fallback).
        Returns whether an entry is permitted under the current trading policy and quota rules.
        """
        c = clock or live_clock
        current_time = now_ms

        if isinstance(strategy_family_or_time, datetime):
            current_time = int(strategy_family_or_time.timestamp() * 1000)
        elif isinstance(order_type_or_symbol, datetime):
            current_time = int(order_type_or_symbol.timestamp() * 1000)

        if current_time is None:
            current_time = c.now_ms()

        policy = TradingPolicyService.get_policy(db)
        if not policy or not getattr(policy, "is_active", True):
            return {
                "can_enter": True,
                "allowed": True,
                "reason_codes": [],
                "reason_code": None,
                "block_reasons": [],
                "reason_message": None,
                "session_instance_id": "DEFAULT",
                "policy_config_version": 1,
                "policy": policy,
                "quota_state": "NOT_APPLICABLE",
                "quota_status": "NOT_APPLICABLE",
                "ny_fills": 0,
                "ny_fill_count": 0,
                "ny_min_fills": 1,
                "ny_target_fills": 1,
                "daily_fills": 0,
                "daily_fill_count": 0,
                "max_daily_fills": 3,
                "remaining_daily_slots": 3,
                "reserved_slots": 0,
                "is_in_ny_window": True,
                "is_fallback_active": False,
                "local_ny_time": "",
                "local_vn_time": "",
                "ny_window_display": "",
                "vn_window_display": "",
                "minutes_to_window_start": None,
                "minutes_to_fallback": None,
                "minutes_to_window_end": None,
                "window_info": {}
            }

        window = TradingPolicyService.get_ny_session_window(policy, now_ms=current_time, clock=c)
        quota = TradingPolicyService.update_quota_lifecycle_state(db, policy, now_ms=current_time, clock=c)
        day_audit = crud.get_or_create_today_audit(db, clock=c)

        can_enter = True
        reason_codes = []
        block_reasons = []

        # 1. Mode check
        if policy.mode != "PAPER":
            can_enter = False
            reason_codes.append("UNSUPPORTED_MODE")
            block_reasons.append("Chỉ hỗ trợ chế độ PAPER trading.")

        # 2. Daily Fill Cap (Strict: Max 3 fills per UTC+7 day)
        if day_audit.fills_count >= policy.max_daily_fills:
            can_enter = False
            reason_codes.append("MAX_DAILY_ENTRIES")
            block_reasons.append(f"Đã đạt giới hạn tối đa {policy.max_daily_fills} lệnh khớp/ngày (UTC+7)")

        # 3. Session Window & Slot Reservation Policy
        if policy.entry_session_policy == "NY_ONLY":
            if not window["is_in_window"]:
                can_enter = False
                reason_codes.append("OUTSIDE_ENTRY_WINDOW")
                block_reasons.append(
                    f"OUTSIDE_ENTRY_WINDOW: Ngoài cửa sổ giao dịch phiên Mỹ ({window['ny_window_str']} = {window['vn_window_str']}). "
                    f"Chính sách NY_ONLY chỉ cho phép mở lệnh trong khung giờ này."
                )
        elif policy.entry_session_policy == "ALL_SESSIONS_WITH_NY_RESERVE":
            # If outside NY window and no NY fill yet today
            if not window["is_in_window"] and quota.total_fills == 0 and policy.reserve_ny_slot:
                # If already used 2 fills today, the 3rd fill MUST be reserved for NY!
                if day_audit.fills_count >= 2:
                    can_enter = False
                    reason_codes.append("NY_SLOT_RESERVED")
                    block_reasons.append(
                        "NY_SLOT_RESERVED: Đã sử dụng 2 slot giao dịch trong ngày. "
                        "Slot thứ 3 bắt buộc được dự trữ cho mục tiêu 1 lệnh phiên Mỹ."
                    )

        remaining_daily_slots = max(0, policy.max_daily_fills - day_audit.fills_count)
        reserved_slots = 1 if (policy.reserve_ny_slot and quota.total_fills == 0 and remaining_daily_slots > 0) else 0

        # Minutes calculations
        min_to_start = None
        min_to_fb = None
        min_to_end = None
        if current_time < window["window_start_ms"]:
            min_to_start = max(0, int((window["window_start_ms"] - current_time) / 60000))
        if current_time < window["fallback_start_ms"]:
            min_to_fb = max(0, int((window["fallback_start_ms"] - current_time) / 60000))
        if current_time < window["window_end_ms"]:
            min_to_end = max(0, int((window["window_end_ms"] - current_time) / 60000))

        return {
            "can_enter": can_enter,
            "allowed": can_enter,
            "reason_codes": reason_codes,
            "reason_code": reason_codes[0] if reason_codes else None,
            "block_reasons": block_reasons,
            "reason_message": block_reasons[0] if block_reasons else None,
            "session_instance_id": window["session_instance_id"],
            "policy_config_version": policy.version,
            "policy": policy,
            "quota_state": quota.quota_status,
            "quota_status": quota.quota_status,
            "ny_fills": quota.total_fills,
            "ny_fill_count": quota.total_fills,
            "ny_min_fills": quota.target_fills,
            "ny_target_fills": quota.target_fills,
            "daily_fills": day_audit.fills_count,
            "daily_fill_count": day_audit.fills_count,
            "max_daily_fills": policy.max_daily_fills,
            "remaining_daily_slots": remaining_daily_slots,
            "reserved_slots": reserved_slots,
            "is_in_ny_window": window["is_in_window"],
            "is_fallback_active": window["phase"] == "IN_FALLBACK_WINDOW",
            "local_ny_time": window["local_ny_time"],
            "local_vn_time": window["local_vn_time"],
            "ny_window_display": window["ny_window_str"],
            "vn_window_display": window["vn_window_str"],
            "minutes_to_window_start": min_to_start,
            "minutes_to_fallback": min_to_fb,
            "minutes_to_window_end": min_to_end,
            "window_info": window
        }

    @staticmethod
    def record_quota_fill(
        db: Session,
        order: models.PaperOrder,
        clock: Optional[IClock] = None,
        commit: bool = False
    ) -> models.SessionQuota:
        """
        Atomically records an opened fill into the SessionQuota ledger.
        Ensures quota is fulfilled if order opened in NY window.
        """
        c = clock or live_clock
        now_ms = order.opened_at or c.now_ms()
        policy = TradingPolicyService.get_policy(db) or TradingPolicyService.get_default_policy()
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms, clock=c)
        quota = TradingPolicyService.get_or_create_session_quota(db, policy, now_ms=now_ms, clock=c)

        # Check if opened_at falls into NY window
        if window["window_start_ms"] <= now_ms < window["window_end_ms"]:
            family = getattr(order, "strategy_family", "STANDARD_SMC") or "STANDARD_SMC"
            if family == "NY_QUOTA_PAPER":
                quota.fallback_fills += 1
            else:
                quota.standard_fills += 1
            quota.total_fills += 1
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ bằng lệnh {order.direction} (#{order.id})"
            order.session_instance_id = quota.session_instance_id

        quota.last_evaluated_at = now_ms
        quota.updated_at = now_ms
        if commit:
            db.commit()
            db.refresh(quota)
        return quota

    @staticmethod
    def record_fill(
        db: Session,
        symbol: str = "XAUUSDT",
        fill_time: Optional[datetime] = None,
        order_id: Optional[str] = None,
        is_ny_quota_candidate: bool = True,
        order: Optional[models.PaperOrder] = None,
        clock: Optional[IClock] = None,
        commit: bool = False
    ) -> Dict[str, Any]:
        """
        Convenience wrapper for TradeLifecycleService to atomically record fill
        into SessionQuota and return session details.
        """
        target_order = order
        if not target_order and order_id:
            target_order = db.query(models.PaperOrder).filter(models.PaperOrder.id == order_id).first()

        if target_order:
            quota = TradingPolicyService.record_quota_fill(db, target_order, clock=clock, commit=commit)
            return {
                "session_instance_id": quota.session_instance_id,
                "quota_status": quota.quota_status,
                "total_fills": quota.total_fills
            }

        # Fallback if order object not yet flushed
        policy = TradingPolicyService.get_policy(db)
        c = clock or live_clock
        now_ms = int(fill_time.timestamp() * 1000) if fill_time else c.now_ms()
        window = TradingPolicyService.get_ny_session_window(policy, now_ms=now_ms, clock=c)
        quota = TradingPolicyService.get_or_create_session_quota(db, policy, now_ms=now_ms, clock=c)

        if window["window_start_ms"] <= now_ms < window["window_end_ms"]:
            quota.total_fills += 1
            quota.standard_fills += 1
            quota.quota_status = "FULFILLED"
            quota.last_status_reason = f"Đã hoàn thành chỉ tiêu phiên Mỹ ({order_id or 'FILL'})"
            quota.last_evaluated_at = now_ms
            quota.updated_at = now_ms
            if commit:
                db.commit()

        return {
            "session_instance_id": quota.session_instance_id,
            "quota_status": quota.quota_status,
            "total_fills": quota.total_fills
        }

trading_policy_service = TradingPolicyService()

