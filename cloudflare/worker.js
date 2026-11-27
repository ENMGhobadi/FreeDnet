import { DurableObject } from "cloudflare:workers";
import { connect } from "cloudflare:sockets";

const MAX_READ_BUFFER_BYTES = 2 * 1024 * 1024; // 2 MB
const MAX_SYNC_CHUNK_BYTES = 1024 * 1024; // 1 MB
const SESSION_IDLE_MS = 300000; // 5 minutes

function jsonResponse(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunkSize = 0x8000;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

function base64ToBytes(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

async function gzipBytes(bytes) {
  const cs = new CompressionStream("gzip");
  const writer = cs.writable.getWriter();
  writer.write(bytes);
  writer.close();
  const buf = await new Response(cs.readable).arrayBuffer();
  return new Uint8Array(buf);
}

async function gunzipBytes(bytes) {
  const ds = new DecompressionStream("gzip");
  const writer = ds.writable.getWriter();
  writer.write(bytes);
  writer.close();
  const buf = await new Response(ds.readable).arrayBuffer();
  return new Uint8Array(buf);
}


const HOP_BY_HOP_REQ = new Set([
  "host", "connection", "keep-alive", "proxy-connection", "proxy-authenticate",
  "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade",
  "content-length", "accept-encoding"
]);
const HOP_BY_HOP_RESP = new Set([
  "connection", "keep-alive", "proxy-connection", "transfer-encoding",
  "content-encoding", "content-length", "upgrade"
]);

async function handleHttpFetch(body) {
  const targetUrl = String(body.url || "").trim();
  const method = String(body.method || "GET").toUpperCase();
  if (!/^https?:\/\//i.test(targetUrl)) {
    return jsonResponse({ ok: false, error: "Invalid URL" }, 400);
  }

  const reqHeaders = new Headers();
  if (body.headers && typeof body.headers === "object") {
    for (const [k, v] of Object.entries(body.headers)) {
      if (!HOP_BY_HOP_REQ.has(k.toLowerCase()) && v !== undefined && v !== null) {
        try { reqHeaders.set(k, String(v)); } catch (_) {}
      }
    }
  }

  const fetchOpts = { method, headers: reqHeaders, redirect: "manual" };
  if (body.body && method !== "GET" && method !== "HEAD") {
    fetchOpts.body = base64ToBytes(body.body);
  }

  try {
    const resp = await fetch(targetUrl, fetchOpts);
    const bytes = new Uint8Array(await resp.arrayBuffer());

    const outHeaders = {};
    resp.headers.forEach((val, key) => {
      if (!HOP_BY_HOP_RESP.has(key.toLowerCase())) {
        outHeaders[key] = val;
      }
    });

    if (body.acceptGzip && bytes.length > 512 && bytes.length <= 2 * 1024 * 1024) {
      try {
        const gz = await gzipBytes(bytes);
        if (gz.length < bytes.length) {
          return jsonResponse({
            ok: true,
            status: resp.status,
            statusText: resp.statusText || "",
            headers: outHeaders,
            bodyZ: bytesToBase64(gz),
          });
        }
      } catch (_) {}
    }

    return jsonResponse({
      ok: true,
      status: resp.status,
      statusText: resp.statusText || "",
      headers: outHeaders,
      body: bytes.length ? bytesToBase64(bytes) : "",
    });
  } catch (err) {
    return jsonResponse(
      { ok: false, targetError: true, error: err && err.message ? err.message : String(err) },
      502
    );
  }
}

export class TunnelSessionDO extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.socket = null;
    this.writer = null;
    this.chunks = [];
    this.bufferedBytes = 0;
    this.closed = false;
    this.error = null;
    this.nextTxSeq = 1;
    this.pendingTx = new Map();
    this.rxSeq = 0;
    this.unacked = null;
    this.waiters = [];
  }

  notifyWaiters() {
    while (this.waiters.length > 0) {
      const fn = this.waiters.shift();
      try {
        fn();
      } catch (_) {}
    }
  }

  drainData(maxBytes = MAX_SYNC_CHUNK_BYTES) {
    if (this.chunks.length === 0) return new Uint8Array(0);
    const out = [];
    let collected = 0;
    while (this.chunks.length > 0 && collected < maxBytes) {
      const head = this.chunks[0];
      const remaining = maxBytes - collected;
      if (head.length <= remaining) {
        out.push(head);
        collected += head.length;
        this.chunks.shift();
      } else {
        out.push(head.subarray(0, remaining));
        this.chunks[0] = head.subarray(remaining);
        collected += remaining;
      }
    }
    this.bufferedBytes -= collected;
    const merged = new Uint8Array(collected);
    let offset = 0;
    for (const part of out) {
      merged.set(part, offset);
      offset += part.length;
    }
    return merged;
  }

  async startReadLoop(reader) {
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (value && value.length > 0) {
          this.chunks.push(value);
          this.bufferedBytes += value.length;
          this.notifyWaiters();
          while (this.bufferedBytes >= MAX_READ_BUFFER_BYTES && !this.closed) {
            await new Promise((r) => setTimeout(r, 10));
          }
        }
      }
    } catch (err) {
      this.error = err && err.message ? err.message : String(err);
    } finally {
      this.closed = true;
      this.notifyWaiters();
    }
  }

  async alarm() {
    this.closed = true;
    this.notifyWaiters();
    try {
      if (this.socket) await this.socket.close();
    } catch (_) {}
  }

  async fetch(request) {
    const body = await request.json();
    const action = body.action;

    try {
      await this.ctx.storage.setAlarm(Date.now() + SESSION_IDLE_MS);
    } catch (_) {}

    if (action === "open") {
      if (this.socket) {
        return jsonResponse({
          ok: true,
          sessionId: body.sessionId,
          rxSeq: this.rxSeq,
          data: "",
          closed: this.closed && this.chunks.length === 0 && !this.unacked,
        });
      }
      const targetHost = String(body.targetHost || "").trim();
      const targetPort = parseInt(body.targetPort, 10);
      if (!targetHost || !Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) {
        return jsonResponse({ ok: false, error: "Invalid targetHost or targetPort" }, 400);
      }

      try {
        this.socket = connect({ hostname: targetHost, port: targetPort });
        await this.socket.opened;
        this.writer = this.socket.writable.getWriter();
        const reader = this.socket.readable.getReader();
        this.startReadLoop(reader);

        if (body.data) {
          const initBytes = base64ToBytes(body.data);
          if (initBytes.length > 0) {
            await this.writer.write(initBytes);
          }
        }

        await new Promise((r) => setTimeout(r, 60));
        const out = this.drainData();
        if (out.length > 0) {
          this.rxSeq += 1;
          this.unacked = { seq: this.rxSeq, buf: out };
        }
        return jsonResponse({
          ok: true,
          sessionId: body.sessionId,
          rxSeq: out.length > 0 ? this.rxSeq : 0,
          data: out.length ? bytesToBase64(out) : "",
          closed: this.closed && this.chunks.length === 0 && !this.unacked,
        });
      } catch (err) {
        this.closed = true;
        return jsonResponse(
          { ok: false, targetError: true, error: err && err.message ? err.message : String(err) },
          502
        );
      }
    }

    if (action === "sync") {
      if (!this.socket) {
        return jsonResponse({ ok: false, sessionError: true, error: "Session not found", closed: true }, 404);
      }

      if ((body.dataZ || body.data) && !this.closed && this.writer) {
        const txSeq = Number.isInteger(body.txSeq) ? body.txSeq : 0;
        if (txSeq > 0 && (txSeq < this.nextTxSeq || this.pendingTx.has(txSeq))) {
          // Duplicate retried chunk — skip write to protect TLS/TCP stream integrity
        } else {
          try {
            const writeBytes = body.dataZ
              ? await gunzipBytes(base64ToBytes(body.dataZ))
              : base64ToBytes(body.data);
            if (txSeq > 0) {
              if (this.pendingTx.size < 32) {
                this.pendingTx.set(txSeq, writeBytes);
              }
              while (this.pendingTx.has(this.nextTxSeq)) {
                const ordered = this.pendingTx.get(this.nextTxSeq);
                this.pendingTx.delete(this.nextTxSeq);
                if (ordered.length > 0) {
                  await this.writer.write(ordered);
                }
                this.nextTxSeq += 1;
              }
            } else if (writeBytes.length > 0) {
              await this.writer.write(writeBytes);
            }
          } catch (_) {
            return jsonResponse({ ok: false, error: "Invalid payload data" }, 400);
          }
        }
      }

      if (body.txFin && !this.closed && this.writer) {
        try {
          await this.writer.close();
        } catch (_) {}
      }

      const hasAck = Number.isInteger(body.ackSeq);
      if (!body.txOnly && this.unacked) {
        if (!hasAck || body.ackSeq >= this.unacked.seq) {
          this.unacked = null;
        }
      }

      const waitMs = Math.max(0, Math.min(parseInt(body.waitMs || "0", 10) || 0, 10000));
      const acceptGzip = Boolean(body.acceptGzip);

      if (body.txOnly) {
        const txClosed = this.closed && this.chunks.length === 0 && !this.unacked;
        return jsonResponse({ ok: true, data: "", closed: txClosed });
      }

      if (!this.unacked && this.chunks.length === 0 && !this.closed && waitMs > 0) {
        await new Promise((resolve) => {
          let done = false;
          const finishWait = () => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            if (this.chunks.length > 0 && !this.closed && this.bufferedBytes < 65536) {
              setTimeout(resolve, 8);
            } else {
              resolve();
            }
          };
          const timer = setTimeout(finishWait, waitMs);
          this.waiters.push(finishWait);
        });
      }

      let out;
      let outSeq = this.rxSeq;
      if (this.unacked && hasAck && body.ackSeq < this.unacked.seq) {
        out = this.unacked.buf;
        outSeq = this.unacked.seq;
      } else {
        out = this.drainData();
        if (out.length > 0) {
          this.rxSeq += 1;
          outSeq = this.rxSeq;
          if (hasAck) {
            this.unacked = { seq: outSeq, buf: out };
          }
        }
      }
      const isClosed = this.closed && this.chunks.length === 0 && !this.unacked;
      const looksLikeTls = out.length >= 2 && out[0] >= 0x14 && out[0] <= 0x17 && out[1] === 0x03;

      if (acceptGzip && !looksLikeTls && out.length > 512 && out.length <= 1048576) {
        try {
          const gz = await gzipBytes(out);
          if (gz.length < out.length) {
            return jsonResponse({ ok: true, rxSeq: outSeq, dataZ: bytesToBase64(gz), closed: isClosed });
          }
        } catch (_) {}
      }

      return jsonResponse({
        ok: true,
        rxSeq: outSeq,
        data: out.length ? bytesToBase64(out) : "",
        closed: isClosed,
      });
    }

    if (action === "close") {
      this.closed = true;
      this.notifyWaiters();
      try {
        if (this.socket) await this.socket.close();
      } catch (_) {}
      return jsonResponse({ ok: true });
    }

    return jsonResponse({ ok: false, error: "Unknown action" }, 400);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);


    if (request.method === "GET" && url.pathname === "/ws") {
      if ((request.headers.get("upgrade") || "").toLowerCase() !== "websocket") {
        return new Response("Expected Upgrade: websocket", { status: 426 });
      }
      const expectedKey = env.AUTH_KEY || "change-me-freednet-key";
      const providedKey = request.headers.get("x-auth-key") || url.searchParams.get("authKey") || "";
      if (String(providedKey) !== String(expectedKey)) {
        return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
      }
      const targetHost = (url.searchParams.get("host") || "").trim();
      const targetPort = parseInt(url.searchParams.get("port") || "0", 10);
      if (!targetHost || !Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) {
        return jsonResponse({ ok: false, error: "Invalid host or port" }, 400);
      }

      const pair = new WebSocketPair();
      const [clientWs, serverWs] = Object.values(pair);
      serverWs.accept();

      (async () => {
        const pending = [];
        let writer = null;
        let sock = null;
        let wsClosed = false;

        serverWs.addEventListener("message", async (evt) => {
          const bytes = evt.data instanceof ArrayBuffer
            ? new Uint8Array(evt.data)
            : new TextEncoder().encode(String(evt.data || ""));
          if (!bytes.length) return;
          if (!writer) {
            if (pending.length < 64) pending.push(bytes);
            return;
          }
          try {
            await writer.write(bytes);
          } catch (_) {}
        });

        serverWs.addEventListener("close", () => {
          wsClosed = true;
          try { if (sock) sock.close(); } catch (_) {}
        });

        try {
          sock = connect({ hostname: targetHost, port: targetPort });
          await sock.opened;
          writer = sock.writable.getWriter();
          while (pending.length > 0 && !wsClosed) {
            await writer.write(pending.shift());
          }
          const reader = sock.readable.getReader();
          while (!wsClosed) {
            const { value, done } = await reader.read();
            if (done) break;
            if (value && value.length > 0) {
              serverWs.send(value);
            }
          }
          try { serverWs.close(1000, "EOF"); } catch (_) {}
        } catch (err) {
          try { serverWs.close(1011, (err && err.message ? err.message : "Connect error").slice(0, 100)); } catch (_) {}
        } finally {
          try { if (sock) await sock.close(); } catch (_) {}
        }
      })();

      return new Response(null, { status: 101, webSocket: clientWs });
    }

    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      return jsonResponse({ ok: true, service: "FreeDnet-CF", ts: Date.now() });
    }

    if (request.method === "POST" && url.pathname === "/tunnel") {
      let body;
      try {
        body = await request.json();
      } catch (_) {
        return jsonResponse({ ok: false, error: "Invalid JSON" }, 400);
      }

      const expectedKey = env.AUTH_KEY || "change-me-freednet-key";
      const providedKey = request.headers.get("x-auth-key") || body.authKey || "";
      if (String(providedKey) !== String(expectedKey)) {
        return jsonResponse({ ok: false, error: "Unauthorized" }, 401);
      }

      const action = body.action;
      if (action === "ping") {
        return jsonResponse({ ok: true, service: "FreeDnet-CF", activeSessions: 0, ts: Date.now() });
      }
      if (action === "http_fetch") {
        return handleHttpFetch(body);
      }

      if (action === "open") {
        const sessionId = body.clientOpenId ? String(body.clientOpenId) : crypto.randomUUID();
        const id = env.TUNNEL_DO.idFromName(sessionId);
        const hint = (env.LOCATION_HINT || "").trim();
        const stub = hint ? env.TUNNEL_DO.get(id, { locationHint: hint }) : env.TUNNEL_DO.get(id);
        return stub.fetch(
          new Request("https://do.internal/tunnel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ...body, sessionId }),
          })
        );
      }

      if (action === "sync" || action === "close") {
        const sessionId = String(body.sessionId || "");
        if (!sessionId) {
          return jsonResponse({ ok: false, error: "Missing sessionId", closed: true }, 400);
        }
        const id = env.TUNNEL_DO.idFromName(sessionId);
        const stub = env.TUNNEL_DO.get(id);
        return stub.fetch(
          new Request("https://do.internal/tunnel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          })
        );
      }

      return jsonResponse({ ok: false, error: "Unknown action" }, 400);
    }

    return jsonResponse({ ok: false, error: "Not found" }, 404);
  },
};
