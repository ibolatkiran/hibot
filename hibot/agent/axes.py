"""Two-axis classification (round 1) and the deterministic mapping to the legacy one-axis `classification`.

Why: the old single label mixed WHY it broke (root cause) with WHAT was done about it (resolution).  A status gap in
Catalog.StatusStep is a `config` root cause whose resolution was a `data_fix` by a developer; calling it "config" or
"data_correction" were both defensible, which made ~3 of 7 confidently-wrong answers of the baseline pure taxonomy noise.

  root_cause_type    config | code_defect | master_data | integration | user_error | access | data_issue | unknown
  resolution_action  data_fix | config_change | code_change | master_data_change | explain_to_user | grant_access |
                     escalate_other_system | none | unknown

Legacy `classification` (kept for cards, scoring of old runs, tests) is DERIVED, never chosen independently:

  1. root_cause_type in {master_data, access, integration, code_defect} -> the same name
  2. resolution_action == data_fix (any other root cause: config, data_issue, unknown, user_error) -> data_correction
  3. config -> config;  user_error -> user_error;  data_issue -> data_correction;  unknown -> unknown

and, for old reports / cards that only have the legacy label, the axes are derived back:

  code_defect -> (code_defect, code_change)        master_data -> (master_data, master_data_change)
  access -> (access, grant_access)                 data_correction -> (data_issue, data_fix)
  integration -> (integration, escalate_other_system)   config -> (config, config_change)
  user_error -> (user_error, explain_to_user)      unknown -> (unknown, unknown)
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

ROOT_CAUSE_TYPES = ("config", "code_defect", "master_data", "integration", "user_error", "access", "data_issue", "unknown")
RESOLUTION_ACTIONS = ("data_fix", "config_change", "code_change", "master_data_change", "explain_to_user", "grant_access",
                      "escalate_other_system", "none", "unknown")
LEGACY = ("code_defect", "master_data", "access", "data_correction", "integration", "config", "user_error", "unknown")

LEGACY_TO_AXES: Dict[str, Tuple[str, str]] = {
    "code_defect": ("code_defect", "code_change"),
    "master_data": ("master_data", "master_data_change"),
    "access": ("access", "grant_access"),
    "data_correction": ("data_issue", "data_fix"),
    "integration": ("integration", "escalate_other_system"),
    "config": ("config", "config_change"),
    "user_error": ("user_error", "explain_to_user"),
    "unknown": ("unknown", "unknown"),
}
DEFAULT_ACTION = {v[0]: v[1] for v in LEGACY_TO_AXES.values()}
_DIRECT = ("master_data", "access", "integration", "code_defect")

ROOT_TR = {
    "config": "Konfigürasyon", "code_defect": "Kod hatası", "master_data": "Ana veri", "integration": "Entegrasyon",
    "user_error": "Kullanıcı hatası / tasarım gereği", "access": "Yetki / erişim", "data_issue": "Veri sorunu", "unknown": "Belirsiz",
}
ACTION_TR = {
    "data_fix": "Veri düzeltme (DB'de elle)", "config_change": "Konfigürasyon değişikliği", "code_change": "Kod değişikliği",
    "master_data_change": "Ana veri değişikliği", "explain_to_user": "Kullanıcıya açıklama", "grant_access": "Yetki verilmesi",
    "escalate_other_system": "Başka sisteme (SAP/Atlas vb.) yönlendirme", "none": "Aksiyon gerekmiyor", "unknown": "Belirsiz",
}


def derive_classification(root_cause_type: Optional[str], resolution_action: Optional[str]) -> str:
    rct = root_cause_type if root_cause_type in ROOT_CAUSE_TYPES else "unknown"
    if rct in _DIRECT:
        return rct
    if resolution_action == "data_fix":
        return "data_correction"
    return {"config": "config", "user_error": "user_error", "data_issue": "data_correction"}.get(rct, "unknown")


def derive_axes(classification: Optional[str]) -> Tuple[str, str]:
    return LEGACY_TO_AXES.get(classification or "unknown", ("unknown", "unknown"))


def normalize(rep: Dict[str, Any]) -> Dict[str, Any]:
    """Make axes and legacy `classification` consistent (in place).  Axes win; a missing axis is derived from the legacy label."""
    rct, ra, cl = rep.get("root_cause_type"), rep.get("resolution_action"), rep.get("classification")
    if rct not in ROOT_CAUSE_TYPES:
        rct = None
    if ra not in RESOLUTION_ACTIONS:
        ra = None
    if cl not in LEGACY:
        cl = None
    if rct is None and ra is None:
        rct, ra = derive_axes(cl)
    elif rct is None:
        rct = derive_axes(cl)[0] if cl else "unknown"
    elif ra is None:
        ra = derive_axes(cl)[1] if cl and derive_axes(cl)[0] == rct else DEFAULT_ACTION[rct]
    rep["root_cause_type"], rep["resolution_action"] = rct, ra
    rep["classification"] = derive_classification(rct, ra)
    return rep
