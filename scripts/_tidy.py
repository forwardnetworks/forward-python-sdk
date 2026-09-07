"""Format generated Python before it is written.

Generators emit correct but unpolished code: imports that a particular module
turns out not to need, lines past the limit. Formatting the text *before*
comparing it with what is on disk keeps generation idempotent, which the CI
freshness check depends on.
"""

from __future__ import annotations

import shutil
import subprocess

__all__ = ["tidy"]

# Import sorting, unused-import removal, and __all__ sorting. Letting the linter
# apply its own ordering avoids generators guessing at it and drifting.
FIX_RULES = "I,F401,RUF022"


def tidy(source: str, filename: str) -> str:
    """Return ``source`` with imports sorted and the file formatted."""
    if shutil.which("ruff") is None:
        return source

    fixed = subprocess.run(
        [
            "ruff",
            "check",
            "--fix-only",
            "--quiet",
            "--select",
            FIX_RULES,
            "--stdin-filename",
            filename,
            "-",
        ],
        input=source,
        capture_output=True,
        text=True,
        check=False,
    )
    body = fixed.stdout if fixed.returncode == 0 and fixed.stdout else source

    formatted = subprocess.run(
        ["ruff", "format", "--quiet", "--stdin-filename", filename, "-"],
        input=body,
        capture_output=True,
        text=True,
        check=False,
    )
    return formatted.stdout if formatted.returncode == 0 and formatted.stdout else body
