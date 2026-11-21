/** Token hashing and AES-GCM wrapping for webhook secrets and GitHub tokens. */

const te = new TextEncoder();
const td = new TextDecoder();

export async function importMasterKey(hex: string): Promise<CryptoKey> {
  const clean = (hex || "").trim();
  if (!/^[0-9a-fA-F]{64}$/.test(clean)) {
    throw new Error("FORGEPULSE_MASTER_KEY must be 32 bytes as 64 hex characters");
  }
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  return crypto.subtle.importKey("raw", bytes, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

export async function hashToken(masterHex: string, token: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    te.encode(masterHex.trim()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, te.encode(token));
  return bufferToHex(sig);
}

export async function tokensEqual(masterHex: string, token: string, expectedHash: string): Promise<boolean> {
  const actual = await hashToken(masterHex, token);
  if (actual.length !== expectedHash.length) return false;
  const a = te.encode(actual);
  const b = te.encode(expectedHash);
  return crypto.subtle.timingSafeEqual ? crypto.subtle.timingSafeEqual(a, b) : actual === expectedHash;
}

export async function encryptString(masterHex: string, plaintext: string): Promise<string> {
  const key = await importMasterKey(masterHex);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, te.encode(plaintext));
  const packed = new Uint8Array(iv.length + cipher.byteLength);
  packed.set(iv, 0);
  packed.set(new Uint8Array(cipher), iv.length);
  return bufferToBase64(packed);
}

export async function decryptString(masterHex: string, blob: string): Promise<string> {
  const key = await importMasterKey(masterHex);
  const packed = base64ToBuffer(blob);
  const iv = packed.slice(0, 12);
  const data = packed.slice(12);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, data);
  return td.decode(plain);
}

export function randomToken(prefix: string): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return prefix + bufferToHex(bytes.buffer);
}

export function randomId(prefix: string): string {
  return prefix + crypto.randomUUID().replace(/-/g, "").slice(0, 20);
}

function bufferToHex(buffer: ArrayBuffer): string {
  return [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function bufferToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function base64ToBuffer(value: string): Uint8Array {
  const binary = atob(value);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export async function sha256Hex(data: ArrayBuffer | Uint8Array): Promise<string> {
  const buf = data instanceof Uint8Array ? data : new Uint8Array(data);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return bufferToHex(digest);
}

export async function hmacSha256Hex(secret: string, body: Uint8Array): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    te.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, body);
  return bufferToHex(sig);
}

export function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}
