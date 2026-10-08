import time
import math
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
            metadata_version=meta.metadata_version,
            min_leverage=meta.min_leverage,
            max_leverage=meta.max_leverage,
            cross_margin_supported=False,
            source=meta.status if meta.status == "SUCCESS" else "Bitget Classic Futures USDT-M",
            status=meta.status
        )

    @staticmethod
    def update_settings(db: Session, update: schemas.RiskSettingsUpdate) -> schemas.RiskSettingsResponse:
        current_version_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "risk_config_version").first()
        current_version = int(current_version_cfg.value) if current_version_cfg else 1

        # Strict CAS version check: expected_config_version must equal current_version
        if update.expected_config_version is not None and update.expected_config_version != current_version:
            raise ValueError(f"STALE_EDIT: Expected config_version {update.expected_config_version} does not match current {current_version}")

        lev_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_leverage").first()
        mm_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_margin_mode").first()
        risk_cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == "default_risk_pct").first()

        current_leverage = int(lev_cfg.value) if lev_cfg else 5
        current_margin_mode = mm_cfg.value if mm_cfg else "ISOLATED"
        current_risk_pct = float(risk_cfg.value) if risk_cfg else 0.25

        new_leverage = update.leverage if update.leverage is not None else current_leverage
        new_margin_mode = update.margin_mode.upper() if update.margin_mode is not None else current_margin_mode
        new_risk_pct = update.risk_pct if update.risk_pct is not None else current_risk_pct

        # Typed validation
        if not math.isfinite(new_leverage) or int(new_leverage) != new_leverage:
            raise ValueError("Leverage must be a finite integer")
        new_leverage = int(new_leverage)

        meta = instrument_provider.get_metadata_sync("XAUUSDT")
        if new_leverage < meta.min_leverage or new_leverage > meta.max_leverage:
            raise ValueError(f"Leverage {new_leverage} is out of allowed range ({meta.min_leverage}-{meta.max_leverage})")

        if new_margin_mode not in ("ISOLATED", "CROSS"):
            raise ValueError(f"Margin mode '{new_margin_mode}' is invalid. Allowed modes: ISOLATED, CROSS")

        if not math.isfinite(new_risk_pct) or new_risk_pct <= 0 or new_risk_pct > 10.0:
            raise ValueError(f"Risk percentage {new_risk_pct} must be a positive finite number <= 10.0%")

        now_ms = int(time.time() * 1000)
        new_version = current_version + 1

        def _upsert(key: str, value: str):
            cfg = db.query(models.SystemConfig).filter(models.SystemConfig.key == key).first()
            if cfg:
                cfg.value = value
                cfg.updated_at = now_ms
            else:
                db.add(models.SystemConfig(key=key, value=value, updated_at=now_ms))

        _upsert("default_leverage", str(new_leverage))
        _upsert("default_margin_mode", new_margin_mode)
        _upsert("default_risk_pct", str(new_risk_pct))
        _upsert("risk_config_version", str(new_version))

        db.commit()

        return RiskSettingsService.get_settings(db)

risk_settings_service = RiskSettingsService()

