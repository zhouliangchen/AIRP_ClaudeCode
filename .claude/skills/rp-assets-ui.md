---
name: rp-assets-ui
description: Use when an RP turn can benefit from non-blocking image generation or per-card UI customization.
---

## RP Assets And UI

This skill improves immersion after the text turn is safe. It is optional and must be asynchronous in spirit.

## Image Work

- Use `skills/image_generate.py` for generated image assets.
- Store generated assets under the active card folder, not in source-controlled runtime folders.
- Respect local image configuration files and secrets; do not commit them.
- Rebuild frontend content only through the existing runtime helpers.

## Per-Card UI Work

Use per-card files such as `ui_manifest.json`, `.beautify_template.html`, `.beautify.json`, and `.regex_scripts.json`. Do not treat global `skills/styles/index.html` as a card-specific customization layer.

## Delivery Priority

Text delivery comes first. Optional image and UI work must not block `round_deliver.py`, `skills/styles/response.txt`, or browser update completion.
