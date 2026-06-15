"""Shared response.txt tag parsing and content transforms.

The tagged response format is the single contract between Claude Code output
and handler.py / write_memory.py / round_deliver.py. Centralizing the parsing
prevents drift between these consumers.
"""
import re


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

    Returns a dict with any of: polished_input, content, summary, options, tokens.
    The tokens value is a parsed dict; others are stripped strings.
    """
    result = {}
    for tag in ("polished_input", "content", "summary", "options", "tokens"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            result[tag] = parse_tokens(raw) if tag == "tokens" else raw
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
