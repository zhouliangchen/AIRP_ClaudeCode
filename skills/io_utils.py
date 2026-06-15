"""Shared file/JSON I/O helpers used across the skills pipeline."""
import json
from pathlib import Path


def read_file(path, encoding="utf-8"):
    """Safely read a text file; returns None on any failure."""
    try:
        return Path(path).read_text(encoding=encoding)
    except Exception:
        return None


def read_json(path, default=None):
    """Safely read a JSON file; returns default on any failure."""
    try:
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def write_json(path, data, indent=2):
    """Write data as JSON, creating parent directories as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")


def walk_paths(obj, prefix=""):
    """Recursively list all leaf JSON paths with their current values."""
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lines.extend(walk_paths(v, f"{prefix}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            lines.extend(walk_paths(v, f"{prefix}/{i}"))
    else:
        val = json.dumps(obj, ensure_ascii=False)
        if len(val) > 80:
            val = val[:80] + "..."
        lines.append(f"  {prefix} = {val}")
    return lines
