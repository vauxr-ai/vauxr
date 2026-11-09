import {test,expect,login,requestIntegration,client} from './auth-fixture';

for (const width of [390,1366]) test(`Add Agent denial uses React confirmation and preserves cancellation ${width}`,async({page,server})=>{
  await page.setViewportSize({width,height:844});
  await login(page,server);
  const {request,secret}=await requestIntegration(server,'Disposable confirmation fixture');
  let nativeDialogs=0,denials=0;
  page.on('dialog',async dialog=>{nativeDialogs++;await dialog.dismiss();});
  page.on('request',request=>{if(request.method()==='POST'&&request.url().endsWith('/integrations/v1/deny'))denials++;});
  await page.getByRole('button',{name:'+ Add Agent',exact:true}).click();
  const add=page.getByRole('dialog',{name:'Add Agent',exact:true});
  await add.getByRole('button',{name:'Choose OpenClaw',exact:true}).click();
  await add.getByRole('button',{name:'Refresh integration requests',exact:true}).click();
  await add.getByRole('navigation',{name:'Enrollment setup steps',exact:true}).getByRole('button',{name:'Next',exact:true}).click();
  const deny=add.getByRole('button',{name:'Deny integration request',exact:true});
  const confirmation=page.getByRole('dialog',{name:'Deny integration request',exact:true});
  await deny.click();await expect(confirmation).toContainText(request.request_id);
  await confirmation.getByRole('button',{name:'Cancel',exact:true}).click();
  await expect(confirmation).toHaveCount(0);await expect(deny).toBeFocused();
  expect(denials).toBe(0);expect((await client(server,'status',secret)).state).toBe('pending');
  await deny.click();await page.keyboard.press('Escape');
  await expect(confirmation).toHaveCount(0);await expect(add).toBeVisible();expect(denials).toBe(0);
  await deny.click();await confirmation.getByRole('button',{name:'Deny request',exact:true}).click();
  await expect(confirmation).toHaveCount(0);
  expect((await client(server,'status',secret)).state).toBe('denied');
  expect(denials).toBe(1);expect(nativeDialogs).toBe(0);
  await expect(add).toBeVisible();
});
