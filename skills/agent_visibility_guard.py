"""Hidden GM visibility guards for actor-facing runtime fields."""

from __future__ import annotations

import copy
import re
from typing import Any, Iterable

import agent_schemas


HIDDEN_TEXT_KEYS = {
    "ai",
    "gm",
    "gm_only",
    "gm_only_text",
    "hidden",
    "hidden_text",
    "private",
    "private_notes",
    "world_truth",
}
VISIBILITY_GUARD_EXTRA_MARKER_KEYS = {
    "gm_only_text",
    "hidden_text",
    "hidden_truth",
    "internal_state",
    "internal_thoughts",
    "private_memory",
    "private_notes",
}
HIDDEN_MARKER_KEYS = set(agent_schemas.FORBIDDEN_ACTOR_KEYS) | VISIBILITY_GUARD_EXTRA_MARKER_KEYS
HIDDEN_PHRASE_STRIP_CHARS = " \t\r\n.,:;!?。！？；，、："
CJK_FUZZY_SEPARATOR_CHARS = "　.,:;!?。！？；，、：（）()[]【】{}<>《》\"'“”‘’…·-—_"
CJK_FUZZY_SEPARATOR_RE = r"[\s" + re.escape(CJK_FUZZY_SEPARATOR_CHARS) + r"]*"
CJK_CLAUSE_SPLIT_RE = re.compile(r"[\r\n。！？；;，、,]+")
HIDDEN_PHRASE_MAX_CHARS = 160
CJK_HIDDEN_PHRASE_MIN_CHARS = 4
CJK_INSTRUCTION_SUFFIXES = (
    "不要",
    "不得",
    "不能",
    "请勿",
    "不应",
    "不需要",
    "提前透露",
    "透露给玩家",
)
CJK_HIDDEN_LABELS = (
    "隐藏事实",
    "隐藏设定",
    "秘密",
    "世界真相",
    "真相",
    "幕后事实",
    "GM知道",
    "GM已知",
)
CJK_HIDDEN_PREFIX_RE = re.compile(
    r"^(?:"
    + "|".join(re.escape(label) for label in sorted(CJK_HIDDEN_LABELS, key=len, reverse=True))
    + r")(?:是|为|[:：]|\s+)"
)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _string_leaves(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, dict):
        texts = []
        for child in value.values():
            texts.extend(_string_leaves(child))
        return texts
    if isinstance(value, list):
        texts = []
        for child in value:
            texts.extend(_string_leaves(child))
        return texts
    return []


def _canonical_tokens(text: str) -> list[str]:
    raw = str(text or "")
    acronym_separated = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", raw)
    camel_separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", acronym_separated)
    return re.findall(r"[a-z0-9]+", camel_separated.lower())


HIDDEN_MARKER_TOKENS = {
    marker: tuple(_canonical_tokens(marker))
    for marker in HIDDEN_MARKER_KEYS
}


def _has_non_ascii_text(value: str) -> bool:
    return any(ord(char) > 127 and not char.isspace() for char in value)


def _is_cjk_char(char: str) -> bool:
    return (
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        or "\u3040" <= char <= "\u30ff"
        or "\uac00" <= char <= "\ud7af"
    )


def _has_cjk_text(value: str) -> bool:
    return any(_is_cjk_char(char) for char in value)


def _clean_hidden_phrase(value: str) -> str:
    return str(value or "").strip(HIDDEN_PHRASE_STRIP_CHARS)


def _strip_cjk_instruction_suffix(value: str) -> str:
    text = _clean_hidden_phrase(value)
    indexes = [
        index
        for marker in CJK_INSTRUCTION_SUFFIXES
        for index in [text.find(marker)]
        if index > 0
    ]
    if indexes:
        text = text[:min(indexes)]
    return _clean_hidden_phrase(text)


def _strip_cjk_hidden_prefix(value: str) -> str:
    text = _clean_hidden_phrase(value)
    return _clean_hidden_phrase(CJK_HIDDEN_PREFIX_RE.sub("", text, count=1))


def _cjk_hidden_clause_candidates(value: str) -> set[str]:
    candidates = set()
    if not _has_cjk_text(value):
        return candidates

    fragments = {value}
    fragments.update(CJK_CLAUSE_SPLIT_RE.split(value))
    for fragment in list(fragments):
        for separator in (":", "："):
            if separator in fragment:
                fragments.add(fragment.split(separator, 1)[1])

    for fragment in fragments:
        clean = _strip_cjk_instruction_suffix(_strip_cjk_hidden_prefix(fragment))
        if clean and not any(clean.startswith(marker) for marker in CJK_INSTRUCTION_SUFFIXES):
            candidates.add(clean)
    return candidates


def _hidden_phrases_from_text(value: str) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()
    phrases = {text}
    for separator in (":", "："):
        if separator in text:
            phrases.add(text.split(separator, 1)[1].strip())
    phrases.update(_cjk_hidden_clause_candidates(text))

    words = _canonical_tokens(text)
    for size in range(4, min(8, len(words)) + 1):
        for index in range(0, len(words) - size + 1):
            phrases.add(" ".join(words[index:index + size]))

    for phrase in list(phrases):
        clean = _clean_hidden_phrase(phrase)
        lower = clean.lower()
        for article in ("the ", "a ", "an "):
            if lower.startswith(article):
                phrases.add(clean[len(article):])

    kept = set()
    for phrase in phrases:
        clean = _clean_hidden_phrase(phrase)
        if len(clean) > HIDDEN_PHRASE_MAX_CHARS:
            continue
        if len(clean) >= 12 or (_has_non_ascii_text(clean) and len(clean) >= CJK_HIDDEN_PHRASE_MIN_CHARS):
            kept.add(clean)
    return kept


def _looks_like_hidden_recent_chat_text(value: Any) -> bool:
    text = str(value or "")
    lower = text.lower()
    return (
        _contains_hidden_marker(text)
        or "gm-only" in lower
        or "gm only" in lower
        or "hidden" in lower
        or "private" in lower
    )


def _recent_chat_hidden_texts(input_payload: dict) -> list[str]:
    texts = []
    hidden_recent_chat_keys = HIDDEN_TEXT_KEYS - {"ai", "gm"}
    for item in _list(input_payload.get("recent_chat")):
        if isinstance(item, str):
            if _looks_like_hidden_recent_chat_text(item):
                texts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        hidden_visibility = str(item.get("visibility", "")).lower() in {"gm_only", "hidden", "private"}
        for key, value in item.items():
            key_lower = str(key).lower()
            if hidden_visibility or key_lower in hidden_recent_chat_keys:
                texts.extend(_string_leaves(value))
            elif key_lower in {"ai", "gm"}:
                texts.extend(text for text in _string_leaves(value) if _looks_like_hidden_recent_chat_text(text))
    return texts


def hidden_phrases(input_payload: dict) -> list[str]:
    """Return hidden source phrases that must not reach actor-facing GM fields."""
    routed = _dict(input_payload.get("routed_input"))
    hidden_sources = []
    hidden_sources.extend(_string_leaves(routed.get("user_instruction_channel")))
    hidden_sources.extend(_string_leaves(input_payload.get("user_instruction_channel")))
    hidden_sources.extend(_string_leaves(input_payload.get("gm_only_hidden_settings")))
    hidden_sources.extend(_string_leaves(input_payload.get("hidden_facts")))
    hidden_sources.extend(_string_leaves(input_payload.get("world_truth")))
    hidden_sources.extend(_string_leaves(input_payload.get("gm_only_recent_chat")))
    hidden_sources.extend(_string_leaves(input_payload.get("hidden_recent_chat")))
    hidden_sources.extend(_string_leaves(input_payload.get("private_recent_chat")))
    hidden_sources.extend(_recent_chat_hidden_texts(input_payload))

    phrases = set()
    for text in hidden_sources:
        phrases.update(_hidden_phrases_from_text(text))
    return sorted(phrases, key=lambda phrase: (-len(phrase), phrase))


def _hidden_phrase_pattern(phrase: str) -> re.Pattern:
    if _has_cjk_text(phrase):
        units = [
            char
            for char in phrase
            if not char.isspace() and char not in CJK_FUZZY_SEPARATOR_CHARS
        ]
        if units:
            return re.compile(
                CJK_FUZZY_SEPARATOR_RE.join(re.escape(char) for char in units),
                re.IGNORECASE,
            )
    return re.compile(re.escape(str(phrase)), re.IGNORECASE)


def redact_text(text: str, phrases: Iterable[str]) -> str:
    """Redact every hidden phrase match from text."""
    redacted = str(text or "")
    for phrase in phrases:
        pattern = _hidden_phrase_pattern(str(phrase))
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def _contains_hidden_marker(value: Any) -> bool:
    tokens = _canonical_tokens(str(value or ""))
    if not tokens:
        return False
    for marker_tokens in HIDDEN_MARKER_TOKENS.values():
        if not marker_tokens or len(marker_tokens) > len(tokens):
            continue
        for index in range(0, len(tokens) - len(marker_tokens) + 1):
            if tuple(tokens[index:index + len(marker_tokens)]) == marker_tokens:
                return True
    return False


def _redact_value(value: Any, phrases: Iterable[str], *, redact_markers: bool = False) -> Any:
    if isinstance(value, str):
        redacted = redact_text(value, phrases)
        if redact_markers and _contains_hidden_marker(redacted):
            return "[redacted]"
        return redacted
    if isinstance(value, dict):
        redacted = {}
        for key, child in value.items():
            if redact_markers and _contains_hidden_marker(key):
                continue
            redacted[key] = _redact_value(child, phrases, redact_markers=redact_markers)
        return redacted
    if isinstance(value, list):
        return [_redact_value(child, phrases, redact_markers=redact_markers) for child in value]
    return value


def _redact_optional_field(
    item: Any,
    field: str,
    phrases: Iterable[str],
    *,
    redact_markers: bool = False,
) -> None:
    if isinstance(item, dict) and field in item:
        item[field] = _redact_value(item[field], phrases, redact_markers=redact_markers)


def sanitize_gm_output(gm_output: dict, input_payload: dict) -> dict:
    """Return a sanitized copy of actor/story-facing GM output fields."""
    sanitized = copy.deepcopy(gm_output)
    phrases = hidden_phrases(input_payload if isinstance(input_payload, dict) else {})

    for beat in _list(sanitized.get("scene_beats")):
        _redact_optional_field(beat, "content", phrases, redact_markers=True)
        _redact_optional_field(beat, "metadata", phrases, redact_markers=True)
    for event in _list(sanitized.get("events")):
        _redact_optional_field(event, "content", phrases, redact_markers=True)
        _redact_optional_field(event, "metadata", phrases, redact_markers=True)
    for call in _list(sanitized.get("actor_calls")):
        _redact_optional_field(call, "prompt", phrases, redact_markers=True)
        _redact_optional_field(call, "reason", phrases, redact_markers=True)
        _redact_optional_field(call, "metadata", phrases, redact_markers=True)
    for promotion in _list(sanitized.get("character_promotions")):
        _redact_optional_field(promotion, "reason", phrases, redact_markers=True)
        _redact_optional_field(promotion, "profile_seed", phrases, redact_markers=True)

    return sanitized


__all__ = ["hidden_phrases", "redact_text", "sanitize_gm_output"]
