# Manual LM Studio system prompts

Choose one of the eight personas and the Ref2VA or FL2VA file. Paste its full text into the chat system prompt in LM Studio, attach your real reference images in the same order as ComfyUI, and supply your story, duration and exact dialogue. FL2VA needs both first and last images; the instructions ask before changing to first-image-only I2VA.

These plain-text prompts are separate from the app's structured JSON proposal prompts. They use the public H3 field format and keep source facts and dialogue authoritative. Check the model's proposed details before pasting its final H3 prompt into ComfyUI. A chat prompt does not configure conditioning nodes, generate a video, trim native frames, listen to an image, or guarantee visual/audio fidelity.

The exporter also supports I2VA, L2VA and T2VA through `backend.prompts.export_system_prompt(persona, mode)`, including an optional `persona: custom style directions` suffix. The sixteen provided files cover the requested reference and first/last modes.

See the bundled [H3 contract](../CONTRACT.md) for supported conditioning and prompt behavior. This is an independent writing aid, not a reproduction of the hosted Context-IR service.
