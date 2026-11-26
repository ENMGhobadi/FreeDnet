'use strict';

const http = require('http');
const net = require('net');
const crypto = require('crypto');
const zlib = require('zlib');

const PORT = parseInt(process.env.PORT || '3000', 10);
const AUTH_KEY = process.env.AUTH_KEY || 'change-me-freednet-key';
const SESSION_IDLE_MS = parseInt(process.env.SESSION_IDLE_MS || '300000', 10); // 5 minutes
const MAX_BODY_BYTES = 4 * 1024 * 1024; // 4 MB
const MAX_READ_BUFFER_BYTES = 4 * 1024 * 1024; // 4 MB
const MAX_SYNC_CHUNK_BYTES = 1536 * 1024; // 1.5 MB

/** @type {Map<string, {
 *   id: string,
 *   socket: net.Socket,
 *   chunks: Buffer[],
 *   bufferedBytes: number,
 *   closed: boolean,
 *   error: string | null,
 *   lastActivity: number,
 *   waiters: Array<() => void>
 * }>} */
const sessions = new Map();

function safeEqual(a, b) {
  if (typeof a !== 'string' || typeof b !== 'string') return false;
  const bufA = Buffer.from(a);
  const bufB = Buffer.from(b);
  if (bufA.length !== bufB.length) return false;
  return crypto.timingSafeEqual(bufA, bufB);
}

function sendJson(res, statusCode, obj) {
  const payload = JSON.stringify(obj);
  res.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store'
  });
  res.end(payload);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    const parts = [];
    let total = 0;
    req.on('data', (chunk) => {
      total += chunk.length;
      if (total > MAX_BODY_BYTES) {
        reject(new Error('Payload too large'));
        req.destroy();
        return;
      }
      parts.push(chunk);
    });
    req.on('end', () => {
      try {
        const raw = Buffer.concat(parts).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(new Error('Invalid JSON'));
      }
    });
    req.on('error', reject);
  });
}

function notifyWaiters(session) {
  while (session.waiters.length > 0) {
    const fn = session.waiters.shift();
    try {
      fn();
    } catch (_) {}
  }
}

function drainSessionData(session, maxBytes = MAX_SYNC_CHUNK_BYTES) {
  if (session.chunks.length === 0) return Buffer.alloc(0);
  const out = [];
  let collected = 0;

  while (session.chunks.length > 0 && collected < maxBytes) {
    const head = session.chunks[0];
    const remaining = maxBytes - collected;
    if (head.length <= remaining) {
      out.push(head);
      collected += head.length;
      session.chunks.shift();
    } else {
      out.push(head.subarray(0, remaining));
      session.chunks[0] = head.subarray(remaining);
      collected += remaining;
    }
  }

  session.bufferedBytes -= collected;
  if (session.bufferedBytes < MAX_READ_BUFFER_BYTES / 2 && !session.closed) {
    session.socket.resume();
  }
  return Buffer.concat(out);
}

function closeSession(sessionId) {
  const session = sessions.get(sessionId);
  if (!session) return;
  session.closed = true;
  notifyWaiters(session);
  try {
    session.socket.destroy();
  } catch (_) {}
  sessions.delete(sessionId);
}


const HOP_BY_HOP_REQ = new Set([
  'host', 'connection', 'keep-alive', 'proxy-connection', 'proxy-authenticate',
  'proxy-authorization', 'te', 'trailers', 'transfer-encoding', 'upgrade',
  'content-length', 'accept-encoding'
]);
const HOP_BY_HOP_RESP = new Set([
  'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding',
  'content-encoding', 'content-length', 'upgrade'
]);

async function handleHttpFetch(body, res) {
  const targetUrl = String(body.url || '').trim();
  const method = String(body.method || 'GET').toUpperCase();
  if (!/^https?:\/\//i.test(targetUrl)) {
    return sendJson(res, 400, { ok: false, error: 'Invalid URL' });
  }

  const reqHeaders = {};
  if (body.headers && typeof body.headers === 'object') {
    for (const [k, v] of Object.entries(body.headers)) {
      if (!HOP_BY_HOP_REQ.has(k.toLowerCase()) && v !== undefined && v !== null) {
        reqHeaders[k] = String(v);
      }
    }
  }

  const fetchOpts = { method, headers: reqHeaders, redirect: 'manual' };
  if (body.body && method !== 'GET' && method !== 'HEAD') {
    fetchOpts.body = Buffer.from(body.body, 'base64');
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 25000);
  fetchOpts.signal = ctrl.signal;

  try {
    const resp = await fetch(targetUrl, fetchOpts);
    clearTimeout(timer);
    const arr = new Uint8Array(await resp.arrayBuffer());
    const buf = Buffer.from(arr);

    const outHeaders = {};
    resp.headers.forEach((val, key) => {
      if (!HOP_BY_HOP_RESP.has(key.toLowerCase())) {
        outHeaders[key] = val;
      }
    });

    const acceptGzip = Boolean(body.acceptGzip);
    if (acceptGzip && buf.length > 512 && buf.length <= 2 * 1024 * 1024) {
      try {
        const gz = zlib.gzipSync(buf, { level: 1 });
        if (gz.length < buf.length) {
          return sendJson(res, 200, {
            ok: true,
            status: resp.status,
            statusText: resp.statusText || '',
            headers: outHeaders,
            bodyZ: gz.toString('base64')
          });
        }
      } catch (_) {}
    }

    return sendJson(res, 200, {
      ok: true,
      status: resp.status,
      statusText: resp.statusText || '',
      headers: outHeaders,
      body: buf.length ? buf.toString('base64') : ''
    });
  } catch (err) {
    clearTimeout(timer);
    return sendJson(res, 502, {
      ok: false,
      targetError: true,
      error: err && err.message ? err.message : String(err)
    });
  }
}

function handleOpen(body, res) {
  const targetHost = String(body.targetHost || '').trim();
  const targetPort = parseInt(body.targetPort, 10);

  if (!targetHost || !Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) {
    return sendJson(res, 400, { ok: false, error: 'Invalid targetHost or targetPort' });
  }

  const sessionId = crypto.randomUUID();
  const family = net.isIP(targetHost) === 6 ? 6 : 4;
  const socket = net.createConnection({ host: targetHost, port: targetPort, family });
  socket.setNoDelay(true);

  const session = {
    id: sessionId,
    socket,
    chunks: [],
    bufferedBytes: 0,
    closed: false,
    error: null,
    lastActivity: Date.now(),
    waiters: []
  };
  sessions.set(sessionId, session);

  let responded = false;
  const connectTimer = setTimeout(() => {
    if (!responded) {
      responded = true;
      closeSession(sessionId);
      sendJson(res, 504, { ok: false, targetError: true, error: 'Target connection timeout' });
    }
  }, 15000);

  socket.on('connect', () => {
    clearTimeout(connectTimer);
    session.lastActivity = Date.now();

    if (body.data) {
      try {
        const initBuf = Buffer.from(body.data, 'base64');
        if (initBuf.length > 0) socket.write(initBuf);
      } catch (_) {}
    }

    // Brief wait (60ms) to capture immediate server banner/handshake if any
    setTimeout(() => {
      if (responded) return;
      responded = true;
      const outBuf = drainSessionData(session);
      sendJson(res, 200, {
        ok: true,
        sessionId,
        data: outBuf.length ? outBuf.toString('base64') : '',
        closed: session.closed
      });
    }, 60);
  });

  socket.on('data', (chunk) => {
    session.lastActivity = Date.now();
    session.chunks.push(chunk);
    session.bufferedBytes += chunk.length;
    if (session.bufferedBytes >= MAX_READ_BUFFER_BYTES) {
      socket.pause();
    }
    notifyWaiters(session);
  });

  socket.on('error', (err) => {
    clearTimeout(connectTimer);
    session.error = err.message;
    session.closed = true;
    notifyWaiters(session);
    if (!responded) {
      responded = true;
      sessions.delete(sessionId);
      sendJson(res, 502, { ok: false, targetError: true, error: err.message });
    }
  });

  socket.on('close', () => {
    clearTimeout(connectTimer);
    session.closed = true;
    notifyWaiters(session);
  });
}

function handleSync(body, res) {
  const sessionId = String(body.sessionId || '');
  const session = sessions.get(sessionId);
  if (!session) {
    return sendJson(res, 404, { ok: false, error: 'Session not found', closed: true });
  }

  session.lastActivity = Date.now();

  if ((body.dataZ || body.data) && !session.closed) {
    try {
      let writeBuf;
      if (body.dataZ) {
        writeBuf = zlib.gunzipSync(Buffer.from(body.dataZ, 'base64'));
      } else {
        writeBuf = Buffer.from(body.data, 'base64');
      }
      if (writeBuf.length > 0) {
        session.socket.write(writeBuf);
      }
    } catch (err) {
      return sendJson(res, 400, { ok: false, error: 'Invalid payload data' });
    }
  }

  const waitMs = Math.max(0, Math.min(parseInt(body.waitMs || '0', 10) || 0, 10000));
  const acceptGzip = Boolean(body.acceptGzip);

  const finish = () => {
    if (body.txOnly) {
      return sendJson(res, 200, { ok: true, data: '', closed: session.closed });
    }
    const outBuf = drainSessionData(session);
    const isClosed = session.closed && session.chunks.length === 0;
    if (isClosed) {
      sessions.delete(sessionId);
    }
    const looksLikeTls = outBuf.length >= 2 && outBuf[0] >= 0x14 && outBuf[0] <= 0x17 && outBuf[1] === 0x03;
    if (acceptGzip && !looksLikeTls && outBuf.length > 512 && outBuf.length <= 524288) {
      try {
        const gz = zlib.gzipSync(outBuf, { level: 1 });
        if (gz.length < outBuf.length) {
          return sendJson(res, 200, { ok: true, dataZ: gz.toString('base64'), closed: isClosed });
        }
      } catch (_) {}
    }
    sendJson(res, 200, {
      ok: true,
      data: outBuf.length ? outBuf.toString('base64') : '',
      closed: isClosed
    });
  };

  if (session.chunks.length > 0 || session.closed || waitMs === 0) {
    return finish();
  }

  let done = false;
  const onEvent = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    if (session.chunks.length > 0 && !session.closed && session.bufferedBytes < 65536) {
      setTimeout(finish, 8);
    } else {
      finish();
    }
  };

  const timer = setTimeout(onEvent, waitMs);
  session.waiters.push(onEvent);
}

// Cleanup idle sessions periodically
setInterval(() => {
  const now = Date.now();
  for (const [id, session] of sessions.entries()) {
    if (now - session.lastActivity > SESSION_IDLE_MS) {
      closeSession(id);
    }
  }
}, 30000).unref();

const server = http.createServer(async (req, res) => {
  if (req.method === 'GET' && (req.url === '/' || req.url === '/health')) {
    return sendJson(res, 200, {
      ok: true,
      service: 'FreeDnet',
      activeSessions: sessions.size,
      ts: Date.now()
    });
  }

  if (req.method === 'POST' && req.url === '/tunnel') {
    let body;
    try {
      body = await readJsonBody(req);
    } catch (err) {
      return sendJson(res, 400, { ok: false, error: err.message });
    }

    const providedKey = req.headers['x-auth-key'] || body.authKey || '';
    if (!safeEqual(String(providedKey), AUTH_KEY)) {
      return sendJson(res, 401, { ok: false, error: 'Unauthorized' });
    }

    const action = body.action;
    if (action === 'ping') {
      return sendJson(res, 200, {
        ok: true,
        service: 'FreeDnet',
        activeSessions: sessions.size,
        ts: Date.now()
      });
    }
    if (action === 'open') {
      return handleOpen(body, res);
    }
    if (action === 'sync') {
      return handleSync(body, res);
    }
    if (action === 'close') {
      closeSession(String(body.sessionId || ''));
      return sendJson(res, 200, { ok: true });
    }
    if (action === 'http_fetch') {
      return handleHttpFetch(body, res);
    }

    return sendJson(res, 400, { ok: false, error: 'Unknown action' });
  }

  sendJson(res, 404, { ok: false, error: 'Not found' });
});

server.keepAliveTimeout = 60000;
server.headersTimeout = 65000;

server.listen(PORT, '0.0.0.0', () => {
  console.log(`FreeDnet server listening on port ${PORT}`);
});
