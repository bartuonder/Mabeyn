import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_every_file_the_readme_links_to_exists():
    targets = re.findall(r"\]\(((?:evals|docs|apps|loadtest|tests|mabeyn)/[^)#]+)", README)
    assert targets
    missing = sorted({t for t in targets if not (ROOT / t).exists()})
    assert not missing, missing


def test_every_anchor_the_readme_links_to_is_a_heading():
    headings = {
        re.sub(r"[^a-z0-9 _-]", "", h.lower()).replace(
            " ", "-"
        )  # GitHub keeps the leading hyphen of an emoji
        for h in re.findall(r"^#+ (.+)$", README, flags=re.MULTILINE)
    }
    anchors = set(re.findall(r"\]\(#([^)]+)\)", README))
    assert anchors and not anchors - headings, sorted(anchors - headings)
