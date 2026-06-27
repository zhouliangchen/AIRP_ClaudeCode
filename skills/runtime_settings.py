"""Runtime settings and quality metric helpers for current AIRP runs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import player_decision_evidence


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
NSFW_VALUES = {"直白", "舒缓", "关闭"}


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


def _safe_preset_path(root: Path, name: str) -> Path | None:
    if not isinstance(name, str):
        return None
    selected = name.strip()
    if not selected or selected in {".", ".."}:
        return None
    if ":" in selected or "/" in selected or "\\" in selected:
        return None
    if any(part == ".." for part in Path(selected).parts):
        return None
    try:
        root_resolved = root.resolve()
        candidate = (root / f"{selected}.json").resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate


def load_style_profile(presets_dir: str | Path, name: str) -> dict[str, str]:
    root = Path(presets_dir)
    selected = name.strip() if isinstance(name, str) and name.strip() else DEFAULT_SETTINGS["style"]
    warning = ""
    profile_name = selected
    profile_path = _safe_preset_path(root, selected)

    if profile_path is None:
        warning = f"unsafe style profile name: {selected}"
        profile_name = DEFAULT_SETTINGS["style"]
        profile_path = _safe_preset_path(root, profile_name)

    if profile_path is None or not profile_path.exists():
        warning = f"{warning}; style profile missing: {profile_name}".strip("; ")
        profile_name = DEFAULT_SETTINGS["style"]
        profile_path = _safe_preset_path(root, profile_name)

    payload = _read_json(profile_path, {}) if profile_path is not None else {}
    profile = _profile_from_payload(payload, profile_name)
    profile["warning"] = warning
    return profile


def normalize_prompt_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, Mapping) else {}
    raw_settings = data.get("settings") if isinstance(data.get("settings"), Mapping) else data
    settings = normalize_settings(raw_settings if isinstance(raw_settings, Mapping) else {})
    raw_profile = data.get("style_profile")
    style_profile = _profile_from_payload(raw_profile, settings["style"])
    if isinstance(raw_profile, Mapping):
        warning = raw_profile.get("warning")
        if isinstance(warning, str):
            style_profile["warning"] = warning
    return {
        "settings": settings,
        "style_profile": style_profile,
    }


def build_prompt_payload(settings: Mapping[str, Any] | None, presets_dir: str | Path | None = None) -> dict[str, Any]:
    normalized = normalize_settings(settings)
    if presets_dir is None:
        style_profile = _profile_from_payload({"name": normalized["style"]}, normalized["style"])
    else:
        style_profile = load_style_profile(presets_dir, normalized["style"])
    return normalize_prompt_payload({
        "settings": normalized,
        "style_profile": style_profile,
    })


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


def _read_story_input(run_dir: Path) -> dict[str, Any]:
    for rel in (
        Path("artifacts") / "story.input.json",
        Path("story.input.json"),
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
    story_input = _read_story_input(Path(run_dir))
    exempted = player_decision_evidence.has_valid_player_decision(story_input)

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
