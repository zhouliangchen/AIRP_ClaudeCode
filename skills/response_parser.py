"""Shared response.txt tag parsing and content transforms.

The tagged response format is the single contract between Claude Code output
and handler.py / write_memory.py / round_deliver.py. Centralizing the parsing
prevents drift between these consumers.
"""
import re
import json


CONTRACT_TAGS = (
    "polished_input",
    "content",
    "character_dialogues",
    "derived_content_edits",
    "edit_only",
    "summary",
    "options",
    "tokens",
)
CONTENT_RUNTIME_TAGS = tuple(tag for tag in CONTRACT_TAGS if tag != "content")


def _repair_json_string_controls(text):
    """Escape literal control characters that appear inside JSON strings."""
    out = []
    in_string = False
    escaped = False
    for ch in text or "":
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = not in_string
            continue
        if in_string and ch in "\r\n\t":
            out.append({"\\r": "\\r", "\\n": "\\n", "\\t": "\\t"}.get(repr(ch)[1:-1], "\\n"))
            continue
        out.append(ch)
    return "".join(out)


def _loads_json_relaxed(raw):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_repair_json_string_controls(raw))


def _strip_contract_tags(text, tags):
    cleaned = text or ""
    for tag in tags:
        cleaned = re.sub(rf"<{tag}>.*?</{tag}>", "", cleaned, flags=re.DOTALL)
    return cleaned.strip()


def _clean_content_text(text):
    return _strip_contract_tags(text, CONTENT_RUNTIME_TAGS)


def parse_tokens(raw):
    """Parse a <tokens> block ('key: value' lines) into a dict.
    Handles int, float, and percentage (e.g. 77.4%) values."""
    tokens = {}
    for line in raw.split("\n"):
        line = line.strip()
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip()
        v = v.strip()
        try:
            tokens[key] = int(v)
            continue
        except ValueError:
            pass
        try:
            tokens[key] = float(v.replace("%", ""))
        except ValueError:
            pass
    return tokens


def parse_response(text):
    """Parse response.txt into structured parts.

    Returns a dict with any of: polished_input, content, character_dialogues,
    summary, options, tokens, derived_content_edits.
    The tokens value is a parsed dict; JSON blocks are parsed into Python values;
    others are stripped strings.
    """
    result = {}
    text = text or ""
    for tag in ("derived_content_edits",):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            try:
                parsed = _loads_json_relaxed(raw)
            except json.JSONDecodeError:
                parsed = []
            result[tag] = parsed if isinstance(parsed, list) else []

    current_turn_source = _strip_contract_tags(text, ("derived_content_edits",))
    for tag in CONTRACT_TAGS:
        if tag in ("content", "derived_content_edits"):
            continue
        m = re.search(rf"<{tag}>(.*?)</{tag}>", current_turn_source, re.DOTALL)
        if not m:
            continue
        raw = m.group(1).strip()
        if tag == "tokens":
            result[tag] = parse_tokens(raw)
        elif tag == "character_dialogues":
            try:
                parsed = _loads_json_relaxed(raw)
            except json.JSONDecodeError:
                parsed = []
            result[tag] = parsed if isinstance(parsed, list) else []
        else:
            result[tag] = raw

    content_source = _strip_contract_tags(current_turn_source, CONTENT_RUNTIME_TAGS)
    m = re.search(r"<content>(.*?)</content>", content_source, re.DOTALL)
    if m:
        result["content"] = _clean_content_text(m.group(1).strip())
    if "content" not in result:
        content = _clean_content_text(content_source)
        if content:
            result["content"] = content
    return result


def strip_tags(text, tag):
    """Remove all <tag>...</tag> blocks from text."""
    return re.sub(rf"<{tag}>.*?</{tag}>", "", text, flags=re.DOTALL).strip()


def strip_mvu_commands(text):
    """Strip MVU command lines and UpdateVariable/json_patch blocks from display text."""
    text = re.sub(
        r"^\s*_\.(?:set|insert|assign|remove|unset|delete|add|move)\s*\(.*?\)\s*;?\s*$",
        "", text, flags=re.MULTILINE,
    )
    text = re.sub(r"<json_patch>[\s\S]*?</json_patch>", "", text)
    text = re.sub(r"<UpdateVariable>[\s\S]*?</UpdateVariable>", "", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def text_to_p(text):
    """Convert plain text with blank-line paragraph breaks to <p>-wrapped HTML."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "\n".join(f"<p>{p}</p>" for p in paras)


def extract_options(ai_text):
    """Extract the inner <options> block from AI text, preserving its content."""
    m = re.search(r"<options>(.*?)</options>", ai_text, re.DOTALL)
    return m.group(1).strip() if m else ""
