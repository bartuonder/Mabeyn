from mabeyn import docs_sync


def test_generated_blocks_match_the_code():
    """Fails when a graph, a limit, a model or a prompt version changed without a doc refresh.

    Fix: `uv run python -m mabeyn.docs_sync`, then review and commit docs/architecture.md.
    """
    text = docs_sync.DOC.read_text(encoding="utf-8")
    assert docs_sync.render(text) == text


def test_every_generated_block_has_a_renderer_and_the_reverse():
    text = docs_sync.DOC.read_text(encoding="utf-8")
    in_doc = {m.group("name") for m in docs_sync.BLOCK.finditer(text)}
    assert in_doc == set(docs_sync.RENDERERS)


def test_every_graph_node_is_described_in_the_prose():
    """Adding a node forces the node tables in the document to be extended."""
    text = docs_sync.DOC.read_text(encoding="utf-8")
    for compiled in (docs_sync.compiled_rag_graph(), docs_sync.compiled_supervisor_graph()):
        missing = [n for n in docs_sync.node_names(compiled) if f"| `{n}` |" not in text]
        assert missing == [], f"describe these nodes in docs/architecture.md: {missing}"


def test_rendering_is_deterministic():
    assert docs_sync.supervisor_graph() == docs_sync.supervisor_graph()
    assert docs_sync.rag_graph() == docs_sync.rag_graph()


def test_the_check_command_reports_drift(tmp_path, monkeypatch):
    stale = tmp_path / "architecture.md"
    stale.write_text("<!-- generated:facts -->\nold\n<!-- /generated -->\n", encoding="utf-8")
    monkeypatch.setattr(docs_sync, "DOC", stale)
    assert docs_sync.main(["--check"]) == 1
    assert docs_sync.main([]) == 0
    assert docs_sync.main(["--check"]) == 0
    assert "Prompt versions" in stale.read_text(encoding="utf-8")
