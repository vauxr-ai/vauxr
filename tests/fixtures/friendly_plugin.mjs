// Optional cross-repository test peer. Reads a built PR38 checkout without modifying it.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';

const [plugin, root, url, channelId, ...ids] = process.argv.slice(2);
const require = createRequire(resolve(plugin, 'package.json'));
assert.equal(JSON.parse(await readFile(resolve(plugin, 'node_modules/openclaw/package.json'), 'utf8')).version,
  '2026.9.3');
const sdk = path => import(pathToFileURL(require.resolve(`openclaw/plugin-sdk/${path}`)));
const { runChannelInboundEvent } = await sdk('channel-inbound');
const { recordInboundSession } = await sdk('conversation-runtime');
const { resolveStorePath, upsertSessionEntry, getSessionEntry, loadSessionStore, sessionDeliveryOrigin } =
  await sdk('session-store-runtime');
const { VauxrBridge } = await import(pathToFileURL(resolve(plugin, 'dist/src/bridge.js')));
// Same test-only, pinned OpenClaw 2026.9.3 gateway projection probe as PR38.
const { l: displayName } = await import(pathToFileURL(
  resolve(plugin, 'node_modules/openclaw/dist/session-utils-list-Bk28ume-.mjs')));
const storePath = resolve(root, 'sessions.json');
const key = id => `agent:assistant:vauxr:${id}`;
const history = id => resolve(root, `${id}.jsonl`);
for (const id of ids) {
  await writeFile(history(id), `existing history for ${id}\n`);
  await upsertSessionEntry({ storePath, sessionKey: key(id), entry: {
    sessionId: `history-${id}`, updatedAt: 1, sessionFile: history(id),
    delivery: { origin: { provider: 'vauxr', surface: 'vauxr', from: id, label: id } },
  } });
}
const output = value => process.stdout.write(JSON.stringify(value) + '\n');
const metaTasks = [], contexts = new Map(), held = new Map();
let eventHandler, run = 0;
const api = {
  config: { session: { store: storePath }, agents: { list: [{ id: 'assistant', default: true }] } },
  logger: { info() {}, debug() {}, warn(message) { throw new Error(message); } },
  runtime: { events: { onAgentEvent(fn) { eventHandler = fn; return () => {}; } }, channel: {
    session: { resolveStorePath, recordInboundSession(params) {
      return recordInboundSession({ ...params, trackSessionMetaTask: task => metaTasks.push(task) });
    } },
    inbound: { async run(params) {
      try {
      await runChannelInboundEvent(params);
      await Promise.all(metaTasks.splice(0));
      const ctx = contexts.get(params.raw.deviceId), id = ctx.SenderId;
      const row = getSessionEntry({ storePath, sessionKey: key(id) });
      assert.equal(ctx.SessionKey, key(id));
      for (const field of ['From', 'SenderId', 'SenderName']) assert.equal(ctx[field], id);
      assert.equal(row.sessionId, `history-${id}`);
      assert.equal(await readFile(history(id), 'utf8'), `existing history for ${id}\n`);
      assert.equal(sessionDeliveryOrigin(row).from, id);
      assert.deepEqual(Object.keys(loadSessionStore(storePath)).sort(), ids.map(key).sort());
      output({ event: 'turn', id, label: sessionDeliveryOrigin(row).label,
        display: displayName(key(id), row), sessionKey: ctx.SessionKey });
      } catch (error) { console.error(error); throw error; }
    } },
    reply: { createReplyDispatcherWithTyping() { return { dispatcher: {} }; },
      async dispatchReplyFromConfig(args) {
        const ctx = args.ctx, runId = `sdk-${++run}`;
        contexts.set(ctx.SenderId, ctx);
        args.replyOptions.onAgentRunStart(runId);
        output({ event: 'pending', id: ctx.SenderId });
        await new Promise(resolve => held.set(ctx.SenderId, resolve));
        eventHandler({ runId, stream: 'assistant', data: { delta: `reply-${ctx.SenderId}` } });
        eventHandler({ runId, stream: 'lifecycle', data: { phase: 'end' } });
      } },
  } },
};
const auth = { async bearer() { return 'integration-secret'; }, subject() { return channelId; },
  connected() { output({ event: 'ready' }); }, disconnected() {}, status() { return { state: 'connected' }; } };
let bridge;
function start() { bridge = new VauxrBridge(api, { url }, auth); bridge.start(); }
start();
for await (const line of createInterface({ input: process.stdin })) {
  const cmd = JSON.parse(line);
  if (cmd.complete) { const complete = held.get(cmd.complete); held.delete(cmd.complete); complete(); }
  if (cmd.restart) { bridge.stop(); start(); }
  if (cmd.stop) { bridge.stop(); break; }
}
