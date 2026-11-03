from mabeyn import prompt_registry


def test_prompt_text_matches_its_recorded_version():
    """Editing a prompt without bumping its PROMPT_VERSION must fail here.

    Fix: bump PROMPT_VERSION in the prompts module, then run
    `uv run python -m mabeyn.prompt_registry` and commit prompt_versions.json.
    """
    assert prompt_registry.mismatches() == []


def test_the_fingerprint_changes_when_a_prompt_changes(monkeypatch):
    from mabeyn.agents import prompts

    before = prompt_registry.fingerprint(prompts)
    monkeypatch.setattr(prompts, "BLOCKED_REPLY", prompts.BLOCKED_REPLY + " extra")
    assert prompt_registry.fingerprint(prompts) != before


def test_the_version_tag_itself_is_not_part_of_the_fingerprint(monkeypatch):
    from mabeyn.rag import prompts

    before = prompt_registry.fingerprint(prompts)
    monkeypatch.setattr(prompts, "PROMPT_VERSION", "other")
    assert prompt_registry.fingerprint(prompts) == before


def test_every_prompt_module_has_a_version():
    versions = prompt_registry.current()
    assert len(versions) == len(prompt_registry.PROMPT_MODULES)
