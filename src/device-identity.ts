import * as crypto from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";

interface DeviceIdentity {
  publicKeyRaw: string;  // base64url-encoded raw 32-byte Ed25519 public key
  privateKeyDer: string; // base64-encoded PKCS8 DER private key (for storage only)
  fingerprint: string;   // hex SHA-256 of the raw 32-byte public key
}

interface StoredData {
  identity: DeviceIdentity;
  deviceToken?: string;
}

// SPKI prefix for Ed25519 keys (12 bytes before the raw 32-byte key)
const SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

const DATA_FILE = "vauxr-identity.json";

let cachedData: StoredData | null = null;

function dataFilePath(dataDir: string): string {
  return path.join(dataDir, DATA_FILE);
}

function toBase64Url(buf: Buffer): string {
  return buf.toString("base64url");
}

function generateIdentity(): DeviceIdentity {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");

  const pubDer = publicKey.export({ type: "spki", format: "der" });
  const privDer = privateKey.export({ type: "pkcs8", format: "der" });

  // Extract raw 32-byte key by stripping the SPKI prefix
  const rawPubKey = pubDer.subarray(SPKI_PREFIX.length);
  const fingerprint = crypto.createHash("sha256").update(rawPubKey).digest("hex");

  return {
    publicKeyRaw: toBase64Url(rawPubKey),
    privateKeyDer: privDer.toString("base64"),
    fingerprint,
  };
}

export function loadOrCreateIdentity(dataDir: string): StoredData {
  if (cachedData) return cachedData;

  const filePath = dataFilePath(dataDir);

  if (fs.existsSync(filePath)) {
    const raw = fs.readFileSync(filePath, "utf-8");
    cachedData = JSON.parse(raw) as StoredData;
    console.log(`[identity] Loaded identity: ${cachedData.identity.fingerprint}`);
    return cachedData;
  }

  const identity = generateIdentity();
  cachedData = { identity };
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(filePath, JSON.stringify(cachedData, null, 2), "utf-8");
  console.log(`[identity] Generated new identity: ${identity.fingerprint}`);
  return cachedData;
}

export function saveDeviceToken(dataDir: string, deviceToken: string): void {
  const data = loadOrCreateIdentity(dataDir);
  data.deviceToken = deviceToken;
  cachedData = data;
  fs.writeFileSync(dataFilePath(dataDir), JSON.stringify(data, null, 2), "utf-8");
  console.log("[identity] Device token saved");
}

export function getDeviceToken(dataDir: string): string | undefined {
  return loadOrCreateIdentity(dataDir).deviceToken;
}

export interface SignParams {
  nonce: string;
  token: string;       // auth.token or auth.deviceToken
  clientId: string;
  clientMode: string;
  role: string;
  scopes: string[];
  platform: string;
  deviceFamily: string;
}

export function signConnectPayload(
  dataDir: string,
  params: SignParams,
): { signature: string; signedAt: number } {
  const data = loadOrCreateIdentity(dataDir);
  const privKeyDer = Buffer.from(data.identity.privateKeyDer, "base64");
  const privateKey = crypto.createPrivateKey({ key: privKeyDer, format: "der", type: "pkcs8" });

  const signedAt = Date.now();

  // v3 payload: v3|deviceId|clientId|clientMode|role|scopes|signedAt|token|nonce|platform|deviceFamily
  const payloadStr = [
    "v3",
    data.identity.fingerprint,
    params.clientId,
    params.clientMode,
    params.role,
    params.scopes.join(","),
    String(signedAt),
    params.token,
    params.nonce,
    params.platform.toLowerCase().trim(),
    params.deviceFamily.toLowerCase().trim(),
  ].join("|");

  const signature = crypto.sign(null, Buffer.from(payloadStr, "utf-8"), privateKey);

  return { signature: toBase64Url(signature), signedAt };
}
