import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def test_relative_links_in_the_docs_point_at_existing_files():
    broken = []
    for page in sorted((ROOT / "docs").rglob("*.md")):
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (page.parent / target).resolve().exists():
                broken.append(f"{page.relative_to(ROOT)} -> {target}")
    assert not broken, broken
