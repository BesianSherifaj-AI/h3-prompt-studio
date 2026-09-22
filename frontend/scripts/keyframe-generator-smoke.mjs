// Isolated production UI regression: all API requests are mocked; no media is generated.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { chromium, expect } from '@playwright/test';

const dist = resolve(dirname(fileURLToPath(import.meta.url)), '../../dist');
const server = createServer(async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = pathname.startsWith('/assets/') ? resolve(dist, '.' + pathname) : resolve(dist, 'index.html');
    if (!file.startsWith(dist + sep)) { res.writeHead(403).end(); return; }
    res.setHeader('Content-Type', ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' })[extname(file)] || 'application/octet-stream');
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise(done => server.listen(0, '127.0.0.1', done));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  page.setDefaultTimeout(6000);
  const errors = [], writes = [];
  page.on('pageerror', error => errors.push(error.message));
  const settings = { assistant_profiles: { studio: { model: 'large', context_length: 8192, ai_memory_mode: 'exclusive' }, game: { model: 'small', context_length: 8192, ai_memory_mode: 'resident_cpu' } } };
  const project = { schema_version: 1, id: 'fixture-project', workspace: 'studio', title: 'Opening scene', mode: 't2va', duration: 5, aspect_ratio: '16:9', profile: 'director', authoring_mode: 'ai', story: { text: 'A lantern lights up.', locked: false }, style: {}, assets: [], subjects: [], shots: [{ id: 'shot', duration: 5, action: 'A lantern glows', setting: 'Garden', camera: {}, visible_subject_ids: [], offscreen_subject_ids: [], dialogue: [] }], soundscape: '', music: '', custom_instructions: '' };
  const first = {id:'reference-one',name:'Character',media_type:'image',role:'first_frame',semantic_role:'character',enabled:true,locked_order:false,description:'Same character',observation:'',approved_observation:'',prompt_tag:'character'};
  project.assets=[first]; project.mode='i2va';
  const mage='mage_flow_edit_turbo_int8_convrot.safetensors';
  let imageRun=null;
  const assetWrites=[];
  await page.route('**/api/**',async route=>{
    const request=route.request(),path=new URL(request.url()).pathname.replace('/api','');
    const body=request.method()==='GET'?null:request.postDataJSON();
    let result={};
    if(path==='/bootstrap')result={token:'a'.repeat(43),project,projects:[project],settings,personas:[]};
    else if(path==='/connections')result={lm:{online:true,models:[]},comfy:[],busy:false,stage:'idle'};
    else if(path==='/compile')result={valid:true,prompt:'A lantern glows.',issues:[],references:[],timeline:[]};
    else if(path==='/projects'){if(body)Object.assign(project,body);result=body||[project];}
    else if(path==='/video/runs')result={runs:[]};
    else if(path==='/production')result={batches:[]};
    else if(path==='/assets/generators')result={generators:[{id:mage,name:'MageFlow',available:true,supports_references:true},{id:'z_image_turbo_bf16.safetensors',name:'Z-Image',available:true}]};
    else if(path==='/asset-runs'&&body){
      assetWrites.push(body); imageRun={id:body.request_id,status:'queued',stage:'Queued image',can_cancel:true};
      await route.fulfill({status:503,json:{detail:'Connection interrupted after submission.'}});return;
    }else if(path.startsWith('/asset-runs/')){
      if(!imageRun){await route.fulfill({status:400,json:{detail:'That image job was not found.'}});return;}
      result=imageRun;
    }else if(path.startsWith('/assets/')){await route.fulfill({status:200,contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9GkAAAAASUVORK5CYII=','base64')});return;}
    await route.fulfill({json:result});
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/studio`);
  await page.getByRole('tab',{name:'Photos',exact:true}).click();
  await page.locator('.keyframe-generator>summary').click();
  const form=page.locator('.keyframe-generator');
  await form.getByLabel('Keyframe model',{exact:true}).selectOption(mage);
  await form.getByLabel('Keyframe prompt',{exact:true}).fill('Keep the same character and lamp; reach toward the switch.');
  await form.getByRole('checkbox',{name:'Character',exact:true}).check();
  await form.getByRole('button',{name:'Generate keyframe',exact:true}).click();
  await expect(form.getByRole('button',{name:'Check saved request',exact:true})).toBeEnabled();
  assert.equal(assetWrites.length,1);
  assert.deepEqual(assetWrites[0].spec.reference_asset_ids,['reference-one']);
  await expect(form.getByLabel('Keyframe model',{exact:true})).toHaveValue(mage);
  await expect(form.getByLabel('Keyframe prompt',{exact:true})).toHaveValue('Keep the same character and lamp; reach toward the switch.');
  const ticket=assetWrites[0].request_id;
  await page.reload();
  await page.getByRole('tab',{name:'Photos',exact:true}).click();
  await page.locator('.keyframe-generator>summary').click();
  await expect(form.getByRole('button',{name:'Check saved request',exact:true})).toBeEnabled();
  await expect(form.getByRole('button',{name:'Generate keyframe',exact:true})).toBeDisabled();
  await expect(form.getByLabel('Keyframe model',{exact:true})).toHaveValue(mage);
  assert.equal(assetWrites.length,1,'Reload only polls; it never repeats the uncertain write');
  imageRun={id:ticket,status:'succeeded',stage:'Image ready',asset:{...first,id:'generated-ending',name:'Ending frame',role:'reference_image'}};
  await form.getByRole('button',{name:'Check saved request',exact:true}).click();
  await expect(form.getByAltText('Generated keyframe: Ending frame')).toBeVisible();
  await form.getByLabel('Add image as',{exact:true}).selectOption('last_frame');
  await form.getByRole('button',{name:'Add to project',exact:true}).click();
  await expect(form.getByText('This image is in your project.',{exact:true})).toBeVisible();
  await expect.poll(()=>project.mode).toBe('fl2va');
  assert.equal(project.assets.find(a=>a.id==='reference-one').role,'first_frame');
  assert.equal(project.assets.find(a=>a.id==='generated-ending').role,'last_frame');
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: reference selection, durable request, uncertain write recovery without retry, draft preserved across reload, add final keyframe, 390px layout.');
}finally{await browser.close();await new Promise(done=>server.close(done));}
