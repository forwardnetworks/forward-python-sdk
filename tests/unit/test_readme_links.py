"""The README is the PyPI page, where a relative link goes nowhere."""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_links_are_absolute() -> None:
    targets = re.findall(r"\]\(([^)\s]+)\)|<(https?://[^>]+)>", README.read_text())
    relative = [
        a for a, _ in targets if a and not a.startswith(("http://", "https://", "#", "mailto:"))
    ]
    assert relative == [], f"relative links break on PyPI: {relative}"
