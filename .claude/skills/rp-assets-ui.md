---
name: rp-assets-ui
description: Use when an RP turn can benefit from non-blocking image generation or per-card UI customization.
---

## RP Assets And UI

This skill improves immersion after the text turn is safe. It is optional and must be async-oriented.

## Rules

- Text delivery has priority. 图片生成 and UI customization must be 异步 and 不得阻塞正文交付.
- Use existing adapters instead of ad hoc API calls:

```powershell
python "{ROOT}/skills/image_generate.py" "<card_folder>" --prompt "..." --kind scene|ui_background|portrait --target "..." --async
```

- Generated image metadata belongs in `.card_assets.json` and files under `generated/images/`.
- Per-card UI customization belongs in `ui_manifest.json`, `.beautify_template.html`, `.beautify.json`, `.regex_scripts.json`, or generated card assets.
- Do not edit global `skills/styles/index.html` for a single card's custom atmosphere.
- If image or UI work fails, record the issue and keep the RP loop running.

## When To Trigger

- A scene reaches a stable visual identity.
- A new important character appears and needs a portrait.
- A chapter or location changes enough to justify a background.
- The critic or story stage identifies that visual context would improve immersion.

## Output

Return a short artifact note for the orchestrator:

```json
{
  "started": true,
  "kind": "scene",
  "target": "...",
  "non_blocking": true,
  "notes": ""
}
```
