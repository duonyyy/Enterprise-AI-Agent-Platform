"""Bundle the canonical schema in both source and wheel distributions."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CanonicalSchemaBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        project = Path(self.root)
        relative = Path("agent_contracts/schemas/agent-spec.v1.schema.json")
        canonical = (project / "../../../contracts/jsonschema/agent-spec.v1.schema.json").resolve()
        embedded = project / "src" / relative
        # An unpacked sdist must build without access to the monorepo.
        source = canonical if canonical.is_file() else embedded
        if not source.is_file():
            raise FileNotFoundError(
                "The canonical AgentSpec schema is required to build agent-contracts"
            )
        includes = build_data["force_include"]
        if not isinstance(includes, dict):
            raise TypeError("Hatch force_include must be a mapping")
        destination = Path("src") / relative if self.target_name == "sdist" else relative
        includes[str(source)] = destination.as_posix()
