import { test, expect, login } from "./auth-fixture";
for (const width of [390,1366]) test(`isolated source preview and publish at ${width}`, async ({page,server}) => {
  await page.setViewportSize({width,height:900});
  await page.addInitScript(()=>{(window as any).micCalls=0;navigator.mediaDevices.getUserMedia=async()=>{(window as any).micCalls++;throw Error("Mic off");};});
  await login(page,server);
  await page.getByRole("button",{name:"Settings",exact:true}).click();
  const editor=page.getByRole("region",{name:"Interface editor",exact:true});
  const html=editor.getByLabel("HTML source",{exact:true});
  await expect(html).toBeVisible();
  let canaryRequests=0;
  await page.route("**/api/preview-canary**",route=>{canaryRequests++;return route.fulfill({status:200,body:"blocked canary"});});
  await html.fill(`<h2>Safe household view</h2><script>parent.previewEscaped=true;fetch('/api/preview-canary')</script><img src='/api/preview-canary'><meta http-equiv='refresh' content='0;url=/api/preview-canary'><iframe src='/api/preview-canary'></iframe><a href='/api/preview-canary'>No navigation</a><form action='/api/preview-canary'><input autofocus onfocus='parent.previewEscaped=true'></form>`);
  await editor.getByLabel("CSS source",{exact:true}).fill(`@import url('/api/preview-canary');h2{color:purple;background-image:url('/api/preview-canary')}`);
  await editor.getByRole("button",{name:"Preview draft",exact:true}).click();
  const frame=page.frameLocator('iframe[title="Isolated interface preview"]');
  await expect(frame.getByRole("heading",{name:"Safe household view"})).toBeVisible();
  expect(await page.locator('iframe[title="Isolated interface preview"]').getAttribute("sandbox")).toBe("");
  expect(await page.evaluate(()=>{
    const iframe=document.querySelector('iframe[title="Isolated interface preview"]') as HTMLIFrameElement;
    try {void iframe.contentWindow!.document;return false;}catch(error){return error instanceof DOMException && error.name==="SecurityError";}
  })).toBe(true);
  expect(await page.evaluate(()=>!!(window as any).previewEscaped)).toBe(false);
  expect(canaryRequests).toBe(0);
  await editor.getByRole("button",{name:"Publish interface",exact:true}).click();
  await expect(editor).toContainText("Interface published");
  await page.reload();
  await page.getByRole("button",{name:"Settings",exact:true}).click();
  await expect(editor.getByLabel("HTML source",{exact:true})).toContainText("Safe household view");
  await expect(page.frameLocator('iframe[title="Isolated interface preview"]').getByRole("heading",{name:"Safe household view"})).toBeVisible();
  expect(await page.evaluate(()=>(window as any).micCalls)).toBe(0);
  expect(canaryRequests).toBe(0);
  await page.locator('iframe[title="Isolated interface preview"]').scrollIntoViewIfNeeded();
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await expect(page.frameLocator('iframe[title="Isolated interface preview"]').getByRole("heading",{name:"Safe household view"})).toBeVisible();
  console.log("Preview text:",await page.frameLocator('iframe[title="Isolated interface preview"]').locator("body").innerText());
  await page.screenshot({path:`/tmp/vauxr-interface-${width}.png`,fullPage:true});
});
