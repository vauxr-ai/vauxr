import {test,expect,login,seedRealtimeAgent,openAgentAction} from './auth-fixture';
import {auditPage} from '../scripts/design-audit.mjs';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {parse} from '../web-client/node_modules/yaml/dist/index.js';
const tokens=parse(readFileSync(new URL('../DESIGN.md',import.meta.url),'utf8').split('---')[1]);
for(const width of [390,768,1366]) test(`diagnose rendered rules ${width}`,async({page,server})=>{
 await page.setViewportSize({width,height:width===390?844:768});
 seedRealtimeAgent(server);await server.restart();await login(page,server);
 const all=[];
 for(const theme of ['light','dark']) {
  await page.getByLabel('Appearance').selectOption(theme);
  all.push(...await auditPage(page,tokens,`${width}/${theme}/Agents-immediate`));
  await page.mouse.move(0,0); await page.waitForTimeout(250);
  all.push(...await auditPage(page,tokens,`${width}/${theme}/Agents`));
  mkdirSync('/tmp/vauxr-design-triage',{recursive:true});
  await page.waitForTimeout(200);
  await page.screenshot({path:`/tmp/vauxr-design-triage/agents-${width}-${theme}.png`});
 }
 mkdirSync('/tmp/vauxr-design-triage',{recursive:true});
 writeFileSync(`/tmp/vauxr-design-triage/${width}.json`,JSON.stringify(all,null,2));
 expect(all.filter(r=>r.rule==='contrast').some(r=>r.status==='FAIL')).toBe(false);
 expect(all.filter(r=>r.rule==='token-colors').some(r=>r.status==='FAIL')).toBe(false);
});

test('collect foreground pipeline and background scroll evidence',async({page,server})=>{
 await page.setViewportSize({width:768,height:768});
 seedRealtimeAgent(server);await server.restart();await login(page,server);
 await page.getByLabel('Appearance').selectOption('light');
 await openAgentAction(page,'Voice pipeline for Synthetic realtime integration');
 await expect(page.getByRole('dialog',{name:'Voice pipeline',exact:true})).toBeVisible();
 const rows=await auditPage(page,tokens,'768/light/Voice pipeline');
 mkdirSync('/tmp/vauxr-design-triage',{recursive:true});
 writeFileSync('/tmp/vauxr-design-triage/pipeline.json',JSON.stringify(rows,null,2));
 expect(rows.filter(r=>r.rule==='overflow')[1].status).toBe('PASS');
 expect(rows.filter(r=>r.rule==='overflow')[1].detail).toContain('1 visible dialogs');
 expect(rows.find(r=>r.rule==='token-colors')?.status).toBe('PASS');
});

test('collect phone Webhooks and dark expanded companion evidence',async({page,server})=>{
 await page.setViewportSize({width:390,height:844});
 await login(page,server);
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('navigation',{name:'Settings sections'}).getByRole('button',{name:/Webhooks/}).click();
 await page.getByLabel('Appearance').selectOption('light');
 const rows=await auditPage(page,tokens,'390/light/Settings-Webhooks');
 await page.getByRole('button',{name:'Agents',exact:true}).click();
 await page.getByLabel('Appearance').selectOption('dark');
 await page.getByRole('button',{name:'Connect browser',exact:true}).click();
 await expect(page.locator('#voice-companion').getByLabel('Conversation agent')).toBeEnabled();
 const expand=page.getByRole('button',{name:/Expand (voice|text) companion/});
 if(await expand.count()) await expand.click();
 await expect(page.getByRole('dialog',{name:/companion/i})).toHaveCount(1);
 rows.push(...await auditPage(page,tokens,'390/dark/Voice companion'));
 await page.waitForTimeout(200);
 await page.screenshot({path:'/tmp/vauxr-design-triage/companion-390-dark.png'});
 mkdirSync('/tmp/vauxr-design-triage',{recursive:true});
 writeFileSync('/tmp/vauxr-design-triage/phone-states.json',JSON.stringify(rows,null,2));
 expect(rows.filter(r=>['contrast','token-colors'].includes(r.rule)&&r.status==='FAIL')).toEqual([]);
});
