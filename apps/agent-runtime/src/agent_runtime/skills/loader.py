from typing import Dict, Any, Optional

class SkillLoader:
    """
    Progressive Disclosure Layer 2: Load.
    Loads full SKILL.md body and parsed manifest on-demand.
    """
    def __init__(self, store: Optional[Dict[str, Dict[str, Any]]] = None):
        self.store = store or {}

    def load_skill(self, skill_version_id: str) -> Dict[str, Any]:
        """Loads immutable instructions by pinned skill_version_id."""
        if skill_version_id not in self.store:
            raise ValueError(f"Skill version '{skill_version_id}' not found or not pinned.")

        record = self.store[skill_version_id]
        if record.get("status") == "disabled":
            raise PermissionError(f"Skill version '{skill_version_id}' is disabled.")

        return {
            "skill_version_id": skill_version_id,
            "name": record["name"],
            "bundle_sha256": record["bundle_sha256"],
            "instructions": record["skill_md"],
            "resources": record.get("resources", [])
        }

