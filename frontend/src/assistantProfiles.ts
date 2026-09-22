export type AssistantWorkspace = "studio" | "game";
export type AssistantProfile = {
  model: string;
  context_length: number;
  ai_memory_mode: "exclusive" | "resident_cpu" | "resident_small";
};
export const CONTEXT_LENGTHS = [4096, 8192, 12288, 16384, 32768, 65536, 131072, 262144];

/** Older saved settings remain readable until the server migrates them. */
export function assistantProfile(settings: any, workspace: AssistantWorkspace): AssistantProfile {
  const value = settings?.assistant_profiles?.[workspace] || settings || {};
  return {
    model: typeof value.model === "string" ? value.model : "",
    context_length: Number.isInteger(value.context_length) && value.context_length > 0 ? value.context_length : 8192,
    ai_memory_mode: value.ai_memory_mode === "resident_small" || value.ai_memory_mode === "resident_cpu" ? value.ai_memory_mode : "exclusive",
  };
}

export function assistantProfilePatch(settings: any, workspace: AssistantWorkspace, patch: Partial<AssistantProfile>) {
  return { assistant_profiles: { [workspace]: { ...assistantProfile(settings, workspace), ...patch } } };
}

export function connectionSettingsPatch(draft: any, workspace: AssistantWorkspace) {
  return {
    lm_url: draft.lm_url,
    comfy_urls: draft.comfy_urls,
    ...assistantProfilePatch(draft, workspace, {}),
  };
}

export function prepareStatusMessage(result: any, fallback: string): string {
  return typeof result?.message === "string" && result.message.trim() ? result.message : fallback;
}

export function assistantProfileReady(selected: AssistantProfile, active: Partial<AssistantProfile> | null | undefined, loaded: boolean): boolean {
  const mode = (value: string | undefined) => value === 'resident_small' ? 'resident_cpu' : value;
  return loaded && !!active && active.model === selected.model && active.context_length === selected.context_length && mode(active.ai_memory_mode) === mode(selected.ai_memory_mode);
}
