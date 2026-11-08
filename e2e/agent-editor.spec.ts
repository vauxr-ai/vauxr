import { createServer, type Socket } from 'node:net';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test, expect, login, seedRealtimeAgent } from './auth-fixture';

const agentId = 'f'.repeat(32);
const originalName = 'Synthetic realtime integration';
const renamed = 'Household acceptance agent';
const artifactDir = '/tmp/vauxr-agents-editor-screenshots';
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAALUlEQVR4nGOsc7vDQEvARFPTRy0YtWDUglELRi0YtWDUglELRi0YtWDUAioCAEG9AeBdi826AAAAAElFTkSuQmCC', 'base64');

// A TCP Wyoming peer, not a replacement for the production HTTP/synthesis handlers.
async function wyoming() {
  const calls: any[] = [];
  const peers = new Set<Socket>();
  const pcm = Buffer.alloc(24000);
  for (let n = 0; n < pcm.length / 2; n++) pcm.writeInt16LE(Math.round(2000 * Math.sin(n * 2 * Math.PI * 440 / 24000)), n * 2);
  const server = createServer(socket => {
    peers.add(socket); socket.on('close', () => peers.delete(socket)); socket.on('error', () => {});
    let buffer = Buffer.alloc(0);
    const send = (type: string, data = {}, payload?: Buffer) => socket.write(Buffer.concat([
      Buffer.from(JSON.stringify({ type, data_length: Buffer.byteLength(JSON.stringify(data)), ...(payload ? { payload_length: payload.length } : {}) }) + '\n'), Buffer.from(JSON.stringify(data)), payload || Buffer.alloc(0),
    ]));
    socket.on('data', chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      while (buffer.includes(10)) {
        const end = buffer.indexOf(10), event = JSON.parse(buffer.subarray(0, end).toString());
        buffer = buffer.subarray(end + 1);
        if (event.type === 'describe') send('info', { tts: [{ name: 'Acceptance Piper', voices: [{name: 'fixture_a'}, {name: 'fixture_b'}] }], asr: [{name:'Acceptance Whisper'}] });
        else if (event.type === 'synthesize') {
          calls.push(event.data);
          send('audio-start', {rate:24000, width:2, channels:1});
          send('audio-chunk', {rate:24000, width:2, channels:1}, pcm.subarray(0, 12000));
          send('audio-chunk', {rate:24000, width:2, channels:1}, pcm.subarray(12000));
          send('audio-stop');
        } else socket.destroy(new Error('Unexpected Wyoming operation'));
      }
    });
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  return { port: (server.address() as any).port as number, calls, pcm,
    close: async () => { for (const peer of peers) peer.destroy(); await new Promise<void>(resolve => server.close(() => resolve())); } };
}

for (const width of [390, 1366]) for (const theme of ['light', 'dark']) {
  test(`agent editor persists identity and keyboard pipeline; real browser WAV ${width} ${theme}`, async ({page, server}) => {
    test.setTimeout(90000);
    const peer = await wyoming();
    try {
      seedRealtimeAgent(server);
      writeFileSync(resolve(server.data, 'speech.json'), JSON.stringify({version:1,
        providers: [
          {id:'fixture_stt',kind:'stt',adapter:'whisper',model:'fixture',host:'127.0.0.1',port:peer.port},
          {id:'fixture_tts',kind:'tts',adapter:'piper',model:'fixture',host:'127.0.0.1',port:peer.port,voices:['fixture_a','fixture_b']},
          {id:'fixture_live',kind:'realtime',adapter:'openai-live',model:'gpt-realtime',host:'api.openai.com',port:443,voices:['ash']},
        ], defaults:{mode:'standard',stt_backend:'fixture_stt',tts_backend:'fixture_tts',voices:{fixture_tts:'fixture_a'},realtime_backend:'fixture_live',realtime_voice:'ash'},devices:{}}));
      await server.restart();
      await page.addInitScript(() => {
        (window as any).decodedSamples = [];
        const original = HTMLMediaElement.prototype.play;
        HTMLMediaElement.prototype.play = function () {
          this.addEventListener('loadedmetadata', () => (window as any).decodedSamples.push({src:this.currentSrc,duration:this.duration}), {once:true});
          return original.call(this);
        };
      });
      await page.setViewportSize({width,height:844});
      await login(page, server);
      await page.getByLabel('Appearance').selectOption(theme);
      await page.getByRole('button',{name:'Agents',exact:true}).click();
      await expect(page.getByRole('heading',{name:'Agents available for use on voice devices',exact:true})).toBeVisible();
      const trigger = page.getByRole('button',{name:`View agent ${originalName}`,exact:true});
      // Use the row's far edge: the stretched action must cover more than its text.
      const box = (await trigger.boundingBox())!;
      await trigger.click({position:{x:box.width - 5,y:box.height / 2}});
      const modal = page.locator('dialog.agent-detail-modal');
      await expect(modal).toHaveAccessibleName(originalName);
      expect(await modal.evaluate(el => el.matches(':modal'))).toBe(true);
      for (let n=0;n<12;n++) { await page.keyboard.press('Tab'); expect(await modal.evaluate(el => el.contains(document.activeElement))).toBe(true); }
      await modal.getByRole('button',{name:'Edit agent name'}).click();
      await modal.getByLabel('Agent name',{exact:true}).fill('Cancelled name');
      await modal.getByRole('button',{name:'Cancel name edit'}).click();
      await expect(modal).toHaveAccessibleName(originalName);
      await modal.getByRole('button',{name:'Edit agent name'}).click();
      await modal.getByLabel('Agent name',{exact:true}).fill(renamed);
      await modal.getByRole('button',{name:'Save name',exact:true}).click();
      await expect(modal).toHaveAccessibleName(renamed);
      const upload = page.waitForResponse(r => r.url().endsWith(`/api/agents/${agentId}/avatar`) && r.request().method()==='POST');
      const pencil = modal.getByRole('button',{name:'Edit agent name'});
      const heading = modal.getByRole('heading',{name:renamed,exact:true});
      const titleBox = (await heading.boundingBox())!, pencilBox = (await pencil.boundingBox())!;
      expect(pencilBox.x - titleBox.x - titleBox.width).toBeLessThan(20);
      await expect(modal.getByText(/Agent ID:|This route is shared|Integration configuration/)).toHaveCount(0);
      const avatarButton = modal.getByRole('button',{name:'Edit agent avatar'});
      await avatarButton.click();
      const avatarDialog = page.getByRole('dialog',{name:'Edit agent avatar',exact:true});
      await page.keyboard.press('Escape');
      await expect(avatarButton).toBeFocused();
      await avatarButton.press('Enter');
      for (let n = 0; n < 6; n++) {
        await page.keyboard.press('Tab');
        expect(await avatarDialog.evaluate(el => el.contains(document.activeElement))).toBe(true);
      }
      mkdirSync(artifactDir,{recursive:true});
      await page.screenshot({path:resolve(artifactDir,`agent-avatar-${width}-${theme}.png`)});
      await avatarDialog.getByLabel('Choose agent avatar').setInputFiles({name:'bad.txt',mimeType:'text/plain',buffer:Buffer.from('invalid')});
      await expect(avatarDialog.getByRole('alert')).toContainText('Choose a PNG');
      await avatarDialog.getByLabel('Choose agent avatar').setInputFiles({name:'safe-fixture.png',mimeType:'image/png',buffer:png});
      await expect(avatarDialog).toHaveCount(0);
      await expect(avatarButton).toBeFocused();
      expect((await upload).status()).toBe(200);
      await expect.poll(() => modal.locator('.agent-avatar img').evaluateAll(images => images.every(image => (image as HTMLImageElement).naturalWidth > 0) && images.length > 0)).toBe(true);
      mkdirSync(artifactDir,{recursive:true});
      await modal.evaluate(el => {el.scrollTop=0;});
      await page.screenshot({path:resolve(artifactDir,`agent-identity-${width}-${theme}.png`)});
      await expect(modal.getByRole('region',{name:'Agent voice',exact:true})).toHaveCount(0);
      await page.keyboard.press('Escape');
      await page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true}).click();
      await expect(modal).toHaveAccessibleName('Voice pipeline');
      const voice = modal.getByRole('region',{name:'Agent voice',exact:true});
      await expect(voice.getByRole('heading',{name:'Voice pipeline',exact:true})).toBeVisible();
      await expect(voice.getByRole('button',{name:'Test selected voice'})).toBeDisabled();
      mkdirSync(artifactDir,{recursive:true});
      await voice.getByRole('tab',{name:'Standard',exact:true}).scrollIntoViewIfNeeded();
      await page.screenshot({path:resolve(artifactDir,`agent-pipeline-${width}-${theme}-standard.png`)});
      await voice.locator('.agent-pipeline').screenshot({path:resolve(artifactDir,`agent-graph-${width}-${theme}-standard.png`)});
      await expect(voice.locator('.pipeline-device .pipeline-entity')).toHaveCount(1);
      await expect(voice.locator('.pipeline-agent .pipeline-entity')).toHaveCount(1);
      await expect(voice.locator('.pipeline-provider')).toHaveCount(2);
      await voice.getByRole('button',{name:`Edit ${renamed}`,exact:true}).click();
      await expect(modal).toHaveAccessibleName(renamed);
      await expect(modal.getByLabel('Agent name',{exact:true})).toHaveValue(renamed);
      await modal.getByRole('button',{name:'Cancel name edit'}).click();
      await page.keyboard.press('Escape');
      await expect(page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true})).toBeFocused();
      await page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true}).click();
      for (let n = 0; n < 6; n++) {
        await page.keyboard.press('Tab');
        expect(await modal.evaluate(el => el.contains(document.activeElement))).toBe(true);
      }
      const deviceDetails = voice.locator('.pipeline-device-info summary').first();
      await deviceDetails.focus(); await page.keyboard.press('Space');
      await expect(voice.locator('.pipeline-device-info').first()).toHaveAttribute('open','');
      await page.keyboard.press('Space');
      await voice.getByRole('button',{name:'Edit Speech To Text',exact:true}).click();
      await expect(voice.getByLabel('STT backend',{exact:true})).toBeFocused();
      const standard = voice.getByRole('tab',{name:'Standard',exact:true});
      await standard.focus(); await page.keyboard.press('ArrowRight');
      await expect(voice.getByRole('tab',{name:'Live / Realtime',exact:true})).toBeFocused();
      await page.screenshot({path:resolve(artifactDir,`agent-pipeline-${width}-${theme}-live.png`)});
      await voice.locator('.agent-pipeline').screenshot({path:resolve(artifactDir,`agent-graph-${width}-${theme}-live.png`)});
      await page.keyboard.press('ArrowLeft'); await expect(standard).toBeFocused();
      const enableLive = voice.getByRole('switch',{name:'Enable Live',exact:true});
      if (await enableLive.isChecked()) {
        await voice.getByRole('switch',{name:'Default Live',exact:true}).check();
        await expect(voice.getByRole('switch',{name:'Default Standard',exact:true})).not.toBeChecked();
      }
      if (await enableLive.isChecked()) { await enableLive.focus(); await page.keyboard.press('Space'); }
      await expect(voice.getByRole('switch',{name:'Default Standard',exact:true})).toBeChecked();
      await voice.getByRole('button',{name:'Save voice configuration'}).click();
      await page.keyboard.press('Escape'); await expect(modal).toHaveCount(0);
      await expect(page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true})).toBeFocused();
      await page.reload();
      await page.getByRole('button',{name:`View agent ${renamed}`,exact:true}).click();
      await expect.poll(() => modal.locator('.agent-avatar img').evaluateAll(images => images.length>0 && images.every(i=>(i as HTMLImageElement).naturalWidth>0))).toBe(true);
      const avatar = await page.request.get(`${server.origin}/api/agents/${agentId}/avatar`);
      expect(avatar.status()).toBe(200); expect(avatar.headers()['content-type']).toContain('image/png'); expect((await avatar.body()).subarray(1,4).toString()).toBe('PNG');
      await page.keyboard.press('Escape');
      await expect(modal).toHaveCount(0);
      await page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true}).click();
      await expect(enableLive).not.toBeChecked();
      await page.keyboard.press('Escape');
      await page.getByRole('button',{name:'Connect browser',exact:true}).click({timeout:5000});
      await expect(page.getByRole('button',{name:'Disconnect',exact:true})).toBeVisible();
      await page.getByRole('button',{name:'Collapse voice companion',exact:true}).click();
      await page.getByRole('button',{name:`Voice pipeline for ${renamed}`,exact:true}).click();
      await voice.getByLabel('Voice',{exact:true}).selectOption('fixture_b');
      const before = readFileSync(resolve(server.data,'speech.json'),'utf8');
      const preview = page.waitForResponse(r => r.url().endsWith('/api/speech/preview') && r.request().method()==='POST');
      await voice.getByRole('button',{name:'Test selected voice'}).click();
      const response = await preview;
      expect(response.status()).toBe(200); expect(response.headers()['content-type']).toContain('audio/wav');
      const wav = await response.body();
      expect(wav.subarray(0,4).toString()).toBe('RIFF'); expect(wav.subarray(8,12).toString()).toBe('WAVE');
      const offset = wav.indexOf(Buffer.from('data')); expect(offset).toBeGreaterThan(0);
      expect(wav.subarray(offset+8)).toEqual(peer.pcm);
      expect(peer.calls).toEqual([{text:'Hello! This is a preview of my voice.',voice:{name:'fixture_b'}}]);
      await expect.poll(() => page.evaluate(() => (window as any).decodedSamples.filter((s:any) => s.src.startsWith('blob:') && s.duration>0).length)).toBeGreaterThan(0);
      expect(readFileSync(resolve(server.data,'speech.json'),'utf8')).toBe(before);
      const bounds = await modal.evaluate(el => { const rect=el.getBoundingClientRect(); return {left:rect.left,right:rect.right,fields:[...el.querySelectorAll('select,input:not([type=file])')].map(f=>{const b=f.getBoundingClientRect();return {left:b.left,right:b.right};})}; });
      expect(bounds.left).toBeGreaterThanOrEqual(0); expect(bounds.right).toBeLessThanOrEqual(width);
      for (const field of bounds.fields) { expect(field.left).toBeGreaterThanOrEqual(bounds.left); expect(field.right).toBeLessThanOrEqual(bounds.right); }
      await voice.getByRole('button',{name:'Test selected voice'}).scrollIntoViewIfNeeded();
      mkdirSync(artifactDir,{recursive:true});
      await page.screenshot({path:resolve(artifactDir,`agent-editor-${width}-${theme}.png`)});
      await page.keyboard.press('Escape');
      const diagnosticsTrigger = page.getByRole('button',{name:`Runtime settings for ${renamed}`,exact:true});
      await diagnosticsTrigger.focus(); await page.keyboard.press('Enter');
      const diagnostics = page.getByRole('dialog',{name:'Agent runtime settings',exact:true});
      for (let n = 0; n < 6; n++) {
        await page.keyboard.press('Tab');
        expect(await diagnostics.evaluate(el => el.contains(document.activeElement))).toBe(true);
      }
      await expect(diagnostics.getByText(`Agent ID: ${agentId}`,{exact:true})).toBeVisible();
      await page.screenshot({path:resolve(artifactDir,`agent-diagnostics-${width}-${theme}.png`)});
      await page.keyboard.press('Escape'); await expect(diagnosticsTrigger).toBeFocused();
    } finally { await peer.close(); }
  });
}
