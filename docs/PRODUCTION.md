# Production batches

Open Studio and expand **Production queue**. Save a valid project, add it to a named batch, or select several saved projects in the intended edit order. Creation freezes each project and its render options. Start explicitly; opening a batch or refreshing its status never submits work.

The queue serializes GPU work. Stop prevents future items and asks the existing manager to cancel its owned current job. After restarting Studio, Resume reconciles recorded request IDs. Uncertain submissions remain blocked until their existing receipts resolve. Retry on a definite failed item creates a new attempt; it never overwrites the original receipt.

## Image-to-video automation

`POST /api/production` accepts a UUID `request_id`, `name`, and `items`, with at most 100 entries. Each entry contains a saved `project_id`, optional `render_options`, and optional `image_spec`. The image specification is the same bounded schema as `/api/asset-runs`: prompt, name, semantic_role, model, width, height, seed and prompt_tag. Start with a Text only project or a First frame project without an assigned frame. The completed image becomes that item's first frame. By default every requested image is prepared before video rendering to reduce model switching.

Projects and generated reference images remain available locally. Queued snapshots do not change when the editor changes. `existing_run_id` can reuse a successful take only if its entire frozen project matches the item. `stop_on_error: false` can continue after definite failures; uncertain submissions always stop the queue.

## Export and review

A completely rendered batch can export a joined film or a ZIP of separate MP4s. Export trims each generated clip to its authored duration; source footage is preserved. A film requires compatible video/audio dimensions and stream formats. ZIP works for mixed formats. Its manifest identifies every run and file. Exports are local and contain no automatic social-network upload.

The latest successful export is saved with the batch as `latest_export` (`url`, `filename`, `kind`, `export_id`, `clip_count`). Production queue keeps its download link after refresh or restart, including exports created through the API. Each generated image has a **First frame** link; finished videos also provide a **Source project** download containing their rendered snapshot.

### Crop cuts through the API

An export request can include `edits`, with at most one crop per zero-based item index. For example:

```json
{"kind":"film","edits":[{"index":0,"cut_at":4,"crop":{"x":100,"y":40,"width":400,"height":240}}]}
```

The clip retains its full frame before `cut_at`, then cuts to that crop, scaled back to the source dimensions. A cut at zero crops the whole clip. Cut times snap to the nearest 24 fps frame and must leave at least one frame before the clip ends. Coordinates and sizes must be even integers within the source frame; dimensions must be positive. Source dimensions are checked before transcoding. This example requires a source large enough to contain its crop.

Crop exports preserve the source audio and original media. Their manifest records the normalized edits, and changed edits produce a separate cached export. The Studio buttons export the unedited batch; custom crop cuts currently use the API, and their download appears in the same queue.

Successful rendering proves that a file was generated, not that the intended action, dialogue, identity or continuity is correct. Review videos and sound before publication. A generated first frame is also fallible. Export labels retain that distinction.

CPU/GPU choices and resource handoff follow the existing [assistant profiles](ASSISTANT_PROFILES.md). Production rendering does not silently choose a smaller assistant.

## API operations

- `GET /api/production` and `GET /api/production/{id}`: read status.
- `POST /api/production/{id}/start` or `/resume`: explicit serial execution.
- `POST /api/production/{id}/cancel`: stop future work and owned current job.
- `POST /api/production/{id}/items/{index}/retry`: reset a definite failed item.
- `GET /api/production/{id}/playlist`: ordered receipts and video links.
- `POST /api/production/{id}/export` with `kind: "film"` or `"clips"` and optional `edits`: local export.

All writes require the current session token and retain the app's loopback boundary. Batch state is in `data/production`; normalized media and exports are in `data/production_exports`.
