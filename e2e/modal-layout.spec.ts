import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { test, expect, login, openAgentAction, seedRealtimeAgent, requestIntegration, client } from './auth-fixture';
import { auditPage } from '../scripts/design-audit.mjs';
import { parse } from '../web-client/node_modules/yaml/dist/index.js';
import { resolve } from 'node:path';
import type { Locator, Page } from '@playwright/test';

const designDir = process.env.VAUXR_DESIGN_CHECK;
const designTokens = designDir ? parse(readFileSync(resolve(import.meta.dirname, '../DESIGN.md'), 'utf8').split('---')[1]) : null;
let sequence = 0;
async function designAudit(page: Page, label: string) {
  if (!designDir) return;
  const rows = (await auditPage(page, designTokens, label)).map(row => ({...row, label}));
  writeFileSync(`${designDir}/${process.pid}-${sequence++}.json`, JSON.stringify(rows));
}

// Check real geometry, including hidden clipping: overflow:hidden is not a fix.
async function fits(dialog: Locator, page: Page) {
  await expect(dialog).toBeVisible();
  await designAudit(page, `${page.viewportSize()!.width}/${await page.locator("html").getAttribute("data-theme")}/${await dialog.evaluate(el => el.getAttribute('aria-label') || document.getElementById(el.getAttribute('aria-labelledby') || '')?.textContent || 'modal')}`);
  const geometry = await dialog.evaluate(root => {
    const r = root.getBoundingClientRect();
    const rendered = (el: Element) => {
      if (!el.getClientRects().length) return false;
      for (let parent=el.parentElement;parent && parent!==root;parent=parent.parentElement) {
        if (parent instanceof HTMLDetailsElement && !parent.open && !parent.querySelector(":scope > summary")?.contains(el)) return false;
      }
      return true;
    };
    const overflowing = [root, ...root.querySelectorAll('*')].filter(el => {
      if (!(el instanceof HTMLElement) || !rendered(el) || ['INPUT','TEXTAREA','SVG'].includes(el.tagName)) return false;
      const s = getComputedStyle(el);
      const namedEllipsis = el.matches('.pipeline-agent-name,.pipeline-agent .pipeline-entity > strong,.agent-voice-modal .modal-body > h3')
        && el.getAttribute('title') === el.textContent && !!el.textContent?.trim()
        && s.overflow === 'hidden' && (s.textOverflow === 'ellipsis' || s.webkitLineClamp === '2');
      if (namedEllipsis) return false;
      if (s.clipPath === 'inset(50%)' || el.classList.contains('sr-only')) return false;
      return (el === root || el.classList.contains('modal-body') || /auto|scroll|hidden|clip/.test(s.overflowY)) && el.clientHeight > 0 && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
    }).map(el => ({tag:el.tagName, classes:el.className, height:el.clientHeight, content:el.scrollHeight}));
    const controls = [...root.querySelectorAll('button,input,select,textarea,[role="tab"]')].filter(rendered).map(el => {
      const b = el.getBoundingClientRect(); return {name:el.getAttribute('aria-label') || el.textContent?.trim(), fits:b.left >= r.left && b.right <= r.right + 1 && b.top >= r.top && b.bottom <= r.bottom + 1};
    }).filter(c => !c.fits);
    return {left:r.left,top:r.top,right:r.right,bottom:r.bottom,overflowing,controls};
  });
  expect.soft(geometry.left).toBeGreaterThanOrEqual(0);
  expect.soft(geometry.top).toBeGreaterThanOrEqual(0);
  expect.soft(geometry.right).toBeLessThanOrEqual(page.viewportSize()!.width);
  expect.soft(geometry.bottom).toBeLessThanOrEqual(page.viewportSize()!.height);
  expect.soft(geometry.overflowing, await dialog.getAttribute('aria-label') || await dialog.textContent()).toEqual([]);
  expect.soft(geometry.controls).toEqual([]);
}

for (const width of (designDir ? [390,768,1366] : [390,1366])) for (const theme of ['light','dark']) test(`every modal fits without inner scrolling ${width} ${theme}`, async ({page,server}) => {
  test.setTimeout(designDir ? 300000 : 90000);page.setDefaultTimeout(8000);
  await page.setViewportSize({width,height:width===390?844:768});
  seedRealtimeAgent(server);
  const storePath=server.data+'/agents.json'; const store=JSON.parse(readFileSync(storePath,'utf8'));store.agents.push({id:'removable-fixture',name:'Removable integration',type:'openclaw',createdAt:'2026-11-08'});writeFileSync(storePath,JSON.stringify(store));
  await server.restart(); await login(page,server);
  await page.getByLabel('Appearance').selectOption(theme);
  if (designDir) {
    for (const screen of ['Agents','Devices','Settings']) {
      await page.getByRole('button',{name:screen,exact:true}).click();
      await designAudit(page, `${width}/${theme}/${screen}`);
      if(screen === 'Settings') {
        const sections = page.getByRole('navigation',{name:'Settings sections'}).getByRole('button');
        for(let i=0;i<await sections.count();i++) { await sections.nth(i).click(); await designAudit(page, `${width}/${theme}/Settings-${i}`); }
      }
    }
    await page.getByRole('button',{name:'Agents',exact:true}).click();
  }
  const shot=async(name:string)=>{mkdirSync('/tmp/vauxr-modal-fit-screenshots',{recursive:true});await page.screenshot({path:`/tmp/vauxr-modal-fit-screenshots/${name}-${width}-${theme}.png`});};
  const close=async(dialog:Locator)=>{await dialog.getByRole('button',{name:'Close dialog',exact:true}).click();await page.keyboard.press('Escape');};
  const pending = await requestIntegration(server,'Pending OpenClaw');
  const second = await requestIntegration(server,'Second OpenClaw');
  await page.getByRole('button',{name:'+ Add Agent',exact:true}).click();
  const add=page.getByRole('dialog',{name:'Add Agent',exact:true});
  await expect(add.getByRole('button',{name:'Choose Vauxr Support',exact:true})).toBeVisible();
  await fits(add,page);
  const help = add.getByRole('link',{name:'How to create a custom agent (opens in a new tab)'});
  await add.getByLabel('Agent name').fill('Preserved draft');
  await help.focus(); await expect(help).toBeFocused();
  await page.route('https://github.com/**', route => route.fulfill({status:200,body:'Guide fixture'}));
  const popupPromise = page.waitForEvent('popup'); await page.keyboard.press('Enter');
  const popup = await popupPromise; await popup.close();
  await expect(add.getByLabel('Agent name')).toHaveValue('Preserved draft');
  await expect(add).toBeVisible();
  await add.getByRole('button',{name:'Choose OpenClaw',exact:true}).click();
  await add.getByRole('button',{name:'Refresh integration requests',exact:true}).click();
  const requestRow=add.getByRole('region',{name:/^Integration request /});await expect(requestRow).toHaveCount(1);
  for(let step=0;step<5;step++) {
    await fits(add,page);await shot(`enrollment-${step}`);
    if(step<4) await add.getByRole('navigation',{name:'Enrollment setup steps',exact:true}).getByRole('button',{name:'Next',exact:true}).click();
  }
  const shownId=(await requestRow.getAttribute('aria-label'))!.replace('Integration request ','');
  const selected=[pending,second].find(item=>item.request.request_id===shownId)!;
  await requestRow.getByLabel('Integration matching code').fill(selected.request.user_code);await requestRow.getByRole('checkbox').check();
  // A click starts approval; native delivery must wait for its durable response.
  const approval = page.waitForResponse(response =>
    new URL(response.url()).pathname === '/api/integrations/v1/approve'
    && response.request().method() === 'POST'
    && response.request().postDataJSON()?.request_id === selected.request.request_id);
  await requestRow.getByRole('button',{name:'Approve matching integration',exact:true}).click();
  expect((await approval).status()).toBe(200);
  const delivery=await client(server,'deliver',selected.secret);await client(server,'ack',{...selected.secret,credential:delivery.credential,saved:true});
  await fits(add,page);
  await add.getByRole('navigation',{name:'Integration requests',exact:true}).getByRole('button',{name:'Next request',exact:true}).click();await fits(add,page);
  await add.getByRole('button',{name:'Choose Vauxr Support',exact:true}).click();await fits(add,page);
  await shot('create'); await close(add);
  await page.getByRole('button',{name:'Edit avatar for Vauxr Support',exact:true}).click();
  const avatar=page.getByRole('dialog',{name:'Edit agent avatar',exact:true});await fits(avatar,page);await shot('avatar');await close(avatar);
  let loadingBox: {height:number,width:number} | undefined;
  let releaseLoading: (() => void) | undefined;
  if(designDir) await page.route('**/api/agents/*/speech', async route => { await new Promise<void>(r => {releaseLoading=r;}); await route.continue(); }, {times:1});
  await openAgentAction(page,'Voice pipeline for Vauxr Support');
  if(designDir) {
    const loadingDialog=page.getByRole('dialog',{name:'Voice pipeline',exact:true});
    await expect(loadingDialog.getByRole('status')).toBeVisible();
    loadingBox = await loadingDialog.boundingBox() || undefined;
    await designAudit(page,`${width}/${theme}/Voice pipeline-loading`);
    releaseLoading!();
  }
  const voice=page.getByRole('dialog',{name:'Voice pipeline',exact:true});await expect(voice.locator('.pipeline-graph')).toBeVisible();await fits(voice,page);await shot('standard');
  if(designDir && loadingBox) {
    const box=await voice.boundingBox();
    writeFileSync(`${designDir}/${process.pid}-${sequence++}.json`,JSON.stringify([{rule:'overflow',status:box && Math.abs(box.height-loadingBox.height)<=1 && Math.abs(box.width-loadingBox.width)<=1?'PASS':'FAIL',detail:'Loading dialog footprint must match populated dialog',fixable:true,label:`${width}/${theme}/loading-footprint`}]));
  }
  if(designDir) {
    await close(voice);
    await page.route('**/api/agents/*/speech', route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Disposable design-check failure'})}),{times:1});
    await openAgentAction(page,'Voice pipeline for Vauxr Support');
    await expect(voice.getByRole('button',{name:/retry/i})).toBeVisible();
    await designAudit(page,`${width}/${theme}/Voice pipeline-error`);
    await close(voice); await openAgentAction(page,'Voice pipeline for Vauxr Support');
    await expect(voice.locator('.pipeline-graph')).toBeVisible();
  }
  for(const label of ['Edit Speech To Text','Edit Text To Speech','Edit Vauxr Support']) {
    const button=voice.getByRole('button',{name:label,exact:true});
    await expect(button).toHaveCount(1); {await button.click();const popup=voice.locator('.pipeline-popover');await fits(popup,page);await page.keyboard.press('Escape');await expect(button).toBeFocused();}
  }
  await close(voice);
  await openAgentAction(page,'Voice pipeline for Synthetic realtime integration');
  await expect(voice.locator('.pipeline-graph')).toBeVisible();await voice.getByRole('tab',{name:/Live/}).click();await fits(voice,page);await shot('live');
  await voice.getByRole('button',{name:'Edit Realtime Provider',exact:true}).click();await fits(voice.locator('.pipeline-popover'),page);await page.keyboard.press('Escape');await close(voice);
  await openAgentAction(page,'Runtime settings for Vauxr Support');
  const runtime=page.getByRole('dialog',{name:'Agent runtime settings',exact:true});await expect(runtime.getByLabel(/^(Support|Model) provider$/)).toBeEnabled();await runtime.getByLabel(/^(Support|Model) provider$/).selectOption('ollama');await fits(runtime,page);await shot('runtime');
  const config=runtime.getByRole('tab',{name:'Configuration',exact:true}); await config.focus();await page.keyboard.press('ArrowRight');const diagnostics=runtime.getByRole('tab',{name:'Diagnostics',exact:true});await expect(diagnostics).toBeFocused();await fits(runtime,page);await shot('runtime-diagnostics');await page.keyboard.press('Home');await expect(config).toBeFocused();await close(runtime);
  await openAgentAction(page,'Runtime settings for Synthetic realtime integration');await runtime.getByRole('tab',{name:'Diagnostics',exact:true}).click();await fits(runtime,page);await close(runtime);
  const row=page.locator('main li').filter({has:page.getByRole('heading',{name:'Removable integration',level:3,exact:true})});await row.locator('summary').click();await row.getByRole('button',{name:'Delete agent',exact:true}).click();const remove=page.getByRole('dialog',{name:'Delete agent',exact:true});await fits(remove,page);
  const cancel=remove.getByRole('button',{name:'Cancel',exact:true});
  const confirm=remove.getByRole('button',{name:'Delete agent',exact:true});
  const c=await cancel.boundingBox(), d=await confirm.boundingBox();
  expect(c && d && Math.abs(c.y+c.height/2-d.y-d.height/2)<1 && c.x+c.width<=d.x).toBeTruthy();
  const trailing=await remove.locator('.modal-actions').evaluate(el=>{const r=el.getBoundingClientRect();const b=el.lastElementChild!.getBoundingClientRect();return Math.abs(r.right-b.right)<1;});
  expect(trailing).toBeTruthy();
  await cancel.click(); await expect(remove).not.toBeVisible();
  await page.getByRole('button',{name:'Connect browser',exact:true}).click();await expect(page.locator('#voice-companion').getByLabel('Conversation agent')).toBeEnabled();
  const expand=page.getByRole('button',{name:/Expand (voice|text) companion/});if(await expand.count()) await expand.click();
  if(width===390){const companion=page.getByRole('dialog',{name:/companion/i});await expect(companion).toHaveCount(1);await fits(companion,page);await shot('companion');}
  await page.getByRole('button',{name:/Collapse (voice|text) companion/}).click();
  await page.getByRole('button',{name:'Devices',exact:true}).click();const card=page.locator('main li').first();await card.getByRole('button').first().click();await card.getByRole('button',{name:'Device Diagnostics',exact:true}).click();const device=page.getByRole('dialog',{name:'Device Diagnostics',exact:true});await fits(device,page);await shot('device-diagnostics');await close(device);
});
