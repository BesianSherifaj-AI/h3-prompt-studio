import { describe, expect, it } from "vitest";
import ModelPicker, { modelPickerOptions, residentModelOptions } from "./ModelPicker";
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

describe("prompt assistant choices", () => {
  it('never reports stale readiness or allows preparation after a failed connection check',()=>{
    const profile={model:'vision',context_length:8192,ai_memory_mode:'exclusive' as const};
    const html=renderToStaticMarkup(createElement(ModelPicker,{settings:profile,activeProfile:profile,online:true,verificationFailed:true,busy:false,models:[{id:'vision',vision:true,loaded:true}],onChange:()=>{},onRefresh:()=>{},onConnections:()=>{},onLoad:()=>{}}));
    expect(html).toContain('readiness unverified');expect(html).toContain('Reconnect');
    expect(html).not.toContain('Ready · GPU');
    expect(html).toContain('disabled="">Prepare assistant');
    expect(html).toContain('value="vision" selected=""');
  });
  it('filters the local lock to installed Qwen 3.8 27B variants', () => {
    const choices = modelPickerOptions([{id:'qwen3.5-4b',vision:true}, {id:'qwen3.8-127b'},
      {id:'qwen3.8-27b@q4_k_s',vision:true}, {id:'huihui-qwen3.8-27b-abliterated@q4_k_s',vision:true}], '', 'qwen3.8-27b');
    expect(choices.map(choice=>choice.id)).toEqual(['qwen3.8-27b@q4_k_s','huihui-qwen3.8-27b-abliterated@q4_k_s']);
  });
  it('explains the lock and keeps GPU handoff while allowing context tuning', () => {
    const html=renderToStaticMarkup(createElement(ModelPicker,{settings:{model:'qwen3.8-27b@q4_k_s',context_length:16384,ai_memory_mode:'exclusive'},modelPolicy:'qwen3.8-27b',online:true,busy:false,models:[{id:'qwen3.8-27b@q4_k_s',vision:true}],onChange:()=>{},onRefresh:()=>{},onConnections:()=>{}}));
    expect(html).toContain('Qwen 3.8 27B locked');
    expect(html).toContain('never substitutes a smaller model');
    expect(html).toContain('GPU · automatic H3 handoff');
    expect(html).not.toContain('CPU · keep ready');
    expect(html).toContain('value="16384" selected=""');
  });
  it('distinguishes identical display names by quantization and preserves exact values',()=>{
    const choices=modelPickerOptions([
      {id:'qwen3.8-27b@q4_k_s',display_name:'Qwen 3.8 27B',vision:true},
      {id:'qwen3.8-27b@q4_k_m',display_name:'Qwen 3.8 27B',vision:true},
    ]);
    expect(choices[0]).toMatchObject({id:'qwen3.8-27b@q4_k_s',label:'Qwen 3.8 27B · Q4_K_S · Reads photos'});
    expect(choices[1].label).toBe('Qwen 3.8 27B · Q4_K_M · Reads photos');
  });
  it('uses the exact key when friendly names still collide and avoids repeating a visible quant',()=>{
    const choices=modelPickerOptions([{id:'publisher-a/model',name:'Writer'},{id:'publisher-b/model',name:'Writer'},{id:'qwen@q8_0',name:'Qwen Q8_0'}]);
    expect(choices[0].label).toContain('Writer · publisher-a/model');
    expect(choices[1].label).toContain('Writer · publisher-b/model');
    expect(choices[2].label).toBe('Qwen Q8_0 · Photo support unknown');
  });
  it('offers CPU residency for verified vision models up to 8 GB',()=>{
    expect(residentModelOptions([
      {id:'qwen-4b',vision:true,size_bytes:3_000_000_000},
      {id:'qwen-8b',vision:true,size_bytes:7_000_000_000},
      {id:'large',vision:true,size_bytes:16_000_000_000},
      {id:'text',vision:false,size_bytes:2_000_000_000},
      {id:'unknown',vision:true},
    ]).map(model=>model.id)).toEqual(['qwen-4b','qwen-8b']);
  });
  it('shows the active workspace, saved context, and honest loaded status',()=>{
    const html=renderToStaticMarkup(createElement(ModelPicker,{settings:{model:'large',context_length:65536,ai_memory_mode:'exclusive'},workspace:'game',online:true,busy:false,stage:'AI ready · H3 kept loaded',models:[{id:'large',vision:true,loaded:true}],onChange:()=>{},onRefresh:()=>{},onConnections:()=>{},onLoad:()=>{}}));
    expect(html).toContain('Game assistant');
    expect(html).toContain('value="65536" selected=""');
    expect(html).toContain('Model loaded · prepare this profile');
    expect(html).toContain('Prepare assistant');
    expect(html).not.toContain('0.8B');
    expect(html).not.toContain('AI ready · H3 kept loaded');
  });
  it('explains why a text model cannot provide full Game scene inspection',()=>{
    const profile={model:'text',context_length:8192,ai_memory_mode:'exclusive' as const};
    const html=renderToStaticMarkup(createElement(ModelPicker,{settings:profile,activeProfile:profile,workspace:'game',online:true,busy:false,models:[{id:'text',vision:false,loaded:true}],onChange:()=>{},onRefresh:()=>{},onConnections:()=>{}}));
    expect(html).toContain('Full Game requires a vision model');
    expect(html).not.toContain('Ready · GPU');
  });
  it("keeps all installed text, vision and unknown models with honest capability and loaded labels", () => {
    const options = modelPickerOptions([
      { id: "vision", name: "Small vision", vision: true, loaded: true },
      { id: "text", name: "Writer", vision: false },
      { id: "unknown", name: "Unreported", vision: null },
    ], "vision");
    expect(options.map(option => option.id)).toEqual(["vision", "text", "unknown"]);
    expect(options[0].label).toBe("Small vision · Reads photos · loaded");
    expect(options[1].label).toBe("Writer · Text only");
    expect(options[2].label).toBe("Unreported · Photo support unknown");
  });
  it("preserves an unavailable saved selection and removes duplicate discovery entries", () => {
    const options = modelPickerOptions([{ id: "one" }, { id: "one", name: "Duplicate" }], "saved-model");
    expect(options.map(option => option.id)).toEqual(["saved-model", "one"]);
    expect(options[0].missing).toBe(true);
    expect(options[0].label).toContain("not listed");
    expect(modelPickerOptions(undefined, "saved")[0].id).toBe("saved");
    expect(modelPickerOptions()).toEqual([]);
  });
});
