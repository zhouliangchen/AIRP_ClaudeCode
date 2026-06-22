"""Runtime settings and quality metric helpers for current AIRP runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_SETTING_KEYS = {
    "style",
    "wordCount",
    "nsfw",
    "selfRepairMode",
    "allowSourceCodeSelfRepair",
}

REMOVED_SETTING_KEYS = {
    "person",
    "antiImpersonation",
    "bgNpc",
    "charName",
}

DEFAULT_SETTINGS = {
    "style": "北棱特调",
    "wordCount": 600,
    "nsfw": "直白",
    "selfRepairMode": "limited",
    "allowSourceCodeSelfRepair": False,
}

SELF_REPAIR_MODES = {"off", "analysis_only", "limited", "full"}
NSFW_VALUES = {"直白", "舒缓"}


def bool_value(value: Any) -> bool:
    return value is True


def int_value(value: Any, default: int, minimum: int = 100, maximum: int = 6000) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < minimum or number > maximum:
        return default
    return number


def normalize_settings(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    data = raw if isinstance(raw, Mapping) else {}
    style = data.get("style")
    nsfw = data.get("nsfw")
    self_repair_mode = data.get("selfRepairMode")

    return {
        "style": style if isinstance(style, str) and style.strip() else DEFAULT_SETTINGS["style"],
        "wordCount": int_value(data.get("wordCount"), DEFAULT_SETTINGS["wordCount"]),
        "nsfw": nsfw if nsfw in NSFW_VALUES else DEFAULT_SETTINGS["nsfw"],
        "selfRepairMode": self_repair_mode if self_repair_mode in SELF_REPAIR_MODES else DEFAULT_SETTINGS["selfRepairMode"],
        "allowSourceCodeSelfRepair": bool_value(data.get("allowSourceCodeSelfRepair")),
    }


def read_settings(path: str | Path) -> dict[str, Any]:
    settings_path = Path(path)
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    return normalize_settings(raw if isinstance(raw, Mapping) else {})


def write_settings(path: str | Path, settings: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    settings_path = Path(path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return normalized


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def _profile_from_payload(payload: Any, fallback_name: str) -> dict[str, str]:
    data = payload if isinstance(payload, Mapping) else {}
    raw_name = data.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else fallback_name
    raw_title = data.get("title")
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else name
    raw_content = data.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    return {"name": name, "title": title, "content": content, "warning": ""}


def load_style_profile(presets_dir: str | Path, name: str) -> dict[str, str]:
    root = Path(presets_dir)
    selected = name.strip() if isinstance(name, str) and name.strip() else DEFAULT_SETTINGS["style"]
    profile_path = root / f"{selected}.json"
    warning = ""
    profile_name = selected

    if not profile_path.exists():
        warning = f"style profile missing: {selected}"
        profile_name = DEFAULT_SETTINGS["style"]
        profile_path = root / f"{profile_name}.json"

    profile = _profile_from_payload(_read_json(profile_path, {}), profile_name)
    profile["warning"] = warning
    return profile


def extract_tag(text: str, tag: str) -> str:
    if not isinstance(text, str) or not tag:
        return ""
    match = re.search(
        rf"<{re.escape(tag)}\b[^>]*>(.*?)</{re.escape(tag)}>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1) if match else ""


def count_chinese_chars(text: str) -> int:
    if not isinstance(text, str):
        return 0
    return sum(1 for char in text if "\u4e00" <= char <= "\u9fff")


def count_words(text: str) -> int:
    if not isinstance(text, str):
        return 0
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+(?:[._'-][A-Za-z0-9]+)*", text)
    return len(tokens)


def _read_trace(run_dir: Path) -> dict[str, Any]:
    for rel in (
        Path("artifacts") / "interaction.trace.json",
        Path("interaction.trace.json"),
    ):
        path = run_dir / rel
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def build_quality_metrics(
    run_dir: str | Path,
    settings: Mapping[str, Any],
    style_profile: Mapping[str, Any],
    story: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    content = story.get("content", "") if isinstance(story, Mapping) else ""
    visible_content = extract_tag(content, "content") or (content if isinstance(content, str) else "")
    target = int(normalized["wordCount"])
    trace = _read_trace(Path(run_dir))
    exempted = trace.get("stop_reason") == "player_decision" or trace.get("decision_point") is not None

    return {
        "word_count": {
            "target": target,
            "minimum": int(target * 0.8),
            "current": count_words(visible_content),
            "exempted": exempted,
        },
        "chinese_char_count": {
            "current": count_chinese_chars(visible_content),
        },
        "visible_content": {
            "text": visible_content,
        },
        "style_profile": {
            "name": style_profile.get("name", "") if isinstance(style_profile, Mapping) else "",
            "warning": style_profile.get("warning", "") if isinstance(style_profile, Mapping) else "",
        },
        "nsfw": {
            "expected": normalized["nsfw"],
        },
        "output_perspective": {
            "expected": "second_person",
        },
    }
