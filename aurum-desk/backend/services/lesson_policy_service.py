import os
import time
import logging
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
import models
import schemas

logger = logging.getLogger(__name__)

BUILD_SHA = "55b3528"
APP_VERSION = "10.1.0"

class LessonPolicyService:
    """
    Authoritative, persisted, and versioned policy service for Governed Lesson Rules.
    - Manages server-authoritative feature flags:
        * lesson_advisory_enabled (default True)
        * lesson_entry_rules_enabled (default True)
        * lesson_shadow_mode (default False)
        * plan_adjustment_enabled (default False)
    - Persists flags in models.SystemConfig with versioning & optimistic locking.
    - Supports environment variable overrides for emergency rollout/rollback.
    - Provides runtime capability diagnostics for UI version checking.
    """

    @classmethod
    def get_policy(cls, db: Optional[Session] = None) -> Dict[str, Any]:
        """
        Retrieves authoritative policy flags snapshot.
        If db is provided, reads from models.SystemConfig with versioning.
        Environment variables take precedence if explicitly set.
        """
        now_ms = int(time.time() * 1000)
        version = 1
        advisory_enabled = True
        entry_rules_enabled = True
        shadow_mode = False
        plan_adj_enabled = False
        updated_at = now_ms

        if db is not None:
            try:
                c_ver = db.query(models.SystemConfig).filter(models.SystemConfig.key == "lesson_policy_version").first()
                c_adv = db.query(models.SystemConfig).filter(models.SystemConfig.key == "lesson_advisory_enabled").first()
                c_ent = db.query(models.SystemConfig).filter(models.SystemConfig.key == "lesson_entry_rules_enabled").first()
                c_shd = db.query(models.SystemConfig).filter(models.SystemConfig.key == "lesson_shadow_mode").first()
                c_adj = db.query(models.SystemConfig).filter(models.SystemConfig.key == "plan_adjustment_enabled").first()

                if c_ver and c_ver.value:
                    version = int(c_ver.value)
                if c_adv and c_adv.value:
                    advisory_enabled = (c_adv.value.lower() in ("true", "1", "yes"))
                if c_ent and c_ent.value:
                    entry_rules_enabled = (c_ent.value.lower() in ("true", "1", "yes"))
                if c_shd and c_shd.value:
                    shadow_mode = (c_shd.value.lower() in ("true", "1", "yes"))
                if c_adj and c_adj.value:
                    plan_adj_enabled = (c_adj.value.lower() in ("true", "1", "yes"))
                if c_ver and c_ver.updated_at:
                    updated_at = c_ver.updated_at
            except Exception as e:
                logger.warning(f"Could not load lesson policy from DB, using defaults: {e}")

        # Environment variable overrides
        if "AURUM_LESSON_ADVISORY_ENABLED" in os.environ:
            advisory_enabled = (os.environ["AURUM_LESSON_ADVISORY_ENABLED"].lower() in ("true", "1", "yes"))
        if "AURUM_LESSON_ENTRY_RULES_ENABLED" in os.environ:
            entry_rules_enabled = (os.environ["AURUM_LESSON_ENTRY_RULES_ENABLED"].lower() in ("true", "1", "yes"))
        if "AURUM_LESSON_SHADOW_MODE" in os.environ:
            shadow_mode = (os.environ["AURUM_LESSON_SHADOW_MODE"].lower() in ("true", "1", "yes"))
        if "AURUM_PLAN_ADJUSTMENT_ENABLED" in os.environ:
            plan_adj_enabled = (os.environ["AURUM_PLAN_ADJUSTMENT_ENABLED"].lower() in ("true", "1", "yes"))

        return {
            "lesson_advisory_enabled": advisory_enabled,
            "lesson_entry_rules_enabled": entry_rules_enabled,
            "lesson_shadow_mode": shadow_mode,
            "plan_adjustment_enabled": plan_adj_enabled,
            "version": version,
            "updated_at": updated_at
        }

    @classmethod
    def reset_to_defaults(cls, db: Optional[Session] = None):
        """Resets policy to defaults."""
        now_ms = int(time.time() * 1000)
        for env_k in ["AURUM_LESSON_ADVISORY_ENABLED", "AURUM_LESSON_ENTRY_RULES_ENABLED", "AURUM_LESSON_SHADOW_MODE", "AURUM_PLAN_ADJUSTMENT_ENABLED"]:
            if env_k in os.environ:
                os.environ.pop(env_k)

        if db is not None:
            keys_vals = [
                ("lesson_policy_version", "1"),
                ("lesson_advisory_enabled", "true"),
                ("lesson_entry_rules_enabled", "true"),
                ("lesson_shadow_mode", "false"),
                ("plan_adjustment_enabled", "false")
            ]
            for k, v in keys_vals:
                row = db.query(models.SystemConfig).filter(models.SystemConfig.key == k).first()
                if not row:
                    row = models.SystemConfig(key=k, value=v, updated_at=now_ms)
                    db.add(row)
                else:
                    row.value = v
                    row.updated_at = now_ms
            db.commit()

        from services.lesson_rule_service import LessonRuleService
        LessonRuleService.invalidate_cache()

    @classmethod
    def update_policy(
        cls,
        db: Session,
        update: Any,
        expected_version: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Updates policy config with optimistic locking check on expected_version.
        Accepts schemas.LessonPolicyUpdate, dict, or keyword args.
        """
        current = cls.get_policy(db)
        cur_version = current["version"]

        exp_ver = expected_version
        if hasattr(update, "expected_version") and update.expected_version is not None:
            exp_ver = update.expected_version
        elif isinstance(update, dict) and update.get("expected_version") is not None:
            exp_ver = update.get("expected_version")

        if exp_ver is not None and exp_ver != cur_version:
            raise ValueError(f"STALE_EDIT: Expected policy version {exp_ver} does not match current version {cur_version}")

        now_ms = int(time.time() * 1000)
        new_version = cur_version + 1

        def _upsert(key: str, val: str):
            cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
            if cfg:
                cfg.value = val
                cfg.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key=key, value=val, updated_at=now_ms))

        upd_dict = update if isinstance(update, dict) else (update.model_dump(exclude_unset=True) if hasattr(update, "model_dump") else {})

        new_adv = upd_dict.get("lesson_advisory_enabled") if upd_dict.get("lesson_advisory_enabled") is not None else current["lesson_advisory_enabled"]
        new_ent = upd_dict.get("lesson_entry_rules_enabled") if upd_dict.get("lesson_entry_rules_enabled") is not None else current["lesson_entry_rules_enabled"]
        new_shd = upd_dict.get("lesson_shadow_mode") if upd_dict.get("lesson_shadow_mode") is not None else current["lesson_shadow_mode"]
        new_adj = upd_dict.get("plan_adjustment_enabled") if upd_dict.get("plan_adjustment_enabled") is not None else current["plan_adjustment_enabled"]

        _upsert("lesson_advisory_enabled", "true" if new_adv else "false")
        _upsert("lesson_entry_rules_enabled", "true" if new_ent else "false")
        _upsert("lesson_shadow_mode", "true" if new_shd else "false")
        _upsert("plan_adjustment_enabled", "true" if new_adj else "false")
        _upsert("lesson_policy_version", str(new_version))

        db.commit()

        # Invalidate rule cache
        from services.lesson_rule_service import LessonRuleService
        LessonRuleService.invalidate_cache()

        return cls.get_policy(db)

    @classmethod
    def get_runtime_capabilities(cls) -> Dict[str, Any]:
        """
        Returns runtime build SHA, capabilities, and supported API routes for UI diagnostics.
        """
        return {
            "version": APP_VERSION,
            "build_sha": BUILD_SHA,
            "capabilities": [
                "LESSON_GOVERNANCE_V10",
                "ENTRY_DECISION_SERVICE",
                "DESIRED_STATE_ENABLE",
                "SHADOW_EVALUATION",
                "STAGE_SNAPSHOTS",
                "TIMEFRAME_STAGE_SCOPE"
            ],
            "features": {
                "lesson_entry_rules": True,
                "lesson_shadow_mode": True,
                "stage_snapshots": True,
                "plan_adjustment": False
            },
            "supported_routes": [
                "/api/v1/lessons/{lesson_id}/toggle-enable",
                "/api/v1/lessons/{lesson_id}/set-enable",
                "/api/v1/lessons/validate-predicate",
                "/api/v1/lessons/policy",
                "/api/v1/system/capabilities"
            ]
        }
