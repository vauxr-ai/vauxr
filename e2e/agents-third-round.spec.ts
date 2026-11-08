import {openAgentAction} from "./auth-fixture";
import {mkdirSync} from 'node:fs';
import {resolve} from 'node:path';
import {test,expect,login} from './auth-fixture';

for(const width of [390,1366]) for(const theme of ['light','dark']) {
  test(`agent type choices, centered dialogs and create retry ${width} ${theme}`,async({page,server})=>{
    await page.setViewportSize({width,height:900});
    await login(page,server);
    await page.getByLabel('Appearance').selectOption(theme);
    await page.getByRole('button',{name:'+ Add Agent',exact:true}).click();
    const modal=page.getByRole('dialog',{name:'Add Agent',exact:true});
    const box=await modal.boundingBox();
    expect(Math.abs(box!.x*2+box!.width-width)).toBeLessThan(2);
    expect(Math.abs(box!.y*2+box!.height-900)).toBeLessThan(2);
    await modal.getByRole('button',{name:'Choose OpenClaw',exact:true}).click();
    await expect(modal.getByRole('region',{name:'Integration enrollment'})).toBeVisible();
    await expect(modal.getByRole('button',{name:'Create agent',exact:true})).toHaveCount(0);
    await modal.getByRole('button',{name:'Choose Vauxr Support',exact:true}).focus();
    await page.keyboard.press('Enter');
    await modal.getByLabel('Agent name').fill('Retry Support');
    const artifacts='/tmp/vauxr-third-round-screenshots';mkdirSync(artifacts,{recursive:true});
    await page.screenshot({path:resolve(artifacts,`add-agent-${width}-${theme}.png`)});
    await page.route('**/api/local-agents',async route=>{
      if(route.request().method()==='POST') await route.fulfill({status:503,contentType:'application/json',body:'{}'});
      else await route.continue();
    });
    await modal.getByRole('button',{name:'Create agent',exact:true}).click();
    await expect(modal.getByRole('alert')).toContainText('Request failed (503)');
    await expect(modal).toBeVisible();
    await expect(modal.getByLabel('Agent name')).toHaveValue('Retry Support');
    await page.unroute('**/api/local-agents');
    const created=page.waitForResponse(r=>r.url().endsWith('/api/local-agents')&&r.request().method()==='POST');
    await modal.getByRole('button',{name:'Create agent',exact:true}).click();
    expect((await created).status()).toBe(201);
    await expect(modal).toHaveCount(0);
    await expect(page.getByRole('heading',{name:'Retry Support', level:3, exact:true})).toBeVisible();
    await expect(page.locator('dialog')).toHaveCount(0);
    await openAgentAction(page, 'Voice pipeline for Retry Support');
    const pipeline=page.getByRole('dialog',{name:'Voice pipeline',exact:true});
    await expect(pipeline.getByRole('tab',{name:'Standard',exact:true}).locator('svg.pipeline-default-check')).toHaveCount(1);
    await pipeline.getByRole('button',{name:'Save pipeline configuration',exact:true}).click();
    await expect(pipeline).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Voice pipeline for Retry Support',exact:true})).toBeFocused();
  });
}

test('deleting an enrolled agent revokes its credential and survives restart',async({page,server})=>{
  test.setTimeout(60000);page.setDefaultTimeout(10000);
  const {requestIntegration,openIntegrations,client,native}=await import('./auth-fixture');
  await login(page,server);
  console.log('Deletion fixture: logged in');
  const {request,secret}=await requestIntegration(server,'Removable integration');
  await openIntegrations(page);
  const enrollment=page.getByRole('region',{name:'Integration enrollment',exact:true});
  await enrollment.getByRole('button',{name:'Refresh integration requests',exact:true}).click();
  const row=enrollment.getByRole('region',{name:`Integration request ${request.request_id}`,exact:true});
  await row.getByLabel('Integration matching code').fill(request.user_code);
  await row.getByRole('checkbox').check();
  await row.getByText('Approve matching integration').click();
  console.log('Deletion fixture: approval submitted');
  const delivery=await client(server,'deliver',secret);
  await client(server,'ack',{...secret,credential:delivery.credential,saved:true});
  expect((await native(server,'/api/devices',undefined,delivery.credential)).status).toBe(200);
  console.log('Deletion fixture: enrollment acknowledged');
  await page.getByRole('button',{name:'Agents',exact:true}).click();
  await page.getByRole('button',{name:'Refresh agents',exact:true}).click();
  const card=page.locator('main li').filter({has:page.getByRole('heading',{name:'Removable integration', level:3, exact:true})});
  await card.locator('summary').click();
  await card.getByRole('button',{name:'Delete agent',exact:true}).click();
  const modal=page.getByRole('dialog',{name:'Delete agent',exact:true});
  await modal.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(card).toBeVisible();
  await card.getByRole('button',{name:'Delete agent',exact:true}).click();
  await modal.getByRole('button',{name:'Delete agent',exact:true}).click();
  console.log('Deletion fixture: delete requested');
  await expect(modal).toHaveCount(0);await expect(card).toHaveCount(0);
  expect((await native(server,'/api/devices',undefined,delivery.credential)).status).toBe(401);
  await server.restart();await page.reload();
  await expect(page.getByRole('heading',{name:'Removable integration', level:3, exact:true})).toHaveCount(0);
  expect((await native(server,'/api/devices',undefined,delivery.credential)).status).toBe(401);
});

test('voice dialog reserves final size while its data loads',async({page,server})=>{
  await page.setViewportSize({width:390,height:844});await login(page,server);
  let release:()=>void=()=>{};
  const pending=new Promise<void>(resolve=>{release=resolve;});
  await page.route('**/api/agents/vauxr-support/speech',async route=>{await pending;await route.continue();});
  await openAgentAction(page,'Voice pipeline for Vauxr Support');
  const modal=page.getByRole('dialog',{name:'Voice pipeline',exact:true});
  await expect(modal.getByRole('status',{name:'Loading voice configuration',exact:true})).toBeVisible();
  const initial=(await modal.boundingBox())!;
  release();await expect(modal.locator('.pipeline-graph')).toBeVisible();
  const final=(await modal.boundingBox())!;
  expect(Math.abs(initial.height-final.height)).toBeLessThan(1);
  expect(Math.abs(initial.y-final.y)).toBeLessThan(1);
  expect(Math.abs(final.y*2+final.height-844)).toBeLessThan(2);
});
