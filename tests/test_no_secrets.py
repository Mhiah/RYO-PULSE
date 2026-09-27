"""Hackathon rule: committing a real API key is a disqualification. Fail the build if one sneaks in."""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "data"}
PATTERNS = [
    re.compile(r"(RYO_MCP_KEY|TAVILY_API_KEY|OPENROUTER_API_KEY|API_KEY|TOKEN)[ \t]*=[ \t]*['\"]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(sk-[A-Za-z0-9]{20,}|tvly-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{30,})\b"),
]


def test_no_env_file_committed_and_no_keys():
    assert not (ROOT / ".env").exists() or ".env" in (ROOT / ".gitignore").read_text()
    hits = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts) or path.name == ".env":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pat in PATTERNS:
            hits += [f"{path.relative_to(ROOT)}: {m.group(0)[:12]}…" for m in pat.finditer(text)]
    assert not hits, f"possible secrets: {hits}"
