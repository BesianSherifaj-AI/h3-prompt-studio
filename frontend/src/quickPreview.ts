const ref8 = 'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors';
const ref4 = 'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors';

export type ContinuationRenderPreset = 'inherit' | 'draft' | 'quality';

/** Presets change sampling choices only; references, seeds and extra LoRAs stay. */
export function continuationRenderSettings(render: Record<string, any> = {}, mode: string, preset: ContinuationRenderPreset = 'inherit') {
  if(preset === 'inherit') return structuredClone(render);
  if(preset === 'draft') return quickPreviewSettings(render,mode);
  const next:Record<string,any>={...structuredClone(render),resolution:'0.3',steps:8};
  if(mode==='ref2va') {
    const loras=Array.isArray(next.loras)?next.loras:[{name:ref8,strength:1,enabled:true}];
    next.loras=loras.map((l:any)=>l.name===ref4?{...l,name:ref8}:l);
  }
  return next;
}

/** A separate draft keeps the user's other LoRAs, strengths and original take. */
export function quickPreviewSettings(render: Record<string, any>, mode: string) {
  const next: Record<string, any> = {...structuredClone(render), resolution:'0.3',steps:4};
  if(mode==='ref2va') {
    const loras=Array.isArray(next.loras)?next.loras:[{name:ref8,strength:1,enabled:true}];
    next.loras=loras.map((l:any)=>l.name===ref8?{...l,name:ref4}:l);
  }
  return next;
}
