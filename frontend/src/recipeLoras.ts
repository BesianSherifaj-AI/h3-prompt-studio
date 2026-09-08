import type { Project } from "./model";

export const REF_SPEED_LORA = "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors";
export const FRAME_SPEED_LORA = "minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors";
const modes = new Set(["ref2va", "i2va", "fl2va", "l2va", "t2va"]);

/** Call before changing project.mode; only the known speed adapter follows it. */
export function matchRecipeLoraToMode(project: Project, nextMode: string): void {
  if (!modes.has(project.mode) || !modes.has(nextMode) ||
      (project.mode === "ref2va") === (nextMode === "ref2va")) return;
  const loras = project.comfy_render?.loras;
  if (!Array.isArray(loras)) return;
  const previousRecipe = project.mode === "ref2va" ? REF_SPEED_LORA : FRAME_SPEED_LORA;
  const nextRecipe = nextMode === "ref2va" ? REF_SPEED_LORA : FRAME_SPEED_LORA;
  // Preserve row order, custom strength, switches and all unrelated adapters.
  project.comfy_render.loras = loras.map((lora: any) =>
    lora?.name === previousRecipe ? { ...lora, name: nextRecipe } : lora,
  );
}
