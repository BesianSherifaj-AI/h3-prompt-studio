# ComfyUI setup

Prompt Studio connects to an existing local ComfyUI installation. Desktop, portable, and source installs use the same HTTP API; no Pinokio installation is needed.

## Required runtime

Use a ComfyUI build with native MiniMax H3 nodes and install the compatible custom-node packages in that installation's `custom_nodes` directory. Follow their own setup instructions and model licenses:

- [Native ComfyUI H3 guide](https://docs.comfy.org/tutorials/video/minimax/minimax-h3).
- [MMH3 Media](https://github.com/einhorn13/mmh3_media), tested at revision `f4694b97f11f1a2236287e1ed8af18db5ea7d371`, for `.mmh3` state, continuation, prompt inspection, and joining.
- [PlagueKind nodes](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes) providing the `H3SLAAttention` node and compatible Kitchen attention runtime. The recipe uses 0.85 sparsity with audio protection. Merely installing the Python node does not establish that its GPU kernels work.

The current render templates use these exact installed filenames:

```text
models/diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
models/diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
models/text_encoders/qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors
models/vae/minimax_h3_video_vae_fp16.safetensors
models/vae/minimax_h3_audio_vae_fp32.safetensors
models/loras/minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors
models/loras/minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors
```

Optional Ref2VA quick-draft adapter:

```text
models/loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
```

Check [Kijai's H3 files](https://huggingface.co/Kijai/MiniMax-H3_comfy), [experimental H3 files](https://huggingface.co/Kijai/MiniMax-H3-experimental), [LightX2V Turbo](https://huggingface.co/lightx2v/Minimax-h3-Turbo), and [Turbo SLA](https://huggingface.co/lightx2v/Minimax-h3-Turbo-SLA) against the recipe and each repository's instructions. No model weights are redistributed here. Different quantizations or naming conventions need a deliberately adapted template; renaming an incompatible model does not make it compatible.

If your model/runtime differs, prompt authoring and prompt export still work. Direct rendering uses a bounded, tested recipe rather than treating arbitrary ComfyUI model combinations as interchangeable. Advanced users may place reviewed API templates in the repository's `workflows/` directory under the filenames expected by `backend/comfy_transfer.py`; the embedded recipe is used when those files are absent.

## Connect and render

Start ComfyUI, then enter its local base address under **Connection** in Studio. Keep only intended instances. The standard defaults are `http://127.0.0.1:8188`, `http://127.0.0.1:8000`, and `http://127.0.0.1:8010`. Studio checks installed node inputs and model options through `/object_info` and checks the queue before GPU hand-off.

**Generate video** submits a new graph with its own output prefix. **Try another seed** reuses the selected take's saved graph; editing an unrelated ComfyUI tab does not change an existing Studio take. **Save continuation state** is needed for true motion-state continuation and combining.

## Optional in-ComfyUI bridge

Copy this repository's `comfy_extension` folder into your chosen ComfyUI installation's `custom_nodes/H3PromptStudioBridge` folder. Its `__init__.py` must be directly inside that folder. Restart that ComfyUI instance and refresh its web page. The bridge is frontend-only and imports no GPU models.

Open Studio first. On a supported native H3 prompt node, use **Open in Prompt Studio** to import its ordered images and prompt into a separate draft. Studio can send the improved prompt back, or prepare a new workflow with connected images. Applying a prepared workflow opens a new editable graph; it does not silently queue a render.

The bridge supports local ComfyUI origins on ports 8188, 8000, and 8010 and Studio on 8766. Custom ComfyUI ports can use direct rendering through Studio's configured backend connection, but the optional browser bridge needs its origin allowlist updated in both `backend/app.py` and `frontend/src/bridge.ts`. Rebuild the frontend after making that change.

Session-scoped bridge messages verify the origin, receiving window, and session ID. Prepared transfers are short-lived and tied to their intended ComfyUI instance. The bridge cannot browse arbitrary local files or queue arbitrary graphs through the Studio backend.
