import { describe, expect, it } from 'vitest';
import { assistantProfile, assistantProfilePatch, assistantProfileReady, connectionSettingsPatch, prepareStatusMessage } from './assistantProfiles';

const settings = {
  lm_url:'http://127.0.0.1:1234/v1', comfy_urls:['http://127.0.0.1:8188'],
  model:'legacy', context_length:4096, ai_memory_mode:'resident_small',
  assistant_profiles:{studio:{model:'large',context_length:65536,ai_memory_mode:'exclusive'},game:{model:'small',context_length:8192,ai_memory_mode:'resident_cpu'}},
};
describe('independent assistant settings',()=>{
  it('changing a model preserves context and only sends the selected workspace',()=>{
    expect(assistantProfilePatch(settings,'studio',{model:'larger'})).toEqual({assistant_profiles:{studio:{model:'larger',context_length:65536,ai_memory_mode:'exclusive'}}});
    expect(assistantProfile(settings,'game').model).toBe('small');
  });
  it('reads legacy settings without forcing a smaller context',()=>{
    expect(assistantProfile({model:'legacy',context_length:131072,ai_memory_mode:'exclusive'},'game')).toEqual({model:'legacy',context_length:131072,ai_memory_mode:'exclusive'});
  });
  it('connection edits are isolated until commit and never overwrite the other workspace',()=>{
    const draft=structuredClone(settings);
    draft.assistant_profiles.game.model='new-small';
    draft.lm_url='http://localhost:1234/v1';
    const patch=connectionSettingsPatch(draft,'game');
    expect(settings.assistant_profiles.game.model).toBe('small');
    expect(patch).toEqual({lm_url:'http://localhost:1234/v1',comfy_urls:settings.comfy_urls,assistant_profiles:{game:{model:'new-small',context_length:8192,ai_memory_mode:'resident_cpu'}}});
    expect(patch).not.toHaveProperty('model');
  });
  it('does not report ready for an unloaded, stale-context, or wrong-placement instance',()=>{
    const selected=assistantProfile(settings,'game');
    expect(assistantProfileReady(selected,selected,true)).toBe(true);
    expect(assistantProfileReady(selected,selected,false)).toBe(false);
    expect(assistantProfileReady(selected,{...selected,context_length:4096},true)).toBe(false);
    expect(assistantProfileReady(selected,{...selected,ai_memory_mode:'exclusive'},true)).toBe(false);
    expect(assistantProfileReady(selected,null,true)).toBe(false);
  });
  it('uses actual preparation feedback without inventing an unload or vision capability',()=>{
    expect(prepareStatusMessage({message:'CPU assistant remains ready.'},'Assistant prepared.')).toBe('CPU assistant remains ready.');
    expect(prepareStatusMessage({ready:true},'Assistant prepared.')).toBe('Assistant prepared.');
  });
});
