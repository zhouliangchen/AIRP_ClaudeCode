#!/usr/bin/env python3
"""Generate RP/UI image assets for the active card folder.

Provider target is configured through AIRP API settings.
The script is intentionally a CLI adapter so Claude Code can call it via Bash,
and it can later be wrapped by a true MCP server without coupling the RP bridge
runtime to a specific harness configuration.

Usage:
  python skills/image_generate.py <card_folder> --prompt "..." [--kind scene] [--target scene_illustration]

Environment / local config:
  Frontend settings, AIRP_IMAGE_GENERATION_* env vars, and local settings are resolved
  by llm_settings using frontend > environment > local priority.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import uuid
import urllib.request
import urllib.error
from pathlib import Path

import llm_settings

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FRONTEND_SETTINGS_PATH = llm_settings.DEFAULT_FRONTEND_SETTINGS_PATH
LOCAL_SETTINGS_PATH = llm_settings.DEFAULT_LOCAL_SETTINGS_PATH


class ReferenceImageNotSupported(RuntimeError):
    """Raised when the configured image provider cannot accept reference images."""


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


def _path_is_within(base: Path, target: Path) -> bool:
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False


def _safe_card_relative_path(card: Path, value: str) -> str:
    raw = str(value).strip()
    if not raw or re.match(r"^[A-Za-z]:", raw):
        raise ValueError(f"path must stay inside card folder: {value}")
    rel = Path(raw.replace("\\", "/"))
    if rel.is_absolute() or rel.anchor or rel.drive or any(part == ".." for part in rel.parts):
        raise ValueError(f"path must stay inside card folder: {value}")
    resolved = (card / rel).resolve()
    if not _path_is_within(card.resolve(), resolved):
        raise ValueError(f"path must stay inside card folder: {value}")
    return rel.as_posix()


def _write_job_status(card: Path, job_id: str | None, payload: dict) -> None:
    if not job_id:
        return
    jobs_dir = card / "generated" / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    safe_job_id = _safe_slug(job_id)
    job_path = jobs_dir / f"{safe_job_id}.json"
    body = dict(payload)
    body.setdefault("job_id", job_id)
    _write_json(job_path, body)


def _manifest_image_keys(item: dict) -> set[str]:
    keys: set[str] = set()
    for field in ("source_job_id", "path", "id"):
        value = str(item.get(field) or "").strip()
        if value:
            keys.add(f"{field}:{value}")
    return keys


def _merge_manifest_image(images: list, item: dict) -> None:
    if not isinstance(item, dict):
        return
    incoming_keys = _manifest_image_keys(item)
    for index, current in enumerate(images):
        if not isinstance(current, dict):
            continue
        if incoming_keys and (_manifest_image_keys(current) & incoming_keys):
            merged = dict(current)
            merged.update(item)
            images[index] = merged
            return
    images.append(dict(item))


def _completed_job_assets(card: Path) -> list[dict]:
    jobs_dir = card / "generated" / "jobs"
    if not jobs_dir.exists():
        return []
    assets: list[dict] = []
    for path in sorted(jobs_dir.glob("*.json")):
        payload = _load_json(path, {})
        if not isinstance(payload, dict) or payload.get("status") != "completed":
            continue
        asset = payload.get("asset")
        if isinstance(asset, dict):
            item = dict(asset)
            item.setdefault("status", "completed")
            item.setdefault("source_job_id", str(payload.get("job_id") or path.stem))
            assets.append(item)
    return assets


def _reconcile_manifest_with_completed_jobs(card: Path, manifest: dict | None = None) -> dict:
    manifest_path = card / ".card_assets.json"
    if manifest is None:
        manifest = _load_json(manifest_path, {"images": []})
    if not isinstance(manifest, dict):
        manifest = {"images": []}
    images = manifest.get("images")
    if not isinstance(images, list):
        images = []
    manifest["images"] = images
    changed = False
    before = [dict(item) if isinstance(item, dict) else item for item in images]
    for item in _completed_job_assets(card):
        _merge_manifest_image(images, item)
    if before != images:
        changed = True
    if changed:
        _write_json(manifest_path, manifest)
    return manifest


def _build_manifest_item(
    *,
    image_id: str,
    kind: str,
    model: str,
    prompt: str,
    rel_path: str | Path,
    target: str,
    created_at: int,
    references: list[str] | None = None,
    job_id: str | None = None,
    characters: list[str] | None = None,
) -> dict:
    item = {
        "id": image_id,
        "kind": kind,
        "model": model,
        "prompt": prompt,
        "path": Path(rel_path).as_posix(),
        "target": target,
        "created_at": created_at,
        "status": "completed",
    }
    if references:
        item["references"] = list(references)
    if job_id:
        item["source_job_id"] = job_id
    if characters:
        item["characters"] = list(characters)
    return item


def _load_config(card: Path | None = None) -> dict:
    """Load image API config from the unified LLM settings provider."""
    settings = llm_settings.read_effective_settings(
        FRONTEND_SETTINGS_PATH,
        local_path=LOCAL_SETTINGS_PATH,
    )
    image_generation = settings.get("image_generation", {})
    config = {}
    if isinstance(image_generation, dict):
        for key in ("base_url", "api_key", "model"):
            value = image_generation.get(key)
            if isinstance(value, str) and value.strip():
                config[key] = value.strip()
    return config


def _candidate_generation_urls(base_url: str) -> list[str]:
    return _candidate_image_urls(base_url, "/images/generations")


def _candidate_edit_urls(base_url: str) -> list[str]:
    return _candidate_image_urls(base_url, "/images/edits")


def _candidate_image_urls(base_url: str, suffix: str) -> list[str]:
    base = base_url.rstrip("/")
    urls = []
    if base.endswith("/v1"):
        urls.append(base + suffix)
    else:
        urls.append(base + "/v1" + suffix)
        urls.append(base + suffix)
    # Preserve order while deduping.
    deduped = []
    for url in urls:
        if url not in deduped:
            deduped.append(url)
    return deduped


def _decode_image_response(body: dict) -> bytes:
    image = (body.get("data") or [{}])[0]
    b64 = image.get("b64_json")
    if b64:
        return base64.b64decode(b64)
    image_url = image.get("url")
    if image_url:
        with urllib.request.urlopen(image_url, timeout=180) as img_resp:
            return img_resp.read()
    raise RuntimeError("image response contained neither b64_json nor url")


def _multipart_image_payload(fields: dict[str, str], references: list[Path]) -> tuple[bytes, str]:
    boundary = "----AIRPImageBoundary" + uuid.uuid4().hex
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )
    for reference in references:
        content_type = mimetypes.guess_type(str(reference))[0] or "application/octet-stream"
        chunks.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"image\"; filename=\"{reference.name}\"\r\n"
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(reference.read_bytes())
        chunks.append(b"\r\n")
    chunks.append((f"--{boundary}--\r\n").encode("utf-8"))
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary


def _reference_error_is_unsupported(status: int | None, detail: str) -> bool:
    lowered = detail.lower()
    if status in {404, 405, 501}:
        return True
    if status in {400, 415, 422}:
        return any(
            marker in lowered
            for marker in (
                "not supported",
                "unsupported",
                "not found",
                "no such endpoint",
                "unknown endpoint",
                "images/edits",
                "image edit",
                "edits endpoint",
            )
        )
    return False


def _call_openai_images(
    prompt: str,
    model: str,
    size: str,
    config: dict,
    *,
    references: list[Path] | None = None,
) -> bytes:
    api_key = config.get("api_key")
    if not api_key:
        raise RuntimeError(
            "AIRP_IMAGE_GENERATION_API_KEY or image_generation.api_key in AIRP LLM settings is not set"
        )
    base_url = config.get("base_url")
    if not base_url:
        raise RuntimeError("AIRP_IMAGE_GENERATION_BASE_URL or image_generation.base_url in AIRP LLM settings is not set")
    if not model:
        raise RuntimeError("AIRP_IMAGE_GENERATION_MODEL or image_generation.model in AIRP LLM settings is not set")

    references = references or []
    last_error = None
    urls = _candidate_edit_urls(base_url) if references else _candidate_generation_urls(base_url)
    for url in urls:
        if references:
            data, content_type = _multipart_image_payload(
                {
                    "model": model,
                    "prompt": prompt,
                    "size": size,
                    "n": "1",
                },
                references,
            )
        else:
            payload = {
                "model": model,
                "prompt": prompt,
                "size": size,
                "n": 1,
            }
            data = json.dumps(payload).encode("utf-8")
            content_type = "application/json"
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": content_type,
                "User-Agent": "AIRP-ClaudeCode/1.0",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return _decode_image_response(body)
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:600]
            except Exception:
                detail = ""
            if references and _reference_error_is_unsupported(e.code, detail):
                last_error = ReferenceImageNotSupported(f"{url} -> HTTP {e.code}: {detail}")
                continue
            last_error = RuntimeError(f"{url} -> HTTP {e.code}: {detail}")
            continue
        except Exception as e:
            last_error = RuntimeError(f"{url} -> {e}")
            continue
    if isinstance(last_error, ReferenceImageNotSupported):
        raise last_error
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
    for reference in getattr(args, "reference", []) or []:
        cmd.extend(["--reference", str(reference)])
    if getattr(args, "output_path", None):
        cmd.extend(["--output-path", str(args.output_path)])
    if getattr(args, "job_id", None):
        cmd.extend(["--job-id", str(args.job_id)])
    for character in getattr(args, "character", []) or []:
        cmd.extend(["--character", str(character)])
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
        _reconcile_manifest_with_completed_jobs(card)
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
    parser.add_argument("--reference", action="append", default=[], help="card-local reference image path")
    parser.add_argument("--output-path", default=None, help="card-local output path override")
    parser.add_argument("--job-id", default=None, help="job id for generated/jobs/<job-id>.json")
    parser.add_argument("--character", action="append", default=[], help="character metadata for the asset manifest")
    parser.add_argument("--dry-run", action="store_true", help="write manifest entry without calling the API")
    parser.add_argument("--async", dest="async_job", action="store_true", help="queue detached generation job and return immediately")
    args = parser.parse_args()

    card = Path(args.card_folder).resolve()
    if not card.exists():
        _json_out({"ok": False, "error": f"card folder not found: {card}"}, 2)

    try:
        references = [_safe_card_relative_path(card, value) for value in args.reference]
        output_path = _safe_card_relative_path(card, args.output_path) if args.output_path else None
    except ValueError as exc:
        _write_job_status(
            card,
            args.job_id,
            {
                "status": "failed",
                "reason": "invalid_path",
                "error": str(exc),
            },
        )
        _json_out({"ok": False, "error": str(exc)}, 2)

    if args.async_job:
        _json_out(_spawn_async(args))

    config = _load_config(card)
    model = args.model or config.get("model", "")

    gen_dir = card / "generated" / "images"
    gen_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = card / ".card_assets.json"
    manifest = _load_json(manifest_path, {"images": []})
    if not isinstance(manifest, dict):
        manifest = {"images": []}
    manifest.setdefault("images", [])
    manifest = _reconcile_manifest_with_completed_jobs(card, manifest)

    image_id = _safe_slug(args.job_id) if output_path and args.job_id else _next_id(manifest, args.kind)
    rel_path = Path(output_path) if output_path else Path("generated") / "images" / f"{image_id}.png"
    out_path = card / rel_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if args.dry_run:
            if not output_path or not out_path.exists():
                out_path.write_bytes(b"")
        else:
            reference_paths = [card / reference for reference in references]
            image_bytes = _call_openai_images(
                args.prompt,
                model,
                args.size,
                config,
                references=reference_paths,
            )
            out_path.write_bytes(image_bytes)
    except ReferenceImageNotSupported as e:
        _write_job_status(
            card,
            args.job_id,
            {
                "status": "deferred",
                "reason": "reference_image_not_supported",
                "error": str(e),
                "references": references,
                "path": rel_path.as_posix(),
            },
        )
        _json_out(
            {
                "ok": False,
                "error": "reference_image_not_supported",
                "status": "deferred",
                "references": references,
            },
            1,
        )
    except Exception as e:
        _write_job_status(
            card,
            args.job_id,
            {
                "status": "deferred",
                "reason": "image_generation_failed",
                "error": str(e),
                "path": rel_path.as_posix(),
            },
        )
        _json_out({
            "ok": False,
            "error": str(e),
            "model": model,
            "hint": "Set AIRP_IMAGE_GENERATION_API_KEY or image_generation.api_key in AIRP API settings, or use --dry-run for pipeline testing.",
        }, 1)

    item = _build_manifest_item(
        image_id=image_id,
        kind=args.kind,
        model=model,
        prompt=args.prompt,
        rel_path=rel_path,
        target=args.target,
        created_at=int(time.time()),
        references=references,
        job_id=args.job_id,
        characters=args.character,
    )
    _merge_manifest_image(manifest["images"], item)
    _write_json(manifest_path, manifest)
    _write_job_status(
        card,
        args.job_id,
        {
            "status": "completed",
            "path": rel_path.as_posix(),
            "asset": item,
        },
    )
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
