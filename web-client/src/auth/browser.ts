import { clientPost, ownerPost, randomId } from './api';

export interface BrowserIdentity {
  privateKey: CryptoKey; publicKey: string; deviceId: string; origin: string;
  serverId?: string; token?: string; operationId?: string; enrolled?: boolean;
}
const hex = (value: ArrayBuffer) => Array.from(new Uint8Array(value), b => b.toString(16).padStart(2, '0')).join('');
async function database() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open('vauxr-browser-v1', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('identity');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Browser credential storage unavailable.'));
  });
}
export async function readIdentity(): Promise<BrowserIdentity | undefined> {
  const db = await database();
  try { return await new Promise((resolve, reject) => {
    const request = db.transaction('identity').objectStore('identity').get('current');
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(new Error('Cannot read browser identity.'));
  }); } finally { db.close(); }
}
export async function saveIdentity(identity: BrowserIdentity) {
  const db = await database();
  try { await new Promise<void>((resolve, reject) => {
    const tx = db.transaction('identity', 'readwrite', { durability: 'strict' });
    tx.objectStore('identity').put(identity, 'current');
    tx.oncomplete = () => resolve();
    tx.onerror = tx.onabort = () => reject(new Error('Cannot durably save browser identity. Recovery may be required.'));
  }); } finally { db.close(); }
  const saved = await readIdentity();
  if (saved?.token !== identity.token || saved?.operationId !== identity.operationId || saved?.deviceId !== identity.deviceId) throw new Error('Browser credential readback failed.');
}
export function transcript(action: string, c: Record<string, unknown>) {
  return JSON.stringify(['vauxr-enrollment', action, ...['version','request_id','server_id','origin','kind','public_key','device_id','nonce','expires_at','owner_generation','display_name'].map(k => c[k])]);
}
export async function browserIdentity(recover = false): Promise<BrowserIdentity> {
  let identity = await readIdentity();
  if (!identity) {
    const pair = await crypto.subtle.generateKey('Ed25519', false, ['sign', 'verify']) as CryptoKeyPair;
    const raw = await crypto.subtle.exportKey('raw', pair.publicKey);
    identity = { privateKey: pair.privateKey, publicKey: hex(raw), deviceId: `dev_${hex(await crypto.subtle.digest('SHA-256', raw))}`, origin: location.origin };
    await saveIdentity(identity);
  }
  if (identity.origin !== location.origin) throw new Error('Browser identity belongs to another origin. Explicit setup required.');
  if (identity.token && !recover) {
    await maintainIdentity(identity); // Validate and finish a saved ACK before using voice.
    return identity;
  }
  if (identity.enrolled && !recover) throw new Error('Browser access retired or enrollment outcome uncertain. Choose Recover browser identity.');
  if (recover) await ownerPost('/api/lifecycle/v1/recover', {operation_id: randomId(), role: 'device', subject: identity.deviceId});
  const name = 'Browser voice';
  const c = await ownerPost('/api/enrollment/v1/request', {kind: 'browser', public_key: identity.publicKey, display_name: name});
  if (c.version !== 1 || c.origin !== identity.origin || c.kind !== 'browser' || c.public_key !== identity.publicKey || c.device_id !== identity.deviceId || c.display_name !== name || !/^[a-f0-9]{32}$/.test(c.server_id) || !/^[a-f0-9]{32}$/.test(c.request_id) || !/^[a-f0-9]{64}$/.test(c.nonce) || !/^[a-f0-9]{32}$/.test(c.owner_generation) || !Number.isInteger(c.expires_at) || c.expires_at <= Date.now()/1000 || (identity.serverId && identity.serverId !== c.server_id)) throw new Error('Enrollment binding mismatch. No proof sent.');
  identity.serverId = c.server_id;
  await saveIdentity(identity);
  const sign = async (action: string) => hex(await crypto.subtle.sign('Ed25519', identity.privateKey, new TextEncoder().encode(transcript(action, c))));
  const proof = await ownerPost('/api/enrollment/v1/prove', {request_id: c.request_id, signature: await sign('prove')});
  await ownerPost('/api/enrollment/v1/initiate', {request_id: c.request_id, code: proof.code});
  await ownerPost('/api/enrollment/v1/approve', {request_id: c.request_id, code: proof.code});
  // Retain uncertainty before the one-shot redemption. Never silently create another key.
  identity.enrolled = true;
  await saveIdentity(identity);
  const result = await ownerPost('/api/enrollment/v1/redeem', {request_id: c.request_id, signature: await sign('redeem')});
  if (result.device_id !== identity.deviceId || !/^vx_dev_[A-Za-z0-9_-]{43}$/.test(result.device_token)) throw new Error('Invalid browser credential response. Recover browser identity.');
  identity.token = result.device_token;
  identity.operationId = result.operation_id;
  await saveIdentity(identity);
  if (identity.operationId) await acknowledge(identity);
  return identity;
}
async function acknowledge(identity: BrowserIdentity) {
  await clientPost('ack', identity.token!, {operation_id: identity.operationId, saved: true});
  identity.operationId = undefined;
  await saveIdentity(identity);
}
export async function maintainIdentity(identity: BrowserIdentity) {
  if (identity.operationId) await acknowledge(identity);
  const operation = await clientPost('poll', identity.token!);
  if (operation.state === 'pending' || operation.state === 'queued') {
    const result = await clientPost('deliver', identity.token!, {operation_id: operation.operation_id});
    const replacement = {...identity, token: result.credential, operationId: operation.operation_id};
    await saveIdentity(replacement); // Atomic replacement, strict commit, readback, then ACK.
    Object.assign(identity, replacement);
    await acknowledge(identity);
  } else if (operation.state === 'delivered' || operation.state === 'expired') {
    throw new Error('Credential delivery was not acknowledged. Recover browser identity.');
  }
}
export async function retireBrowser() {
  // The active voice tab releases this lock after all in-flight enrollment/storage work.
  const retire = async () => {
    const identity = await readIdentity();
    if (!identity?.enrolled) return;
    const key = 'vauxr-browser-revoke';
    const id = sessionStorage.getItem(key) || randomId();
    sessionStorage.setItem(key, id); // Public operation metadata only, retained before POST.
    await ownerPost('/api/lifecycle/v1/revoke', {operation_id: id, role: 'device', subject: identity.deviceId});
    identity.token = undefined; identity.operationId = undefined;
    await saveIdentity(identity);
    sessionStorage.removeItem(key);
  };
  const channel = new BroadcastChannel('vauxr-voice'); channel.postMessage('stop'); channel.close();
  if (navigator.locks) await navigator.locks.request('vauxr-browser-voice', retire);
  else await retire();
}
