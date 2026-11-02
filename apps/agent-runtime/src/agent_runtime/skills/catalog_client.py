from typing import List, Dict, Any

class SkillCatalogClient:
    """
    Progressive Disclosure Layer 1: Discover.
    Provides metadata-only catalog of approved and pinned skills for the run.
    """
    def __init__(self, pinned_skills: List[Dict[str, Any]] = None):
        self.pinned_skills = pinned_skills or []

    def get_eligible_skills_metadata(self, workspace_id: str) -> List[Dict[str, str]]:
        """Returns only lightweight metadata (name, description, skill_version_id)."""
        return [
            {
                "skill_version_id": s["skill_version_id"],
                "name": s["name"],
                "description": s["description"]
            }
            for s in self.pinned_skills
            if s.get("workspace_id") == workspace_id and s.get("status", "active") == "active"
        ]

