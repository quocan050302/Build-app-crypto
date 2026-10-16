"""
backend/services/smc_model_registry.py
V16 SMC Model Registry & Specification (Rules 090 - 096).
Maps model identifiers to session policy, authority requirements, evidence needs, and UI display metadata.
Fails-closed on unknown models (never fail-open).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from services.scalp_m1_types import ModelIdentifier


@dataclass(frozen=True)
class SMCModelSpec:
    model_id: str
    display_name_vi: str
    authority_type: str  # "ZONE_LIFECYCLE", "CLUSTER_LIFECYCLE", "DIRECT", "CG01_LIFECYCLE"
    session_family: str  # "SCALP_M1_REVERSAL", "SCALP_M1_CONTINUATION", "NY_DAILY_RESEARCH", "CG01_PAPER"
    requires_zone: bool
    requires_fvg: bool
    requires_cluster: bool
    description_vi: str
    required_evidence: tuple = ()


SMC_MODEL_REGISTRY: Dict[str, SMCModelSpec] = {
    ModelIdentifier.SMC_M1_REVERSAL.value: SMCModelSpec(
        model_id=ModelIdentifier.SMC_M1_REVERSAL.value,
        display_name_vi="SMC Đảo chiều M1 (Reversal)",
        authority_type="ZONE_LIFECYCLE",
        session_family="SCALP_M1_REVERSAL",
        requires_zone=True,
        requires_fvg=True,
        requires_cluster=False,
        description_vi="Đảo chiều tại vùng POI/OB kèm quét thanh khoản, CHOCH và retest FVG.",
    ),
    ModelIdentifier.SMC_M1_CONTINUATION.value: SMCModelSpec(
        model_id=ModelIdentifier.SMC_M1_CONTINUATION.value,
        display_name_vi="SMC Tiếp diễn M1 (BOS)",
        authority_type="DIRECT",
        session_family="SCALP_M1_CONTINUATION",
        requires_zone=False,
        requires_fvg=False,
        requires_cluster=False,
        description_vi="Tiếp diễn xu hướng thuận theo BOS và thiên hướng HTF.",
    ),
    ModelIdentifier.SMC_M1_FVG_CONTINUATION.value: SMCModelSpec(
        model_id=ModelIdentifier.SMC_M1_FVG_CONTINUATION.value,
        display_name_vi="SMC Tiếp diễn FVG M1",
        authority_type="DIRECT",
        session_family="SCALP_M1_CONTINUATION",
        requires_zone=False,
        requires_fvg=True,
        requires_cluster=False,
        description_vi="Tiếp diễn xu hướng khi giá phá vỡ cấu trúc và hồi quy retest FVG hợp lệ.",
    ),
    ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value: SMCModelSpec(
        model_id=ModelIdentifier.SMC_M1_SWEEP_RECLAIM.value,
        display_name_vi="SMC Quét & Reclaim M1",
        authority_type="CLUSTER_LIFECYCLE",
        session_family="SCALP_M1_REVERSAL",
        requires_zone=False,
        requires_fvg=False,
        requires_cluster=True,
        description_vi="Phản ứng quét thanh khoản tại cụm liquidity xác nhận và đóng nến reclaim.",
    ),
    ModelIdentifier.NY_DAILY_RESEARCH.value: SMCModelSpec(
        model_id=ModelIdentifier.NY_DAILY_RESEARCH.value,
        display_name_vi="Nghiên cứu phiên New York (NY Research)",
        authority_type="DIRECT",
        session_family="NY_DAILY_RESEARCH",
        requires_zone=False,
        requires_fvg=False,
        requires_cluster=False,
        description_vi="Mô hình nghiên cứu phiên Mỹ trên khung 5M nhằm đảm bảo cơ hội tối thiểu.",
    ),
    ModelIdentifier.CG01_SWEEP_MSS_FVG.value: SMCModelSpec(
        model_id=ModelIdentifier.CG01_SWEEP_MSS_FVG.value,
        display_name_vi="CG-01 Quét, MSS & FVG Retest M1",
        authority_type="CG01_LIFECYCLE",
        session_family="CG01_PAPER",
        requires_zone=True,
        requires_fvg=True,
        requires_cluster=False,
        description_vi="Mô hình chuẩn CG-01: HTF context -> HTF POI -> liquidity sweep -> MSS displacement -> FVG retest.",
        required_evidence=(
            "HTF_CONTEXT",
            "HTF_POI",
            "LIQUIDITY_REFERENCE",
            "SWEEP",
            "STRUCTURE_REFERENCE",
            "MSS",
            "DISPLACEMENT",
            "FVG",
            "ENTRY_TRIGGER",
            "INVALIDATION",
            "TARGET",
        ),
    ),
}


def get_model_spec(model_id: str) -> Optional[SMCModelSpec]:
    return SMC_MODEL_REGISTRY.get(model_id)


def is_registered_model(model_id: str) -> bool:
    return model_id in SMC_MODEL_REGISTRY


def check_model_lifecycle_requirement(model_id: str) -> str:
    spec = get_model_spec(model_id)
    if spec is None:
        return "UNKNOWN_MODEL"
    return spec.authority_type


def get_session_family(model_id: str) -> str:
    spec = get_model_spec(model_id)
    if spec is None:
        return "UNSUPPORTED"
    return spec.session_family


def requires_zone_lifecycle_authority(model_id: str) -> bool:
    spec = get_model_spec(model_id)
    if spec is None:
        return True  # Fail-safe to requiring authority
    return spec.requires_zone
