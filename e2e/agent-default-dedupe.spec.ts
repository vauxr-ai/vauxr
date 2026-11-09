import {mkdirSync} from 'node:fs';
import {test, expect, login, seedRealtimeAgent, openAgentAction} from './auth-fixture';

for (const width of [390,1366]) {
  test(`default status is pill-only in list and runtime settings ${width}`, async ({page,server}) => {
    await page.setViewportSize({width,height:width===390?844:768});
    await seedRealtimeAgent(server);
    await login(page,server);
    await page.getByLabel('Appearance').selectOption(width===390?'dark':'light');
    const row=(name:string)=>page.locator('.agent-list > li').filter({has:page.getByRole('heading',{name,exact:true,level:3})});
    const runtime=page.getByRole('dialog',{name:'Agent runtime settings',exact:true});
    const assertDefault=async(surface:ReturnType<typeof row>)=>{
      await expect(surface.locator('.pill').getByText('Default',{exact:true})).toHaveCount(1);
      await expect(surface.getByRole('button',{name:/^(Default|Set as default)$/})).toHaveCount(0);
    };
    await assertDefault(row('Synthetic realtime integration'));
    await openAgentAction(page,'Runtime settings for Synthetic realtime integration');
    await assertDefault(runtime);
    await runtime.getByRole('tab',{name:'Diagnostics',exact:true}).click();
    await expect(runtime.getByText('Default',{exact:true})).toHaveCount(1);
    await page.keyboard.press('Escape');
    await openAgentAction(page,'Runtime settings for Vauxr Support');
    await expect(runtime.locator('.pill').getByText('Not default',{exact:true})).toBeVisible();
    const action=runtime.getByRole('button',{name:'Set as default',exact:true});
    await expect(action).toBeEnabled();
    page.once('dialog',dialog=>dialog.accept());
    await action.click();
    await assertDefault(runtime);
    const artifacts='/home/node/.openclaw/media/default-dedupe';
    mkdirSync(artifacts,{recursive:true});
    await page.screenshot({path:`${artifacts}/runtime-default-${width}.png`});
    // Check the affected modal's bounds without rerunning the exhaustive design matrix.
    expect(await runtime.evaluate(el=>{
      const r=el.getBoundingClientRect();
      return r.x>=0 && r.y>=0 && r.right<=innerWidth && r.bottom<=innerHeight && el.scrollWidth<=el.clientWidth;
    })).toBe(true);
    await page.keyboard.press('Escape');
    await assertDefault(row('Vauxr Support'));
    await expect(row('Synthetic realtime integration').getByRole('button',{name:'Set as default',exact:true})).toBeEnabled();
    await page.reload();
    await assertDefault(row('Vauxr Support'));
    await openAgentAction(page,'Runtime settings for Vauxr Support');
    await assertDefault(runtime);
    await runtime.getByRole('tab',{name:'Diagnostics',exact:true}).click();
    await expect(runtime.getByText('Default',{exact:true})).toHaveCount(1);
  });
}
