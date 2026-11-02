"""
Layer 3 Defense: Policy Enforcement Point (PEP) Gate.
Enforces tool manifests, checks for denied capabilities, and identifies high-risk actions.
"""

from typing import Dict, Any, List, Optional


class PolicyViolationError(Exception):
    """Raised when an agent or tool violates security policy."""
    pass


class PolicyEngine:
    def __init__(self, manifest: Optional[Dict[str, Any]] = None):
        self.manifest = manifest or {}
        self.tools = {t["id"]: t for t in self.manifest.get("tools", [])}
        self.denied_capabilities = set(self.manifest.get("denied_capabilities", []))

    def evaluate_tool_call(self, tool_id: str, arguments: Dict[str, Any], user_role: str) -> Dict[str, Any]:
        """
        Validates tool execution against policy:
        1. Tool must be registered in the manifest.
        2. Prohibits shell injection or denied capability signatures.
        3. Flags whether action requires Human-in-the-Loop (HITL) approval.
        """
        if tool_id not in self.tools:
            raise PolicyViolationError(f"Tool '{tool_id}' is not permitted by Policy Engine manifest.")

        tool_meta = self.tools[tool_id]
        
        # Check denied argument patterns
        raw_args_str = str(arguments).lower()
        forbidden_signatures = ["os.system", "rm -rf", "drop table", "shutdown", "__import__"]
        for sig in forbidden_signatures:
            if sig in raw_args_str:
                raise PolicyViolationError(f"Denied capability detected in tool arguments: '{sig}'")

        # Determine approval requirement
        risk_level = tool_meta.get("risk_level", "LOW")
        requires_approval = tool_meta.get("requires_approval", False) or (risk_level == "HIGH")

        return {
            "allowed": True,
            "tool_id": tool_id,
            "risk_level": risk_level,
            "requires_approval": requires_approval,
            "sandbox": tool_meta.get("sandbox", "NONE")
        }

