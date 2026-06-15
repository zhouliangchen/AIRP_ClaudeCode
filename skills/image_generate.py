#!/usr/bin/env python3
"""Generate RP/UI image assets for the active card folder.

Default provider target is OpenAI-compatible Images API using gpt-image-2.
The script is intentionally a CLI adapter so Claude Code can call it via Bash,
and it can later be wrapped by a true MCP server without coupling the RP bridge
runtime to a specific harness configuration.

Usage:
  python skills/image_generate.py <card_folder> --prompt "..." [--kind scene] [--target scene_illustration]

Environment / local config:
  OPENAI_API_KEY        required for real generation unless config file provides api_key
  OPENAI_BASE_URL       optional, defaults to https://api.openai.com/v1
  IMAGE_MODEL           optional, defaults to gpt-image-2
  image_config.local.json or skills/image_config.local.json are supported and gitignored
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


DEFAULT_MODEL = "gpt-image-2"


def _json_out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def _safe_slug(text: str) -> str:
    keep = []
    for ch in text.lower():
        if ch.isalnum():
            keep.append(ch)
        elif ch in "-_ ":
            keep.append("-")
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:48] or "image"


def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _next_id(manifest: dict, kind: str) -> str:
    prefix = _safe_slug(kind)
    count = 1
    for item in manifest.get("images", []) or []:
        if isinstance(item, dict) and str(item.get("id", "")).startswith(prefix + "-"):
            count += 1
    return f"{prefix}-{count:04d}"


def _load_config(card: Path | None = None) -> dict:
    """Load local image API config from gitignored files and environment."""
    here = Path(__file__).resolve().parent
    root = here.parent
    candidates = []
    if card:
        candidates.append(card / "image_config.local.json")
    candidates.extend([
        here / "image_config.local.json",
        root / "image_config.local.json",
        root / ".image_api.json",
    ])
    config = {}
    for path in candidates:
        data = _load_json(path, None)
        if isinstance(data, dict):
            config.update(data)
    if os.environ.get("OPENAI_API_KEY"):
        config["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("OPENAI_BASE_URL"):
        config["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("IMAGE_MODEL"):
        config["model"] = os.environ["IMAGE_MODEL"]
    config.setdefault("base_url", "https://api.openai.com/v1")
    config.setdefault("model", DEFAULT_MODEL)
    return config


def _candidate_generation_urls(base_url: str) -> list[str]:
    base = base_url.rstrip("/")
    urls = []
    if base.endswith("/v1"):
        urls.append(base + "/images/generations")
    else:
        urls.append(base + "/v1/images/generations")
        urls.append(base + "/images/generations")
    # Preserve order while deduping.
    deduped = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _call_openai_images(prompt: str, model: str, size: str, config: dict) -> bytes:
    api_key = config.get("api_key")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY or api_key in image_config.local.json is not set")

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }
    data = json.dumps(payload).encode("utf-8")
    last_error = None
    for url in _candidate_generation_urls(config.get("base_url", "https://api.openai.com/v1")):
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "User-Agent": "AIRP-ClaudeCode/1.0",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            image = (body.get("data") or [{}])[0]
            b64 = image.get("b64_json")
            if b64:
                return base64.b64decode(b64)
            image_url = image.get("url")
            if image_url:
                with urllib.request.urlopen(image_url, timeout=180) as img_resp:
                    return img_resp.read()
            raise RuntimeError("image response contained neither b64_json nor url")
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                detail = ""
            last_error = RuntimeError(f"{url} -> HTTP {e.code}: {detail}")
            continue
        except Exception as e:
            last_error = RuntimeError(f"{url} -> {e}")
            continue
    raise RuntimeError(str(last_error) if last_error else "image generation failed")


def _spawn_async(args) -> dict:
    """Spawn this script detached without --async and return immediately."""
    cmd = [sys.executable, str(Path(__file__).resolve()), args.card_folder]
    for key in ["prompt", "kind", "target", "size"]:
        val = getattr(args, key)
        if val:
            cmd.extend(["--" + key.replace("_", "-"), str(val)])
    if args.model:
        cmd.extend(["--model", args.model])
    if args.dry_run:
        cmd.append("--dry-run")
    card = Path(args.card_folder).resolve()
    log_dir = card / "generated" / "jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("image-job-" + str(int(time.time())) + ".log")
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log_path, "w", encoding="utf-8") as log:
        subprocess.Popen(
            cmd,
            stdout=log,
            stderr=log,
            cwd=str(Path(__file__).resolve().parent.parent),
            creationflags=flags if flags else 0,
            start_new_session=False if flags else True,
        )
    return {"ok": True, "action": "queued", "log": str(log_path), "command": cmd[:3] + ["..."]}


def _refresh_frontend_assets(card: Path) -> dict:
    """Rebuild frontend data so polling browsers see new image assets."""
    result = {"content_js": False, "error": None}
    try:
        skills_dir = Path(__file__).resolve().parent
        if str(skills_dir) not in sys.path:
            sys.path.insert(0, str(skills_dir))
        import handler

        handler.write_content_js(str(card))
        result["content_js"] = True
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description="Generate image assets for an RP card folder")
    parser.add_argument("card_folder")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--kind", default="scene", help="scene, ui_background, portrait, prop, etc.")
    parser.add_argument("--target", default="scene_illustration")
    parser.add_argument("--model", default=None)
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--dry-run", action="store_true", help="write manifest entry without calling the API")
    parser.add_argument("--async", dest="async_job", action="store_true", help="queue detached generation job and return immediately")
    args = parser.parse_args()

    if args.async_job:
        _json_out(_spawn_async(args))

    card = Path(args.card_folder).resolve()
    if not card.exists():
        _json_out({"ok": False, "error": f"card folder not found: {card}"}, 2)

    config = _load_config(card)
    model = args.model or config.get("model", DEFAULT_MODEL)

    gen_dir = card / "generated" / "images"
    gen_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = card / ".card_assets.json"
    manifest = _load_json(manifest_path, {"images": []})
    if not isinstance(manifest, dict):
        manifest = {"images": []}
    manifest.setdefault("images", [])

    image_id = _next_id(manifest, args.kind)
    rel_path = Path("generated") / "images" / f"{image_id}.png"
    out_path = card / rel_path

    try:
        if args.dry_run:
            out_path.write_bytes(b"")
        else:
            image_bytes = _call_openai_images(args.prompt, model, args.size, config)
            out_path.write_bytes(image_bytes)
    except Exception as e:
        _json_out({
            "ok": False,
            "error": str(e),
            "model": model,
            "hint": "Set OPENAI_API_KEY or api_key in image_config.local.json, or use --dry-run for pipeline testing.",
        }, 1)

    item = {
        "id": image_id,
        "kind": args.kind,
        "model": model,
        "prompt": args.prompt,
        "path": rel_path.as_posix(),
        "target": args.target,
        "created_at": int(time.time()),
    }
    manifest["images"].append(item)
    _write_json(manifest_path, manifest)
    frontend = _refresh_frontend_assets(card)

    _json_out({
        "ok": True,
        "asset": item,
        "manifest": str(manifest_path),
        "file": str(out_path),
        "frontend": frontend,
    })


if __name__ == "__main__":
    main()
