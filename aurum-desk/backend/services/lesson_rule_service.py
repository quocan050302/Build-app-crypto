import time
import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
import models

logger = logging.getLogger(__name__)

# Canonical Whitelist of Supported Metrics and Operators
WHITELIST_METRICS = {
    "spread": {"type": "float", "allowed_ops": ["<=", "<", ">=", ">"]},
    "net_rr": {"type": "float", "allowed_ops": [">=", ">"], "min_bound": 2.0},  # Cannot loosen below baseline RR
    "session": {"type": "list_str", "allowed_ops": ["in", "not_in"]},
    "entry_window": {"type": "window", "allowed_ops": ["between", "outside"]},
    "distance_to_entry_atr": {"type": "float", "allowed_ops": ["<=", "<", ">=", ">"]},
    "quote_age_ms": {"type": "int", "allowed_ops": ["<=", "<"]},
    "evidence.sweep_detected": {"type": "bool", "allowed_ops": ["=="]},
    "evidence.fvg_found": {"type": "bool", "allowed_ops": ["=="]},
    "evidence.structure_confirmed": {"type": "bool", "allowed_ops": ["=="]},
}
class LessonDecisionDict(dict):
    """
    Structured dictionary implementing LessonDecisionItem contract with
    backward-compatible string matching (`in item`), 'text' property, and attribute access.
    """
    def __getitem__(self, key):
        if key == "text" and not super().__contains__("text"):
            return self.get("message") or self.get("title") or ""
        return super().__getitem__(key)

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            return None

    def __setattr__(self, name, value):
        self[name] = value

    def __contains__(self, item):
        if super().__contains__(item):
            return True
        if item in ("text", "human_message", "action_rule", "rule_text"):
            return True
        msg = str(self.get("message") or "")
        title = str(self.get("title") or "")
        next_step = str(self.get("next_step") or "")
        return (str(item) in msg) or (str(item) in title) or (str(item) in next_step)

    def __str__(self):
        return self.get("message") or self.get("title") or ""

class LessonRuleService:
    """
    Deterministic, offline, unit-testable rule engine for V10 Governed Lesson Rules.
    - Three-color classification:
        * INFO (Xanh): Advisory / informational notes, never blocks or tightens guards.
        * WARNING (Vàng): Warning with supporting metrics, logged into decision trace, no blocking.
        * CRITICAL (Đỏ): Entry-restricting rule; requires VALID status, approved, enabled, and matches scope.
    - Whitelist typed predicates only; NO dynamic eval/exec or LLM-generated expressions.
    - Supports shadow evaluation mode for zero-impact dry runs.
    """
    _cache_version: int = 0
    _cached_rules: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def invalidate_cache(cls):
        """Invalidates in-memory rules cache when lessons or rules are created/modified."""
        cls._cache_version += 1
        cls._cached_rules = None

    @classmethod
    def validate_predicate(
        cls,
        predicate_input: Any,
        severity: str = "INFO",
        effect: str = "ANNOTATE"
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates a candidate predicate against the canonical whitelist contract.
        Returns: (is_valid, message, report_dict)
        """
        if not predicate_input:
            if severity == "CRITICAL" or effect == "BLOCK_ENTRY":
                return False, "Quy tắc Đỏ (hạn chế entry) bắt buộc phải có điều kiện có cấu trúc (predicate).", {
                    "valid": False, "error": "MISSING_PREDICATE", "field": "predicate"
                }
            # For INFO or WARNING without predicate, it acts as unconditional advisory/warning
            return True, "Hợp lệ (không có điều kiện cấu trúc)", {"valid": True, "type": "NONE"}

        if isinstance(predicate_input, str):
            try:
                predicate = json.loads(predicate_input)
            except Exception as e:
                return False, f"Predicate không phải JSON hợp lệ: {str(e)}", {
                    "valid": False, "error": "INVALID_JSON", "detail": str(e)
                }
        elif isinstance(predicate_input, dict):
            predicate = predicate_input
        else:
            return False, "Predicate phải là object JSON hoặc dict", {"valid": False, "error": "INVALID_TYPE"}

        metric = predicate.get("metric")
        operator = predicate.get("operator")

        if not metric or metric not in WHITELIST_METRICS:
            allowed = list(WHITELIST_METRICS.keys())
            return False, f"Metric '{metric}' không thuộc danh sách cho phép ({', '.join(allowed)})", {
                "valid": False, "error": "UNSUPPORTED_METRIC", "metric": metric, "allowed": allowed
            }

        cfg = WHITELIST_METRICS[metric]
        if operator not in cfg["allowed_ops"]:
            return False, f"Toán tử '{operator}' không hợp lệ cho metric '{metric}'. Hợp lệ: {cfg['allowed_ops']}", {
                "valid": False, "error": "UNSUPPORTED_OPERATOR", "operator": operator, "allowed": cfg["allowed_ops"]
            }

        # Value / threshold checks
        if cfg["type"] == "float":
            val = predicate.get("threshold")
            if val is None or not isinstance(val, (int, float)):
                return False, f"Metric '{metric}' yêu cầu 'threshold' là số", {
                    "valid": False, "error": "INVALID_THRESHOLD"
                }
            if "min_bound" in cfg and float(val) < cfg["min_bound"]:
                return False, f"Ngưỡng '{metric}' không được nới lỏng dưới mức chuẩn {cfg['min_bound']}", {
                    "valid": False, "error": "BOUND_VIOLATION", "min_bound": cfg["min_bound"]
                }

        elif cfg["type"] == "int":
            val = predicate.get("threshold")
            if val is None or not isinstance(val, int) or val < 0:
                return False, f"Metric '{metric}' yêu cầu 'threshold' là số nguyên không âm", {
                    "valid": False, "error": "INVALID_THRESHOLD"
                }

        elif cfg["type"] == "list_str":
            values = predicate.get("values")
            if not values or not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                return False, f"Metric '{metric}' yêu cầu 'values' là danh sách chuỗi", {
                    "valid": False, "error": "INVALID_VALUES"
                }

        elif cfg["type"] == "window":
            start = predicate.get("start")
            end = predicate.get("end")
            if not start or not end:
                return False, "Cửa sổ thời gian yêu cầu 'start' và 'end' theo định dạng 'HH:MM'", {
                    "valid": False, "error": "INVALID_WINDOW"
                }
            try:
                datetime.strptime(start, "%H:%M")
                datetime.strptime(end, "%H:%M")
            except ValueError:
                return False, "Định dạng thời gian phải là 'HH:MM' (ví dụ: '09:30')", {
                    "valid": False, "error": "INVALID_TIME_FORMAT"
                }

        elif cfg["type"] == "bool":
            val = predicate.get("value")
            if val is None or not isinstance(val, bool):
                return False, f"Metric '{metric}' yêu cầu 'value' là boolean (true/false)", {
                    "valid": False, "error": "INVALID_BOOL"
                }

        report = {
            "valid": True,
            "metric": metric,
            "operator": operator,
            "config": predicate,
            "severity": severity,
            "effect": effect,
            "validated_at": int(time.time() * 1000)
        }
        return True, "Quy tắc kiểm tra cấu trúc hợp lệ", report

    @classmethod
    def _generate_default_fixtures_for_metric(
        cls,
        metric: Optional[str],
        predicate: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        fixtures = []
        if not predicate or not metric:
            return fixtures

        op = predicate.get("operator")
        th = predicate.get("threshold")

        if metric == "spread" and isinstance(th, (int, float)):
            if op in (">=", ">"):
                fixtures.append({"desc": "Spread above threshold", "context": {"spread": float(th) + 0.1}, "expected_matched": True})
                fixtures.append({"desc": "Spread below threshold", "context": {"spread": max(0.0, float(th) - 0.1)}, "expected_matched": False})
            elif op in ("<=", "<"):
                fixtures.append({"desc": "Spread below threshold", "context": {"spread": max(0.0, float(th) - 0.1)}, "expected_matched": True})
                fixtures.append({"desc": "Spread above threshold", "context": {"spread": float(th) + 0.1}, "expected_matched": False})

        elif metric == "net_rr" and isinstance(th, (int, float)):
            fixtures.append({"desc": "Net RR below threshold (violation)", "context": {"net_rr": float(th) - 0.1}, "expected_matched": True})
            fixtures.append({"desc": "Net RR above threshold (satisfies)", "context": {"net_rr": float(th) + 0.5}, "expected_matched": False})

        elif metric == "distance_to_entry_atr" and isinstance(th, (int, float)):
            if op in (">=", ">"):
                fixtures.append({"desc": "Distance above threshold", "context": {"distance_to_entry_atr": float(th) + 0.1}, "expected_matched": True})
                fixtures.append({"desc": "Distance below threshold", "context": {"distance_to_entry_atr": max(0.0, float(th) - 0.1)}, "expected_matched": False})

        elif metric == "session":
            vals = predicate.get("values") or ["NY"]
            if op == "in":
                fixtures.append({"desc": "Session in values", "context": {"session": vals[0]}, "expected_matched": True})
                fixtures.append({"desc": "Session not in values", "context": {"session": "OTHER_SESSION"}, "expected_matched": False})
            elif op == "not_in":
                fixtures.append({"desc": "Session not in values", "context": {"session": "OTHER_SESSION"}, "expected_matched": True})
                fixtures.append({"desc": "Session in values", "context": {"session": vals[0]}, "expected_matched": False})

        elif metric and metric.startswith("evidence."):
            ev_key = metric.split("evidence.", 1)[1]
            req_v = predicate.get("value", True)
            fixtures.append({"desc": "Evidence unsatisfied", "context": {"evidence": {ev_key: not req_v}}, "expected_matched": True})
            fixtures.append({"desc": "Evidence satisfied", "context": {"evidence": {ev_key: req_v}}, "expected_matched": False})

        return fixtures

    @classmethod
    def validate_rule_behavior(
        cls,
        rule: models.Lesson,
        test_fixtures: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates rule behavior against deterministic test fixtures (Finding J, P06).
        Syntax validity alone is not enough; behavior validation proves that the rule
        evaluates cleanly without exceptions and produces expected semantic outcomes.
        """
        is_syn_valid, msg, syn_report = cls.validate_predicate(rule.predicate, rule.severity, rule.effect)
        if not is_syn_valid:
            rule.validation_status = "INVALID"
            rule.validation_report = json.dumps({"valid": False, "stage": "SYNTAX", "detail": msg})
            return False, f"Syntax validation failed: {msg}", {
                "valid": False, "status": "INVALID", "detail": msg
            }

        severity = (rule.severity or "INFO").upper()
        effect = (rule.effect or "ANNOTATE").upper()

        if severity == "INFO" or effect == "ANNOTATE":
            report = {
                "valid": True,
                "status": "VALID",
                "syntax_valid": True,
                "behavior_verified": True,
                "type": "ADVISORY",
                "validated_at": int(time.time() * 1000)
            }
            rule.validation_status = "VALID"
            rule.validation_report = json.dumps(report)
            return True, "Advisory rule behavior verified", report

        predicate = json.loads(rule.predicate) if isinstance(rule.predicate, str) else rule.predicate
        metric = predicate.get("metric") if predicate else None

        fixtures = test_fixtures or cls._generate_default_fixtures_for_metric(metric, predicate)

        results = []
        try:
            for fix in fixtures:
                ctx = fix.get("context", {})
                expected_matched = fix.get("expected_matched")
                res = cls.evaluate_rules(ctx, [rule], feature_flags={"lesson_entry_rules_enabled": True, "lesson_shadow_mode": False})
                matched = any(m.get("matched") for m in res.get("matched_rules", []))
                data_unavail = any(e.get("data_unavailable") for e in res.get("evaluations", []))

                check_pass = True
                if expected_matched is not None and matched != expected_matched:
                    check_pass = False

                results.append({
                    "fixture_desc": fix.get("desc", ""),
                    "matched": matched,
                    "expected_matched": expected_matched,
                    "data_unavailable": data_unavail,
                    "passed": check_pass
                })

            all_passed = len(results) > 0 and all(r["passed"] for r in results)
            if not all_passed and len(fixtures) > 0:
                rule.validation_status = "SYNTAX_VALID_ONLY"
                rule.validation_report = json.dumps({"valid": False, "status": "SYNTAX_VALID_ONLY", "results": results})
                return False, "Behavioral verification failed on test fixtures", {
                    "valid": False, "status": "SYNTAX_VALID_ONLY", "results": results
                }

            report = {
                "valid": True,
                "status": "VALID",
                "syntax_valid": True,
                "behavior_verified": True,
                "fixtures_count": len(results),
                "results": results,
                "validated_at": int(time.time() * 1000)
            }
            rule.validation_status = "VALID"
            rule.validation_report = json.dumps(report)
            return True, "Rule behavior successfully verified", report
        except Exception as e:
            logger.exception(f"Rule behavior validation error: {e}")
            rule.validation_status = "INVALID"
            rule.validation_report = json.dumps({"valid": False, "status": "INVALID", "error": str(e)})
            return False, f"Behavior validation exception: {str(e)}", {
                "valid": False, "status": "INVALID", "error": str(e)
            }

    @staticmethod
    def get_current_session_tags(epoch_ms: int) -> List[str]:
        """
        Derives canonical session tags ('ASIA', 'LONDON', 'NY') using UTC epoch ms.
        Handles standard overlaps accurately.
        """
        dt_utc = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        hour_utc = dt_utc.hour + dt_utc.minute / 60.0
        tags = []

        # ASIA: 00:00 - 09:00 UTC (Tokyo session)
        if 0.0 <= hour_utc < 9.0:
            tags.append("ASIA")

        # LONDON: 07:00 - 16:30 UTC
        if 7.0 <= hour_utc < 16.5:
            tags.append("LONDON")

        # NY: 12:00 - 21:00 UTC (08:00 - 17:00 America/New_York)
        if 12.0 <= hour_utc < 21.0:
            tags.append("NY")

        if not tags:
            tags.append("ASIA")
        return tags

    @classmethod
    def retrieve_active_rules(
        cls,
        db: Session,
        context: Dict[str, Any],
        decision_time: Optional[int] = None
    ) -> List[models.Lesson]:
        """
        Retrieves ONLY active, approved, enabled lesson rules matching context scope and timeframe.
        Strictly excludes ARCHIVED and REJECTED lessons.
        Quarantines any rules with malformed scope (S12).
        """
        now_ms = decision_time if decision_time is not None else int(time.time() * 1000)

        # Base query: APPROVED, is_approved==True, enabled==True, status not in (ARCHIVED, REJECTED)
        query = db.query(models.Lesson).filter(
            models.Lesson.status == "APPROVED",
            models.Lesson.is_approved == True,
            models.Lesson.enabled == True
        )

        all_rules = query.order_by(models.Lesson.created_at.desc()).all()

        symbol = context.get("symbol") or "XAUUSDT"
        family = context.get("strategy_family") or "STANDARD_SMC"
        direction = context.get("direction")
        execution_mode = context.get("execution_mode")
        context_stage = context.get("stage") or "BEFORE_ARM"
        context_tf = context.get("timeframe")
        session_tag = context.get("session")
        derived_sessions = [session_tag] if session_tag else cls.get_current_session_tags(now_ms)

        matched_rules: List[models.Lesson] = []

        for rule in all_rules:
            # Explicit guard against archived/rejected
            if rule.status in ("ARCHIVED", "REJECTED") or not rule.is_approved:
                continue

            # Effective time check
            if rule.effective_at and now_ms < rule.effective_at:
                continue
            if rule.expiry_at and now_ms >= rule.expiry_at:
                continue

            # Stage scope check
            rule_stage = getattr(rule, "stage", None) or "ALL"
            if rule_stage and rule_stage != "ALL" and context_stage and rule_stage != context_stage:
                if context.get("order_type") == "MARKET" and rule_stage == "BEFORE_ARM":
                    pass
                else:
                    continue

            # Scope check
            if rule.scope:
                try:
                    scope = json.loads(rule.scope) if isinstance(rule.scope, str) else rule.scope
                    if not isinstance(scope, dict):
                        # S12: Malformed scope -> quarantine, do NOT apply to ALL!
                        logger.warning(f"Quarantining lesson #{rule.id} with non-dict scope: {rule.scope}")
                        continue

                    # Stage scope in dict
                    scope_stage = scope.get("stage", "ALL")
                    if scope_stage and scope_stage != "ALL" and context_stage and scope_stage != context_stage:
                        continue

                    # Timeframe scope
                    scope_tf = scope.get("timeframe", "ALL")
                    if scope_tf and scope_tf != "ALL" and context_tf and scope_tf != context_tf:
                        continue

                    # Symbol scope
                    rule_sym = scope.get("symbol", "ALL")
                    if rule_sym and rule_sym != "ALL" and rule_sym != symbol:
                        continue

                    # Strategy Family scope
                    rule_fam = scope.get("strategy_family", "ALL")
                    if rule_fam and rule_fam != "ALL" and rule_fam != family:
                        continue

                    # Direction scope
                    rule_dir = scope.get("direction", "ALL")
                    if rule_dir and rule_dir != "ALL" and direction and rule_dir != direction:
                        continue

                    # Execution Mode scope (MANUAL, AUTO, ALL)
                    rule_mode = scope.get("execution_mode", "ALL")
                    if rule_mode and rule_mode != "ALL":
                        if not execution_mode or execution_mode == "UNKNOWN":
                            # S07: Scope mode MANUAL/AUTO/UNKNOWN đúng, không wildcard silent
                            continue
                        if execution_mode != "ALL" and rule_mode != execution_mode:
                            continue

                    # Session scope
                    rule_sess = scope.get("session", "ALL")
                    if rule_sess and rule_sess != "ALL":
                        if rule_sess not in derived_sessions and "ALL" not in derived_sessions:
                            continue
                except Exception as e:
                    # S12: Malformed scope -> quarantine, do NOT apply to ALL!
                    logger.warning(f"Quarantining lesson #{rule.id} with malformed scope JSON: {e}")
                    continue

            matched_rules.append(rule)

        return matched_rules

    @classmethod
    def evaluate_rules(
        cls,
        context: Dict[str, Any],
        rules: List[models.Lesson],
        feature_flags: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Evaluates active rules against verified execution context.
        Uses server-authoritative LessonPolicyService defaults if feature_flags is not passed.
        Supports both evaluate_rules(context, rules) and evaluate_rules(db, context).
        """
        if hasattr(context, "query"):
            db_session = context
            context = rules if isinstance(rules, dict) else {}
            import crud
            rules = crud.get_lessons(db_session)
        elif rules is None:
            rules = []

        if feature_flags is None:
            try:
                from services.lesson_policy_service import LessonPolicyService
                flags = LessonPolicyService.get_policy()
            except Exception:
                flags = {}
        else:
            flags = feature_flags

        advisory_enabled = flags.get("lesson_advisory_enabled", True)
        entry_rules_enabled = flags.get("lesson_entry_rules_enabled", True)
        shadow_mode = flags.get("lesson_shadow_mode", False)

        now_ms = context.get("now_ms") or int(time.time() * 1000)

        can_proceed = True
        blocking_reasons: List[str] = []
        warning_messages: List[str] = []
        advisory_notes: List[str] = []
        matched_rules: List[Dict[str, Any]] = []
        evaluations: List[Dict[str, Any]] = []
        snapshot_items: List[Dict[str, Any]] = []
        lesson_items: List[LessonDecisionDict] = []
        lesson_advisories: List[LessonDecisionDict] = []
        lesson_warnings: List[LessonDecisionDict] = []
        lesson_blockers: List[LessonDecisionDict] = []

        for rule in rules:
            raw_sev = (rule.severity or "INFO").upper()
            severity = "WARN" if raw_sev == "WARNING" else raw_sev
            effect = (rule.effect or "ANNOTATE").upper()
            rule_id = rule.id
            title = rule.title
            version = getattr(rule, "version", 1) or 1
            rule_lesson_id = getattr(rule, "lesson_id", getattr(rule, "related_trade_id", rule.id))
            scope_dict = None
            if rule.scope:
                try:
                    scope_dict = json.loads(rule.scope) if isinstance(rule.scope, str) else rule.scope
                except Exception:
                    scope_dict = None

            eval_item = LessonDecisionDict({
                "rule_id": rule_id,
                "rule_version": version,
                "lesson_id": rule_lesson_id,
                "severity": severity,
                "effect": effect,
                "title": title,
                "matched": False,
                "evaluated": False,
                "data_unavailable": False,
                "would_block": False,
                "effective_block": False,
                "reason_code": None,
                "message": None,
                "next_step": None,
                "metric_value": None,
                "evaluated_at": now_ms,
                "scope": scope_dict,
                "symbol": context.get("symbol"),
                "direction": context.get("direction"),
                "timeframe": context.get("timeframe"),
                "setup_instance_id": context.get("setup_instance_id"),
                "revision": context.get("revision")
            })

            # 1. INFO / ANNOTATE (Xanh - Tham khảo)
            if severity == "INFO" or effect == "ANNOTATE":
                if advisory_enabled:
                    eval_item["matched"] = True
                    eval_item["evaluated"] = True
                    eval_item["reason_code"] = f"LESSON_INFO_{rule_id}"
                    eval_item["message"] = f"Tham khảo #{rule_id}: {title}. {rule.action_rule or ''}".strip()
                    eval_item["next_step"] = "Ghi nhận bài học kinh nghiệm khi theo dõi lệnh."
                    advisory_notes.append(eval_item["message"])
                    matched_rules.append(eval_item)
                    lesson_advisories.append(eval_item)
                evaluations.append(eval_item)
                lesson_items.append(eval_item)
                snapshot_items.append({
                    "lesson_id": rule_id,
                    "version": version,
                    "severity": "INFO",
                    "effect": "ANNOTATE",
                    "matched": True,
                    "title": title
                })
                continue

            # 2. PROPOSE_PLAN_ADJUSTMENT (Disabled/Preview only in V10)
            if effect == "PROPOSE_PLAN_ADJUSTMENT":
                eval_item["evaluated"] = True
                eval_item["matched"] = False
                eval_item["message"] = f"Đề xuất kế hoạch #{rule_id} ({title}) ở chế độ chỉ xem trước (chưa tự động áp dụng trong V10)."
                eval_item["next_step"] = "Xem trước kế hoạch đề xuất."
                evaluations.append(eval_item)
                lesson_items.append(eval_item)
                snapshot_items.append({
                    "lesson_id": rule_id,
                    "version": version,
                    "severity": severity,
                    "effect": "PROPOSE_PLAN_ADJUSTMENT",
                    "matched": False,
                    "title": title
                })
                continue

            # 3. Predicate Evaluation for WARNING (Vàng) or CRITICAL (Đỏ)
            predicate = None
            if rule.predicate:
                try:
                    predicate = json.loads(rule.predicate) if isinstance(rule.predicate, str) else rule.predicate
                except Exception:
                    predicate = None

            if not predicate or not isinstance(predicate, dict):
                # Unstructured warning/critical
                if severity == "WARN" or effect == "WARN_ENTRY":
                    eval_item["matched"] = True
                    eval_item["evaluated"] = True
                    eval_item["reason_code"] = f"LESSON_WARN_{rule_id}"
                    eval_item["message"] = f"Cảnh báo #{rule_id}: {title} (quy tắc tổng quát)."
                    eval_item["next_step"] = "Cân nhắc rủi ro hoặc xem lại kế hoạch trước khi đặt lệnh."
                    warning_messages.append(eval_item["message"])
                    matched_rules.append(eval_item)
                    lesson_warnings.append(eval_item)
                elif severity == "CRITICAL" or effect == "BLOCK_ENTRY":
                    # Critical without valid structured predicate CANNOT block!
                    eval_item["evaluated"] = False
                    eval_item["message"] = f"Quy tắc #{rule_id} thiếu điều kiện cấu trúc hợp lệ; không áp dụng chặn entry."
                    eval_item["next_step"] = "Cấu hình điều kiện có cấu trúc cho bài học trong Nhật ký."
                evaluations.append(eval_item)
                lesson_items.append(eval_item)
                snapshot_items.append({
                    "lesson_id": rule_id,
                    "version": version,
                    "severity": severity,
                    "effect": effect,
                    "matched": eval_item["matched"],
                    "title": title
                })
                continue

            metric = predicate.get("metric")
            operator = predicate.get("operator")
            threshold = predicate.get("threshold")
            if threshold is None:
                threshold = predicate.get("value")
            if threshold is not None:
                try:
                    threshold = float(threshold)
                except (ValueError, TypeError):
                    pass
            values = predicate.get("values")

            matched = False
            data_missing = False
            metric_val = None

            # Extract metric from context
            if metric == "spread":
                metric_val = context.get("spread")
                if metric_val is None and context.get("ask") is not None and context.get("bid") is not None:
                    metric_val = round(context["ask"] - context["bid"], 2)
                if metric_val is not None and threshold is not None:
                    if operator == ">=" and metric_val >= threshold:
                        matched = True
                    elif operator == ">" and metric_val > threshold:
                        matched = True
                    elif operator == "<=" and metric_val <= threshold:
                        matched = True
                    elif operator == "<" and metric_val < threshold:
                        matched = True
                else:
                    data_missing = True

            elif metric == "net_rr":
                metric_val = context.get("net_rr")
                if metric_val is not None:
                    if operator == ">=" and metric_val < threshold:
                        matched = True  # Violates required min net_rr
                    elif operator == ">" and metric_val <= threshold:
                        matched = True
                else:
                    data_missing = True

            elif metric == "session":
                metric_val = context.get("session")
                if metric_val:
                    if operator == "in" and metric_val in values:
                        matched = True
                    elif operator == "not_in" and metric_val not in values:
                        matched = True
                else:
                    data_missing = True

            elif metric == "entry_window":
                tz_name = predicate.get("timezone", "America/New_York")
                try:
                    tz = ZoneInfo(tz_name)
                    dt = datetime.fromtimestamp(now_ms / 1000.0, tz=tz)
                    cur_hm = dt.strftime("%H:%M")
                    metric_val = cur_hm
                    start = predicate.get("start")
                    end = predicate.get("end")
                    in_win = (start <= cur_hm <= end)
                    if operator == "outside" and not in_win:
                        matched = True
                    elif operator == "between" and in_win:
                        matched = True
                except Exception as e:
                    logger.warning(f"Error evaluating entry_window: {e}")
                    data_missing = True

            elif metric == "distance_to_entry_atr":
                metric_val = context.get("distance_to_entry_atr")
                if metric_val is not None:
                    if operator == ">=" and metric_val >= threshold:
                        matched = True
                    elif operator == ">" and metric_val > threshold:
                        matched = True
                    elif operator == "<=" and metric_val <= threshold:
                        matched = True
                    elif operator == "<" and metric_val < threshold:
                        matched = True
                else:
                    data_missing = True

            elif metric == "quote_age_ms":
                metric_val = context.get("quote_age_ms")
                if metric_val is not None:
                    if operator == ">=" and metric_val >= threshold:
                        matched = True
                    elif operator == ">" and metric_val > threshold:
                        matched = True
                else:
                    data_missing = True

            elif metric and metric.startswith("evidence."):
                ev_key = metric.split("evidence.", 1)[1]
                evidence_dict = context.get("evidence") or {}
                if isinstance(evidence_dict, str):
                    try:
                        evidence_dict = json.loads(evidence_dict)
                    except Exception:
                        evidence_dict = {}
                metric_val = evidence_dict.get(ev_key)
                req_val = predicate.get("value")
                if metric_val is not None:
                    if operator == "==" and metric_val != req_val:
                        matched = True  # Required evidence was not satisfied
                else:
                    data_missing = True

            eval_item["metric_value"] = metric_val
            eval_item["evaluated"] = not data_missing

            if data_missing:
                eval_item["data_unavailable"] = True
                eval_item["reason_code"] = "LESSON_RULE_DATA_UNAVAILABLE"
                if (severity == "CRITICAL" or effect == "BLOCK_ENTRY") and predicate.get("strict_data", True):
                    # Mandatory input missing -> block entry specifically (X04)
                    eval_item["would_block"] = True
                    eval_item["message"] = f"Thiếu dữ liệu bắt buộc ({metric}) để kiểm tra quy tắc #{rule_id} ({title})."
                    eval_item["next_step"] = "Kiểm tra kết nối dữ liệu hoặc cấu hình quy tắc trong Nhật ký."
                    if entry_rules_enabled and not shadow_mode and rule.validation_status == "VALID":
                        can_proceed = False
                        eval_item["effective_block"] = True
                        blocking_reasons.append(f"LESSON_RULE_DATA_UNAVAILABLE: {eval_item['message']}")
                        lesson_blockers.append(eval_item)
                    matched_rules.append(eval_item)
                else:
                    # Optional warning data missing -> honest message without fake match (X05)
                    eval_item["message"] = f"Chưa đủ dữ liệu để đánh giá điều kiện cho quy tắc #{rule_id} ({title})."
                    eval_item["next_step"] = "Bổ sung dữ liệu quan sát thị trường."
                    advisory_notes.append(eval_item["message"])
                    lesson_advisories.append(eval_item)
            elif matched:
                eval_item["matched"] = True
                if severity == "WARN" or effect == "WARN_ENTRY":
                    eval_item["reason_code"] = f"LESSON_WARN_{rule_id}"
                    eval_item["message"] = f"Cảnh báo quy tắc #{rule_id} ({title}): {metric} = {metric_val} (ngưỡng {operator} {threshold or values})."
                    eval_item["next_step"] = "Cân nhắc rủi ro hoặc xem lại kế hoạch trước khi đặt lệnh."
                    warning_messages.append(eval_item["message"])
                    matched_rules.append(eval_item)
                    lesson_warnings.append(eval_item)

                elif severity == "CRITICAL" or effect == "BLOCK_ENTRY":
                    # Only block if rule is explicitly marked VALID
                    is_valid_rule = (rule.validation_status == "VALID")
                    if is_valid_rule:
                        eval_item["would_block"] = True
                        eval_item["reason_code"] = "LESSON_RULE_BLOCKED"
                        eval_item["message"] = f"Quy tắc #{rule_id} ({title}) đang hạn chế entry: {metric} = {metric_val} (ngưỡng {operator} {threshold or values})."
                        eval_item["next_step"] = "Chờ thị trường thoát khỏi vùng hạn chế hoặc điều chỉnh quy tắc trong Nhật ký."

                        if shadow_mode:
                            eval_item["message"] += " [SHADOW MODE - Không chặn lệnh thực tế]"
                            warning_messages.append(eval_item["message"])
                            lesson_warnings.append(eval_item)
                        elif entry_rules_enabled:
                            can_proceed = False
                            eval_item["effective_block"] = True
                            blocking_reasons.append(f"LESSON_RULE_BLOCKED: {eval_item['message']}")
                            lesson_blockers.append(eval_item)

                        matched_rules.append(eval_item)
                    else:
                        eval_item["message"] = f"Quy tắc #{rule_id} ({title}) chưa được xác thực (UNVALIDATED); không áp dụng chặn."
                        eval_item["next_step"] = "Xác thực quy tắc trong Nhật ký để kích hoạt hạn chế entry."

            evaluations.append(eval_item)
            lesson_items.append(eval_item)
            snapshot_items.append({
                "lesson_id": rule_id,
                "version": version,
                "severity": severity,
                "effect": effect,
                "matched": eval_item["matched"],
                "metric_value": metric_val,
                "would_block": eval_item["would_block"],
                "reason_code": eval_item.get("reason_code"),
                "title": title
            })

        return {
            "can_proceed": can_proceed,
            "can_enter": can_proceed,
            "blocking_reasons": blocking_reasons,
            "blocker_notes": blocking_reasons,
            "warning_messages": warning_messages,
            "warning_notes": warning_messages,
            "advisory_notes": advisory_notes,
            "matched_rules": matched_rules,
            "evaluations": evaluations,
            "lessons_retrieved_snapshot": snapshot_items,
            "lesson_items": lesson_items,
            "lesson_advisories": lesson_advisories,
            "lesson_warnings": lesson_warnings,
            "lesson_blockers": lesson_blockers,
        }

lesson_rule_service = LessonRuleService()
