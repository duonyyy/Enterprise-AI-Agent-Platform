from pathlib import Path
from typing import Dict, Any

class SkillResourceSecurityViolation(Exception):
    pass

class SkillResourceReader:
    """
    Progressive Disclosure Layer 3: Resources.
    Reads references/ and assets/ from immutable bundles with strict path traversal & zip-slip protection.
    """
    def __init__(self, resource_store: Dict[str, Dict[str, str]] = None):
        self.resource_store = resource_store or {}

    def read_resource(self, skill_version_id: str, relative_path: str) -> str:
        # Step 1: Normalize and detect path traversal attacks
        norm_path = Path(relative_path).as_posix()
        if ".." in norm_path or norm_path.startswith("/") or "\\" in relative_path:
            raise SkillResourceSecurityViolation(f"Path traversal rejected: '{relative_path}'")

        # Step 2: Enforce allowed directories (only references/ or assets/, scripts/ forbidden in MVP)
        if norm_path.startswith("scripts/"):
            raise PermissionError("Scripts execution/reading is disabled in MVP.")
        if not (norm_path.startswith("references/") or norm_path.startswith("assets/")):
            raise SkillResourceSecurityViolation(f"Resource must be in references/ or assets/: '{norm_path}'")

        # Step 3: Fetch resource from store
        bundle = self.resource_store.get(skill_version_id, {})
        if norm_path not in bundle:
            raise FileNotFoundError(f"Resource '{norm_path}' not found in skill bundle.")

        return bundle[norm_path]

