import { expect, test } from 'vitest';
import type { Project } from './model';
import { completedVideoStory } from './videoStory';

const clip=(brief:string,action:string,line:string):Project=>({
  story:{text:brief}, simple:{}, shots:[{action,setting:'Atelier',final_state:'Nora holds the box.',dialogue:[{speaker_id:'mira',text:line}]}],
} as unknown as Project);

test('third clip retains the original story and already-spoken dialogue without mutating source',()=>{
  const first=clip('Mira brings a gift.','Mira gives Nora the box.','This is for you.');
  const second=clip('Nora opens the gift.','Nora opens the lid.','Thank you.');
  second.simple.continuation={previous_story:completedVideoStory(first)};
  const before=structuredClone(second), memory=completedVideoStory(second);
  expect(memory.opening_story).toBe('Mira brings a gift.');
  expect(memory.earlier_clips[0].events[0].dialogue[0].text).toBe('This is for you.');
  expect(memory.shots[0].dialogue[0].text).toBe('Thank you.');
  expect(second).toEqual(before);
  memory.shots[0].dialogue[0].text='changed';
  expect(second.shots[0].dialogue[0].text).toBe('Thank you.');
});

test('long sequences keep the opening and bounded recent history',()=>{
  let current=clip('The original gift.','Mira arrives.','Hello.');
  for(let i=0;i<30;i++){
    const next=clip(`Next ${i}`,`Action ${i}`,'');
    next.simple.continuation={previous_story:completedVideoStory(current)};
    current=next;
  }
  const memory=completedVideoStory(current);
  expect(memory.opening_story).toBe('The original gift.');
  expect(memory.earlier_clips).toHaveLength(20);
  expect(memory.earlier_clips.at(-1).brief).toBe('Next 28');
  expect(memory.brief).toBe('Next 29');
});
