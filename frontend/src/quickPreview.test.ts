import {expect,it} from 'vitest';
import {quickPreviewSettings,continuationRenderSettings} from './quickPreview';
it('makes a draft without changing the original or dropping extra LoRAs',()=>{
  const p={resolution:'1.0',steps:16,seed:42,loras:[{name:'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors',strength:.9,enabled:true},{name:'facial.safetensors',strength:.3,enabled:true}]};
  const original=structuredClone(p),q=quickPreviewSettings(p,'ref2va');
  expect(q.resolution).toBe('0.3');expect(q.steps).toBe(4);expect(q.seed).toBe(42);
  expect(q.loras[0]).toEqual({...p.loras[0],name:'minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors'});
  expect(q.loras[1]).toEqual(p.loras[1]);expect(p).toEqual(original);
});
it('retains a custom LoRA choice and frame-mode stacks',()=>{
  const render={loras:[{name:'custom.safetensors',strength:1}]};
  expect(quickPreviewSettings(render,'ref2va').loras).toEqual(render.loras);
  expect(quickPreviewSettings(render,'fl2va').loras).toEqual(render.loras);
});
it('quality restores the eight-step reference adapter after a draft and preserves all other options',()=>{
  const source={resolution:'1.0',steps:16,seed:44,save_mmh3:true,continuation_source:'own.mmh3',
    loras:[{name:'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors',strength:.85,enabled:true},
      {name:'face.safetensors',strength:.3,enabled:true},{name:'disabled.safetensors',strength:.4,enabled:false}]};
  const draft=continuationRenderSettings(source,'ref2va','draft');
  const quality=continuationRenderSettings(draft,'ref2va','quality');
  expect(quality).toEqual({...source,resolution:'0.3',steps:8});
  expect(source.steps).toBe(16);expect(draft.steps).toBe(4);
});
it('quality never replaces custom or frame-mode LoRA stacks',()=>{
  const source={steps:4,loras:[{name:'custom.safetensors',strength:.8,enabled:true}]};
  for(const mode of ['ref2va','fl2va','i2va','t2va'])expect(continuationRenderSettings(source,mode,'quality')).toEqual({...source,resolution:'0.3',steps:8});
  const inherited=continuationRenderSettings(source,'ref2va','inherit');
  expect(inherited).toEqual(source);expect(inherited).not.toBe(source);expect(inherited.loras).not.toBe(source.loras);
});
