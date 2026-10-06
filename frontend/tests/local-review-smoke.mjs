// Explicit local QA: creates a named Video draft and imports a public demo.
// Manual saves/playback are real. AI frame/repair controls use a labeled UI fixture.
// Model, analysis, rendering and production starts are forbidden.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const root=path.resolve(import.meta.dirname,'../..'), base='http://127.0.0.1:8766';
const evidence=path.join(root,'test-results/local-review-browser');
const testVideo=path.join(root,'demo/scene-continuity/continuity-30s.mp4');
const poster=fs.readFileSync(path.join(root,'demo/scene-continuity/coin-poster.png'));
fs.mkdirSync(evidence,{recursive:true});
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000}}), page=await context.newPage();
page.setDefaultTimeout(20_000);
const errors=[], forbidden=[];page.on('pageerror',error=>errors.push(error.message));
const boot=await(await context.request.get(base+'/api/bootstrap?workspace=video')).json();
const headers={'X-H3-Token':boot.token}, stamp=Date.now().toString(36);
const title=`Local Review QA ${stamp}`, uploadName=`Browser review QA ${stamp}`;
let assetId='';
const checks={errors,forbidden,layouts:[],ai_controls_use_synthetic_evidence:true};
const fixture=()=>({verdict:'needs_changes',summary:'Synthetic UI fixture: verifies review controls without AI inference.',
  model_key:'Synthetic UI fixture · no model call',improved_prompt:'Two people remain beside the same table. Show the receiver grasp the object before the giver lets go, then hold a clear final pose.',
  issues:[{timestamp:5,severity:'minor',category:'continuity',description:'Synthetic test issue for seeking.',prompt_fix:'Show a clear final hold.'}],
  limitations:['Synthetic UI evidence only. Watch the full public demo; no AI assessment was performed.'],
  samples:[0,5,15,25].map((timestamp,index)=>({timestamp,url:`/api/local-review-ui-fixture/${assetId}/${index}.png`})),media:{duration:30}});
await page.route('**/api/**',async route=>{
  const request=route.request(),pathname=new URL(request.url()).pathname;
  if(request.method()!=='GET' && /\/(?:ai|asset-runs|video\/runs|production)(?:\/|$)|\/(?:analyze|plan|start)$/.test(pathname)){
    forbidden.push(`${request.method()} ${pathname}`);return route.fulfill({status:409,json:{detail:'This QA script forbids model and render operations.'}});
  }
  if(assetId && pathname===`/api/reviews/asset/${assetId}`){
    const response=await route.fetch();
    return response.ok()?route.fulfill({response,json:{...await response.json(),ai:fixture()}}):route.fulfill({response});
  }
  return route.continue();
});
await page.route('**/api/local-review-ui-fixture/**',route=>route.fulfill({contentType:'image/png',body:poster}));
const resume=async()=>{await page.getByRole('button',{name:new RegExp(title)}).click();await expect(page.getByLabel('Project name')).toHaveValue(title);};
const openReview=async()=>{
  const disclosure=page.locator('.simple-editor-tools');
  if(await disclosure.getAttribute('open')===null)await disclosure.locator('summary').click();
  await page.getByRole('button',{name:'Review other videos',exact:true}).click();
  await expect(page.getByRole('button',{name:'Close video review',exact:true})).toBeVisible();
};
try{
  await page.goto(base+'/video');
  await page.locator('.project-home').getByRole('button',{name:'New video',exact:true}).first().click();
  const form=page.locator('.new-project-form');
  await form.getByLabel('Video name',{exact:true}).fill(title);await form.getByLabel(/Your idea/).fill('Two people stand beside a table.');
  const created=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/projects/new' && response.request().method()==='POST');
  await form.getByRole('button',{name:'Create video',exact:true}).click();checks.project_id=(await(await created).json()).id;
  await expect(page.getByRole('heading',{name:'Bring your idea to life.'})).toBeVisible();
  await page.locator('#simple-tab-story').click();await page.locator('.prompt-workbench summary').click();
  await page.getByRole('button',{name:'Prop handoff',exact:true}).click();await page.getByRole('button',{name:'Add direction to my idea'}).click();
  expect(await page.locator('.simple-idea-input').inputValue()).toContain('receiver');
  await expect(page.getByRole('button',{name:'Save now',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Save now',exact:true}).click();
  await expect(page.locator('.project-save-state')).toContainText('All changes saved');await page.reload();await resume();
  expect(await page.locator('.simple-idea-input').inputValue()).toContain('receiver');checks.named_create_and_resume=true;checks.guided_draft_persisted=true;
  await openReview();
  const imported=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/reviews/import' && response.request().method()==='POST',{timeout:120_000});
  await page.locator('.review-library input[type=file]').setInputFiles({name:uploadName+'.mp4',mimeType:'video/mp4',buffer:fs.readFileSync(testVideo)});
  assetId=(await(await imported).json()).id;checks.video_id=assetId;
  await page.getByRole('button',{name:new RegExp(uploadName)}).click();
  const review=page.locator('.video-review');await expect(review.getByLabel('Your notes')).toBeEditable();
  await review.getByRole('button',{name:'Needs changes',exact:true}).click();await review.getByLabel('Your notes').fill('QA saved review: inspect the complete video before approval.');
  await review.getByLabel('Matches the prompt',{exact:true}).selectOption('pass');await review.getByRole('button',{name:'Save review',exact:true}).click();
  await expect(review.getByText('Review saved on this computer.',{exact:true})).toBeVisible();
  const video=page.locator('.review-library-content > video');await video.evaluate(async element=>{element.muted=true;await element.play();});
  await expect.poll(()=>video.evaluate(element=>element.currentTime)).toBeGreaterThan(.15);await video.evaluate(element=>element.pause());checks.browser_upload_and_playback=true;
  await expect(review.locator('.video-review-samples button')).toHaveCount(4);await review.locator('.video-review-samples button').last().click();
  expect(await video.evaluate(element=>element.currentTime)).toBeGreaterThan(3);checks.sample_controls_seek=true;
  await page.screenshot({path:path.join(evidence,'review-desktop.png')});
  for(const width of [390,320]){
    await page.setViewportSize({width,height:844});const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);expect(overflow).toBe(false);
    await expect(page.getByRole('button',{name:'Close video review'})).toBeVisible();await page.screenshot({path:path.join(evidence,`review-${width}.png`)});checks.layouts.push({width,horizontal_overflow:overflow});
  }
  await page.getByRole('button',{name:'Close video review'}).click();await page.reload();await resume();await openReview();
  await page.getByRole('button',{name:new RegExp(uploadName)}).click();await expect(review.getByRole('button',{name:'Needs changes',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(review.getByLabel('Your notes')).toHaveValue(/QA saved review/);checks.manual_review_persisted=true;
  await review.getByRole('button',{name:/Use.*(prompt|writer|idea)/i}).click();await expect(page.getByRole('dialog')).toHaveCount(0);
  expect((await page.locator('.simple-idea-input').inputValue()).length).toBeGreaterThan(30);checks.repair_prompt_to_writer=true;
  await page.screenshot({path:path.join(evidence,'writer-320.png')});expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)).toBe(false);
  await expect(page.getByRole('button',{name:'Save now',exact:true})).toBeEnabled();await page.getByRole('button',{name:'Save now',exact:true}).click();expect(errors).toEqual([]);expect(forbidden).toEqual([]);
}catch(error){await page.screenshot({path:path.join(evidence,'failure.png')});throw error;}
finally{
  if(boot.projects.some(project=>project.id===boot.project.id))await context.request.post(base+'/api/projects/'+boot.project.id+'/activate',{headers,data:{}});
  fs.writeFileSync(path.join(evidence,'result.json'),JSON.stringify(checks,null,2));await browser.close();
}
console.log(JSON.stringify(checks,null,2));
