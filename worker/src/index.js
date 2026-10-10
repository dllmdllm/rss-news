/**
 * News Pulse — Web Push Worker
 * Endpoints:
 *   GET  /vapid-public-key  → returns VAPID public key (for frontend)
 *   POST /subscribe         → save push subscription to KV
 *   POST /unsubscribe       → remove subscription
 *   POST /notify            → send push to all subscribers (protected by NOTIFY_SECRET)
 *
 * Required Worker secrets (wrangler secret put):
 *   VAPID_PUBLIC_KEY   uncompressed P-256 public key, base64url (65 bytes)
 *   VAPID_PRIVATE_KEY  raw P-256 private key, base64url (32 bytes)
 *   VAPID_SUBJECT      mailto: or https: URI identifying sender
 *   NOTIFY_SECRET      shared secret used by GitHub Actions to call /notify
 *
 * Required KV binding: SUBSCRIPTIONS
 */

/* ── Base64url helpers ─────────────────────────────────────────────── */

function u8ToB64(arr) {
  return btoa(String.fromCharCode(...arr))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

function b64ToU8(str) {
  const pad = "=".repeat((4 - (str.length % 4)) % 4);
  const b64 = (str + pad).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(b64), c => c.charCodeAt(0));
}

function strToB64(s) {
  return u8ToB64(new TextEncoder().encode(s));
}

function cat(...arrs) {
  const out = new Uint8Array(arrs.reduce((n, a) => n + a.length, 0));
  let off = 0;
  for (const a of arrs) { out.set(a, off); off += a.length; }
  return out;
}

/* ── VAPID JWT (ES256) ─────────────────────────────────────────────── */

async function vapidHeader(endpoint, privB64, pubB64, subject) {
  const aud = new URL(endpoint).origin;
  const exp = Math.floor(Date.now() / 1000) + 43200;

  const hdr = strToB64(JSON.stringify({ typ: "JWT", alg: "ES256" }));
  const pay = strToB64(JSON.stringify({ aud, exp, sub: subject }));
  const msg = `${hdr}.${pay}`;

  const pub  = b64ToU8(pubB64);
  const jwk  = {
    kty: "EC", crv: "P-256", d: privB64,
    x: u8ToB64(pub.slice(1, 33)),
    y: u8ToB64(pub.slice(33, 65)),
  };
  const key = await crypto.subtle.importKey(
    "jwk", jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]
  );
  const sig = new Uint8Array(
    await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, new TextEncoder().encode(msg))
  );
  return `vapid t=${msg}.${u8ToB64(sig)},k=${pubB64}`;
}

/* ── RFC 8291 payload encryption (aes128gcm) ───────────────────────── */

async function hmacSign(key, data) {
  const k = await crypto.subtle.importKey("raw", key, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", k, data));
}

async function hkdfExpand1(prk, info, len) {
  // Single-block HKDF-Expand: T(1) = HMAC(PRK, info || 0x01)
  return (await hmacSign(prk, cat(info, new Uint8Array([1])))).slice(0, len);
}

async function encryptPayload(subKeys, plaintext) {
  const enc   = new TextEncoder();
  const auth  = b64ToU8(subKeys.auth);
  const ua    = b64ToU8(subKeys.p256dh);
  const salt  = crypto.getRandomValues(new Uint8Array(16));

  // Generate server ECDH key pair
  const asKP = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
  const as   = new Uint8Array(await crypto.subtle.exportKey("raw", asKP.publicKey));

  // ECDH
  const uaKey    = await crypto.subtle.importKey("raw", ua, { name: "ECDH", namedCurve: "P-256" }, false, []);
  const ecdhBits = await crypto.subtle.deriveBits({ name: "ECDH", public: uaKey }, asKP.privateKey, 256);

  // PRK_key = HKDF-Extract(auth, ECDH)
  const prk1 = await hmacSign(auth, ecdhBits);
  // IKM' = HKDF-Expand(PRK_key, "WebPush: info\0" || ua_pub || as_pub, 32)
  const ikm  = await hkdfExpand1(prk1, cat(enc.encode("WebPush: info\0"), ua, as), 32);
  // PRK = HKDF-Extract(salt, IKM')
  const prk2 = await hmacSign(salt, ikm);
  // CEK + Nonce
  const cek  = await hkdfExpand1(prk2, enc.encode("Content-Encoding: aes128gcm\0"), 16);
  const iv   = await hkdfExpand1(prk2, enc.encode("Content-Encoding: nonce\0"), 12);

  // AES-128-GCM encrypt (content || 0x02 delimiter)
  const cekKey = await crypto.subtle.importKey("raw", cek, "AES-GCM", false, ["encrypt"]);
  const ct     = new Uint8Array(await crypto.subtle.encrypt(
    { name: "AES-GCM", iv, tagLength: 128 }, cekKey,
    cat(enc.encode(plaintext), new Uint8Array([2]))
  ));

  // aes128gcm header: salt(16) | rs(4, BE) | idlen(1) | as_pub(65)
  const rs = new Uint8Array(4);
  new DataView(rs.buffer).setUint32(0, 4096, false);
  return cat(salt, rs, new Uint8Array([as.length]), as, ct);
}

/* ── Subscription KV key ───────────────────────────────────────────── */

async function kvKey(endpoint) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
  return u8ToB64(new Uint8Array(buf)).slice(0, 32);
}

// Only browser push services may receive outbound authenticated requests.
// Exact/provider suffix checks deliberately exclude arbitrary HTTPS endpoints.
function validEndpoint(endpoint) {
  try {
    if (typeof endpoint !== "string" || endpoint.length > 2048) return false;
    const u = new URL(endpoint);
    return u.protocol === "https:" && !u.username && !u.password &&
      (!u.port || u.port === "443") && !u.hash && (
        u.hostname === "fcm.googleapis.com" ||
        u.hostname === "updates.push.services.mozilla.com" ||
        u.hostname === "web.push.apple.com" ||
        u.hostname.endsWith(".notify.windows.com")
      ) && u.pathname.length > 1;
  } catch { return false; }
}

function validSubscription(sub) {
  if (!validEndpoint(sub?.endpoint)) return false;
  try {
    const p = sub?.keys?.p256dh, a = sub?.keys?.auth;
    if (typeof p !== "string" || typeof a !== "string" ||
        !/^[A-Za-z0-9_-]{87}$/.test(p) || !/^[A-Za-z0-9_-]{22}$/.test(a)) return false;
    return b64ToU8(p).length === 65 && b64ToU8(p)[0] === 4 && b64ToU8(a).length === 16;
  } catch { return false; }
}

const MAX_SUBSCRIPTIONS = 1000;
const MAX_REQUEST_BYTES = 8192;
async function requestTooLarge(request) {
  if (Number(request.headers.get("Content-Length") || 0) > MAX_REQUEST_BYTES) return true;
  const reader = request.clone().body?.getReader();
  if (!reader) return false;
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) return false;
      bytes += value.byteLength;
      if (bytes > MAX_REQUEST_BYTES) {
        // Do not await cancellation of a tee while its other branch is unread.
        reader.cancel().catch(() => {});
        return true;
      }
    }
  } finally { reader.releaseLock(); }
}

/* ── Send one push ─────────────────────────────────────────────────── */

async function sendOne(sub, payload, env) {
  if (!validSubscription(sub)) throw new Error("Invalid stored subscription");
  const auth = await vapidHeader(sub.endpoint, env.VAPID_PRIVATE_KEY, env.VAPID_PUBLIC_KEY, env.VAPID_SUBJECT);
  const body = await encryptPayload(sub.keys, payload);

  const res = await fetch(sub.endpoint, {
    method:  "POST",
    redirect: "error",
    headers: {
      Authorization:     auth,
      "Content-Type":     "application/octet-stream",
      "Content-Encoding": "aes128gcm",
      TTL:               "86400",
    },
    body,
  });

  // 410 Gone / 404 = expired subscription, clean up
  if (res.status === 410 || res.status === 404) {
    await env.SUBSCRIPTIONS.delete(await kvKey(sub.endpoint));
  } else if (!res.ok && res.status !== 201) {
    throw new Error(`Push ${res.status}`);
  }
}

/* ── CORS headers ──────────────────────────────────────────────────── */

const CORS = {
  "Access-Control-Allow-Origin":  "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

/* ── Main ──────────────────────────────────────────────────────────── */

export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    const { pathname } = new URL(request.url);
    if (request.method === "POST") {
      if (await requestTooLarge(request))
        return new Response("Request too large", { status: 413, headers: CORS });
    }

    // ── GET /vapid-public-key
    if (pathname === "/vapid-public-key" && request.method === "GET") {
      return new Response(env.VAPID_PUBLIC_KEY ?? "", {
        headers: { "Content-Type": "text/plain", ...CORS },
      });
    }

    // ── POST /subscribe
    if (pathname === "/subscribe" && request.method === "POST") {
      try {
        const sub = await request.json();
        if (!validSubscription(sub))
          return new Response("Invalid subscription", { status: 400, headers: CORS });
        const key = await kvKey(sub.endpoint);
        // KV is eventually consistent: this is bounded admission, not an atomic
        // quota. A strict distributed quota requires deployment infrastructure.
        if (!await env.SUBSCRIPTIONS.get(key)) {
          const page = await env.SUBSCRIPTIONS.list({ limit: MAX_SUBSCRIPTIONS });
          if (page.keys.length >= MAX_SUBSCRIPTIONS || !page.list_complete)
            return new Response("Subscription capacity reached", { status: 429, headers: CORS });
        }
        await env.SUBSCRIPTIONS.put(key, JSON.stringify({ endpoint: sub.endpoint, keys: sub.keys }),
          { expirationTtl: 90 * 86400 });
        return new Response("ok", { headers: CORS });
      } catch (e) {
        return new Response(String(e), { status: 500, headers: CORS });
      }
    }

    // ── POST /unsubscribe
    if (pathname === "/unsubscribe" && request.method === "POST") {
      try {
        const { endpoint } = await request.json();
        if (!validEndpoint(endpoint)) return new Response("Invalid endpoint", { status: 400, headers: CORS });
        await env.SUBSCRIPTIONS.delete(await kvKey(endpoint));
        return new Response("ok", { headers: CORS });
      } catch (e) {
        return new Response(String(e), { status: 500, headers: CORS });
      }
    }

    // ── POST /notify  (protected)
    if (pathname === "/notify" && request.method === "POST") {
      if (!env.NOTIFY_SECRET || request.headers.get("Authorization") !== `Bearer ${env.NOTIFY_SECRET}`)
        return new Response("Unauthorized", { status: 401 });

      try {
        const { title = "News Pulse", body = "", url = "" } = await request.json();
        const payload = JSON.stringify({ title, body, url });

        let cursor;
        let sent = 0, failed = 0;
        do {
          const page = await env.SUBSCRIPTIONS.list({ cursor, limit: 100 });
          cursor = page.cursor;
          const results = await Promise.allSettled(
            page.keys.slice(0, MAX_SUBSCRIPTIONS - sent - failed).map(async ({ name }) => {
              const raw = await env.SUBSCRIPTIONS.get(name);
              if (!raw) return;
              await sendOne(JSON.parse(raw), payload, env);
            })
          );
          sent   += results.filter(r => r.status === "fulfilled").length;
          failed += results.filter(r => r.status === "rejected").length;
        } while (cursor && sent + failed < MAX_SUBSCRIPTIONS);

        return new Response(JSON.stringify({ sent, failed }), {
          headers: { "Content-Type": "application/json", ...CORS },
        });
      } catch (e) {
        return new Response(String(e), { status: 500, headers: CORS });
      }
    }

    return new Response("Not found", { status: 404 });
  },
};
