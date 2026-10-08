import time
import logging
from sqlalchemy.orm import Session
import models
import schemas
from services.instrument_provider import instrument_provider

logger = logging.getLogger(__name__)

class RiskSettingsService:
    @staticmethod
    def get_settings(db: Session) -> schemas.RiskSettingsResponse:
        lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
        mm_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
        risk_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_risk_pct").first()
        version_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()

        lev = int(lev_cfg.value) if lev_cfg else 5
        mm = mm_cfg.value if mm_cfg else "ISOLATED"
        risk_pct = float(risk_cfg.value) if risk_cfg else 0.25
        config_version = int(version_cfg.value) if version_cfg else 1
        updated_at = int(lev_cfg.updated_at) if lev_cfg and lev_cfg.updated_at else int(time.time() * 1000)

        # Get metadata version from provider (sync fallback)
        meta = instrument_provider.get_metadata_sync("XAUUSDT")

        return schemas.RiskSettingsResponse(
            symbol="XAUUSDT",
            product_type="USDT-FUTURES",
            requested_leverage=lev,
            margin_mode=mm,
            risk_pct=risk_pct,
            config_version=config_version,
            updated_at=updated_at,
            metadata_version=meta.metadata_version
        )

    @staticmethod
    def update_settings(db: Session, update: schemas.RiskSettingsUpdate) -> schemas.RiskSettingsResponse:
        current_version_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()
        current_version = int(current_version_cfg.value) if current_version_cfg else 1

        if update.expected_config_version is not None and update.expected_config_version < current_version:
            raise ValueError(f"STALE_EDIT: Expected {update.expected_config_version} but current is {current_version}")

        now_ms = int(time.time() * 1000)
        new_version = current_version + 1

        def _upsert(key: str, value: str):
            cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
            if cfg:
                cfg.value = value
                cfg.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key=key, value=value, updated_at=now_ms))

        # Validate against metadata
        meta = instrument_provider.get_metadata_sync("XAUUSDT")
        if update.leverage < meta.min_leverage or update.leverage > meta.max_leverage:
            raise ValueError(f"Leverage {update.leverage} is out of allowed range ({meta.min_leverage}-{meta.max_leverage})")
        if update.risk_pct <= 0:
            raise ValueError("Risk percentage must be positive")

        _upsert("default_leverage", str(update.leverage))
        _upsert("default_margin_mode", update.margin_mode.upper())
        _upsert("default_risk_pct", str(update.risk_pct))
        _upsert("risk_config_version", str(new_version))

        db.commit()

        return RiskSettingsService.get_settings(db)

risk_settings_service = RiskSettingsService()
