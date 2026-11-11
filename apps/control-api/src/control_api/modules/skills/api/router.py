import hashlib

from control_api.core.authorization import require_permission
from control_api.core.dependencies import CurrentUser
from control_api.modules.skills.api.request_dto import CreateSkillDraftDTO
from control_api.modules.skills.api.response_dto import SkillResponseDTO, SkillVersionDTO
from fastapi import APIRouter, status

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/skills", tags=["skills"])


@router.get("", response_model=list[SkillResponseDTO])
async def list_skills(workspace_id: str, principal: CurrentUser):
    require_permission(principal, "skill:read")
    return [
        {
            "id": "sk_1",
            "slug": "weekly-report",
            "display_name": "Weekly Report",
            "revision": 1,
            "status": "active",
        }
    ]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SkillResponseDTO)
async def create_skill(workspace_id: str, dto: CreateSkillDraftDTO, principal: CurrentUser):
    require_permission(principal, "skill:create")
    return {
        "id": "sk_new",
        "slug": dto.slug,
        "display_name": dto.display_name,
        "revision": 1,
        "status": "active",
    }


@router.post("/{skill_id}/publish", status_code=status.HTTP_201_CREATED, response_model=SkillVersionDTO)
async def publish_skill(workspace_id: str, skill_id: str, principal: CurrentUser):
    require_permission(principal, "skill:publish")
    bundle_hash = hashlib.sha256(f"skill-bundle-{skill_id}".encode()).hexdigest()
    return {
        "id": f"skv_{skill_id}_v1",
        "skill_id": skill_id,
        "version": 1,
        "bundle_sha256": bundle_hash,
        "is_immutable": True,
    }


