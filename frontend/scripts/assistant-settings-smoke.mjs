// Isolated browser regression. Every API call is intercepted; no saved user data is read or changed.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const dist=resolve(dirname(fileURLToPath(import.meta.url)),'../../dist');
const server=createServer(async(req,res)=>{
  try {
    const pathname=new URL(req.url,'http://localhost').pathname;
    const file=pathname.startsWith('/assets/') ? resolve(dist,'.'+pathname) : resolve(dist,'index.html');
    if(!file.startsWith(dist+sep)){res.writeHead(403).end();return;}
    res.setHeader('Content-Type',({'.js':'text/javascript','.css':'text/css','.html':'text/html','.svg':'image/svg+xml'})[extname(file)]||'application/octet-stream');
    res.end(await readFile(file));
  }catch {res.writeHead(404).end();}
});
await new Promise(done=>server.listen(0,'127.0.0.1',done));
const browser=await chromium.launch({headless:true});
try {
  const page=await browser.newPage({viewport:{width:1280,height:900}});
  page.setDefaultTimeout(6000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  let settings={lm_url:'http://127.0.0.1:1234/v1',comfy_urls:['http://127.0.0.1:8188'],model:'large',context_length:32768,ai_memory_mode:'exclusive',assistant_profiles:{studio:{model:'large',context_length:32768,ai_memory_mode:'exclusive'},game:{model:'small',context_length:8192,ai_memory_mode:'resident_cpu'}}};
  const models=[{id:'large',name:'Large vision',vision:true,loaded:false,size_bytes:16_000_000_000},{id:'other',name:'Other vision',vision:true,loaded:false,size_bytes:15_000_000_000},{id:'small',name:'Small vision',vision:true,loaded:true,size_bytes:3_000_000_000}];
  const project={schema_version:1,id:'fixture-project',workspace:'studio',title:'Fixture film',mode:'t2va',duration:5,aspect_ratio:'16:9',profile:'director',authoring_mode:'ai',story:{text:'A lantern glows in a quiet garden.',locked:false},style:{},assets:[],subjects:[],shots:[{id:'shot',duration:5,action:'A lantern glows',setting:'Garden',camera:{framing:'medium',movement:'static',height:'eye level',speed:'slow',focus:''},performance:'',final_state:'',visible_subject_ids:[],offscreen_subject_ids:[],dialogue:[],sound:'',transition:'continuous'}],soundscape:'',music:'',custom_instructions:''};
  const saves=[];let failSave=false;
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname.replace('/api','');
    const body=request.method()==='GET'?null:request.postDataJSON();
    let result={};
    if(path==='/bootstrap') result={token:'a'.repeat(43),resource_token:'b'.repeat(43),project,projects:[project],settings,personas:[]};
    else if(path==='/connections') result={lm:{online:true,models},comfy:[],busy:false,stage:'idle',assistant_profiles:settings.assistant_profiles,active_profile:settings.assistant_profiles.game};
    else if(path==='/settings') {
      saves.push(body);
      if(failSave){await route.fulfill({status:400,json:{detail:'Fixture settings save failed.'}});return;}
      settings={...settings,...body,assistant_profiles:{...settings.assistant_profiles,...body.assistant_profiles}};result=settings;
    }else if(path==='/compile')result={valid:true,prompt:'A lantern glows.',issues:[],references:[],timeline:[]};
    else if(path==='/projects')result=body||[project];
    else if(path==='/stories')result={stories:[]};
    else if(path==='/video/runs')result={runs:[]};
    else if(path==='/assets/generators')result={models:[],errors:[]};
    else if(path==='/ai/prepare')result={ready:true,message:'Fixture assistant prepared.'};
    await route.fulfill({json:result});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/studio`);
  const studio=page.getByRole('region',{name:'Studio assistant model'});
  await expect(studio).toBeVisible();
  await expect(studio.getByRole('combobox',{name:'Context',exact:true})).toHaveValue('32768');
  await studio.getByRole('combobox',{name:'Studio assistant',exact:true}).selectOption('other');
  await expect(studio.getByRole('combobox',{name:'Studio assistant',exact:true})).toHaveValue('other');
  await expect(studio.getByRole('combobox',{name:'Context',exact:true})).toHaveValue('32768');
  assert.deepEqual(Object.keys(saves.at(-1).assistant_profiles),['studio']);
  await page.getByRole('button',{name:'Connections',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'Connections & GPU'});
  await dialog.getByRole('combobox',{name:'Prompt assistant model',exact:true}).selectOption('large');
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await expect(studio.getByRole('combobox',{name:'Studio assistant',exact:true})).toHaveValue('other');
  await page.getByRole('button',{name:'Connections',exact:true}).click();
  await expect(dialog.getByRole('combobox',{name:'Prompt assistant model',exact:true})).toHaveValue('other');
  await dialog.getByRole('combobox',{name:'Prompt assistant model',exact:true}).selectOption('large');
  failSave=true;
  await dialog.getByRole('button',{name:'Save connection',exact:true}).click();
  await expect(dialog.getByRole('alert')).toContainText('Fixture settings save failed.');
  await dialog.getByRole('button',{name:'Close',exact:true}).click();
  await expect(studio.getByRole('combobox',{name:'Studio assistant',exact:true})).toHaveValue('other');
  failSave=false;
  await page.getByRole('link',{name:'Game Play & explore'}).click();
  const game=page.getByRole('region',{name:'Game assistant model'});
  await expect(game).toBeVisible();
  assert.ok((await game.boundingBox()).height<230,'Desktop Game assistant keeps room for the preview');
  await expect(game.getByRole('combobox',{name:'Game assistant',exact:true})).toHaveValue('small');
  failSave=true;
  await game.getByRole('combobox',{name:'Context',exact:true}).selectOption('16384');
  await expect(page.locator('.workspace-feedback').getByRole('alert')).toContainText('Fixture settings save failed.');
  await expect(game.getByRole('combobox',{name:'Context',exact:true})).toHaveValue('8192');
  failSave=false;
  await game.getByRole('combobox',{name:'Context',exact:true}).selectOption('16384');
  await expect(game.getByRole('combobox',{name:'Context',exact:true})).toHaveValue('16384');
  assert.deepEqual(Object.keys(saves.at(-1).assistant_profiles),['game']);
  assert.equal(settings.assistant_profiles.studio.context_length,32768);
  await page.reload();
  await expect(game.getByRole('combobox',{name:'Context',exact:true})).toHaveValue('16384');
  await page.getByRole('button',{name:'Game settings',exact:true}).click();
  const editor=page.getByRole('complementary',{name:'Game editor',exact:true});
  await editor.getByRole('tab',{name:'Rendering',exact:true}).click();
  await editor.getByRole('button',{name:'Connection',exact:true}).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('combobox',{name:'Context length',exact:true}).selectOption('32768');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(editor).toBeVisible();
  await editor.getByRole('button',{name:'Close game editor',exact:true}).click();
  await page.setViewportSize({width:390,height:844});
  await expect(game).toBeVisible();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,'Game must fit a narrow viewport');
  await page.getByRole('link',{name:/^Studio(?: Create & direct)?$/}).click();
  await expect(studio).toBeVisible();
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true,'Studio must fit a narrow viewport');
  assert.deepEqual(errors,[],'No browser runtime errors');
  console.log('PASS: independent profiles, context preservation, cancel, failed save, visible Game error, reload, 390px layout, zero runtime errors.');
}finally{await browser.close();await new Promise(done=>server.close(done));}
