"""Fingerprints of every prompt module, so a prompt edit cannot go out under an old version tag."""

import hashlib
import importlib
import json
from pathlib import Path
from types import ModuleType

REGISTRY_FILE = Path(__file__).with_name("prompt_versions.json")
PROMPT_MODULES = (
    "mabeyn.rag.prompts",
    "mabeyn.agents.prompts",
    "mabeyn.memory.prompts",
    "mabeyn.guard.prompts",
)


def fingerprint(module: ModuleType) -> str:
    """Hash of all upper-case string constants except the version tag itself."""
    parts = sorted(
        (name, value)
        for name, value in vars(module).items()
        if name.isupper() and isinstance(value, str) and name != "PROMPT_VERSION"
    )
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:16]


def current() -> dict[str, str]:
    """Map each module's PROMPT_VERSION to the fingerprint of its current text."""
    versions = {}
    for name in PROMPT_MODULES:
        module = importlib.import_module(name)
        versions[module.PROMPT_VERSION] = fingerprint(module)
    return versions


def recorded() -> dict[str, str]:
    if not REGISTRY_FILE.exists():
        return {}
    loaded: dict[str, str] = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return loaded


def mismatches() -> list[str]:
    known = recorded()
    return [
        f"{version}: text changed without a new version (or the version is not recorded)"
        for version, digest in current().items()
        if known.get(version) != digest
    ]


def main() -> None:
    """Record the current fingerprints. Run after bumping a PROMPT_VERSION."""
    merged = {**recorded(), **current()}
    text = json.dumps(merged, indent=2, sort_keys=True) + "\n"
    REGISTRY_FILE.write_text(text, encoding="utf-8", newline="\n")
    print(f"recorded {len(merged)} prompt versions in {REGISTRY_FILE.name}")


if __name__ == "__main__":
    main()
