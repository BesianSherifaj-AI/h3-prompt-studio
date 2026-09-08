# Third-party components

H3 Prompt Studio is independently implemented application code. It integrates with separately installed tools; their trademarks, source licenses, model licenses, and output terms remain their own. This repository does not include model weights, ComfyUI source, LM Studio binaries, FFmpeg binaries, or downloaded asset packs.

The frontend depends on React and React DOM (MIT), the React scheduler (MIT), and Lucide icons (ISC with notices for Feather-derived portions). Their full bundled-license notices are preserved under `licenses/`. The build tools and development dependencies are declared in `frontend/package.json` with exact resolutions in `frontend/package-lock.json`; their packages carry their own licenses.

The Python dependencies are declared in `requirements.txt` and pinned in `requirements.lock.txt`. Dependency distributions retain their licenses when installed. These include FastAPI, Uvicorn, HTTPX, LM Studio's Python SDK, Pillow, jsonschema, psutil, and their dependencies. Installation does not change their ownership or license terms.

External runtime projects:

- [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3): model and prompt-format documentation. The app's independent prompt compiler and writing aids do not reproduce the hosted Context-IR service.
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI): local workflow engine, installed separately.
- [MMH3 Media](https://github.com/einhorn13/mmh3_media): stateful H3 generation and media assembly, installed separately.
- [PlagueKind nodes](https://github.com/PlagueKind/ComfyUI-PlagueKind-Nodes): SLA attention integration, installed separately.
- [LM Studio](https://lmstudio.ai/): local prompt-model serving and lifecycle management, installed separately.
- [FFmpeg](https://ffmpeg.org/legal.html): media inspection, preview extraction, and decoding. Licensing depends on the separately installed build and enabled components.

Model filenames and project names identify compatible configurations. They are not grants to redistribute a checkpoint. Consult the original model card and the base model's license before using or distributing models or outputs. Reference-photo and demonstration-media rights are separate from source-code rights; review the accompanying example provenance before reusing them.

The repository owner has not yet selected a license for the original application source. No open-source license grant is implied by this notice or by public source availability.
