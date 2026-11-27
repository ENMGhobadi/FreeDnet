'use strict';

const dns = require('dns');
if (typeof dns.setDefaultResultOrder === 'function') {
  dns.setDefaultResultOrder('ipv4first');
}

const http = require('http');
const net = require('net');
const crypto = require('crypto');
const zlib = require('zlib');

const PORT = parseInt(process.env.PORT || '3000', 10);
const AUTH_KEY = process.env.AUTH_KEY || 'change-me-freednet-key';
const SESSION_IDLE_MS = parseInt(process.env.SESSION_IDLE_MS || '300000', 10); // 5 minutes
const MAX_BODY_BYTES = 4 * 1024 * 1024; // 4 MB
const MAX_READ_BUFFER_BYTES = 1536 * 1024; // 1.5 MB
const MAX_SYNC_CHUNK_BYTES = 1024 * 1024; // 1 MB

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
const openIdMap = new Map(); // clientOpenId -> { sessionId, createdAt }

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

  const clientOpenId = body.clientOpenId ? String(body.clientOpenId) : '';
  if (clientOpenId && openIdMap.has(clientOpenId)) {
    const existingId = openIdMap.get(clientOpenId).sessionId;
    const existing = sessions.get(existingId);
    if (existing) {
      return sendJson(res, 200, {
        ok: true,
        sessionId: existingId,
        data: '',
        closed: existing.closed && existing.chunks.length === 0 && !existing.unacked
      });
    }
  }

  const sessionId = crypto.randomUUID();
  if (clientOpenId) {
    openIdMap.set(clientOpenId, { sessionId, createdAt: Date.now() });
  }
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
    nextTxSeq: 1,
    pendingTx: new Map(),
    rxSeq: 0,
    unacked: null,
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
      if (outBuf.length > 0) {
        session.rxSeq += 1;
        session.unacked = { seq: session.rxSeq, buf: outBuf };
      }
      sendJson(res, 200, {
        ok: true,
        sessionId,
        rxSeq: outBuf.length > 0 ? session.rxSeq : 0,
        data: outBuf.length ? outBuf.toString('base64') : '',
        closed: session.closed && session.chunks.length === 0 && !session.unacked
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
    return sendJson(res, 404, { ok: false, sessionError: true, error: 'Session not found', closed: true });
  }

  session.lastActivity = Date.now();

  if ((body.dataZ || body.data) && !session.closed) {
    const txSeq = Number.isInteger(body.txSeq) ? body.txSeq : 0;
    if (txSeq > 0 && (txSeq < session.nextTxSeq || session.pendingTx.has(txSeq))) {
      // Duplicate retried chunk — skip socket.write to protect TLS/TCP stream integrity
    } else {
      try {
        let writeBuf;
        if (body.dataZ) {
          writeBuf = zlib.gunzipSync(Buffer.from(body.dataZ, 'base64'));
        } else {
          writeBuf = Buffer.from(body.data, 'base64');
        }
        if (txSeq > 0) {
          if (session.pendingTx.size < 32) {
            session.pendingTx.set(txSeq, writeBuf);
          }
          while (session.pendingTx.has(session.nextTxSeq)) {
            const ordered = session.pendingTx.get(session.nextTxSeq);
            session.pendingTx.delete(session.nextTxSeq);
            if (ordered.length > 0) session.socket.write(ordered);
            session.nextTxSeq += 1;
          }
        } else if (writeBuf.length > 0) {
          session.socket.write(writeBuf);
        }
      } catch (err) {
        return sendJson(res, 400, { ok: false, error: 'Invalid payload data' });
      }
    }
  }

  if (body.txFin && !session.closed) {
    try {
      session.socket.end();
    } catch (_) {}
  }

  const hasAck = Number.isInteger(body.ackSeq);
  if (!body.txOnly && session.unacked) {
    if (!hasAck || body.ackSeq >= session.unacked.seq) {
      session.unacked = null;
    }
  }

  const waitMs = Math.max(0, Math.min(parseInt(body.waitMs || '0', 10) || 0, 10000));
  const acceptGzip = Boolean(body.acceptGzip);

  const finish = () => {
    if (body.txOnly) {
      const txClosed = session.closed && session.chunks.length === 0 && !session.unacked;
      return sendJson(res, 200, { ok: true, data: '', closed: txClosed });
    }
    let outBuf;
    let outSeq = session.rxSeq;
    if (session.unacked && hasAck && body.ackSeq < session.unacked.seq) {
      outBuf = session.unacked.buf;
      outSeq = session.unacked.seq;
    } else {
      outBuf = drainSessionData(session);
      if (outBuf.length > 0) {
        session.rxSeq += 1;
        outSeq = session.rxSeq;
        if (hasAck) {
          session.unacked = { seq: outSeq, buf: outBuf };
        }
      }
    }
    const isClosed = session.closed && session.chunks.length === 0 && !session.unacked;
    if (isClosed) {
      sessions.delete(sessionId);
    }
    const looksLikeTls = outBuf.length >= 2 && outBuf[0] >= 0x14 && outBuf[0] <= 0x17 && outBuf[1] === 0x03;
    if (acceptGzip && !looksLikeTls && outBuf.length > 512 && outBuf.length <= 1048576) {
      try {
        const gz = zlib.gzipSync(outBuf, { level: 1 });
        if (gz.length < outBuf.length) {
          return sendJson(res, 200, { ok: true, rxSeq: outSeq, dataZ: gz.toString('base64'), closed: isClosed });
        }
      } catch (_) {}
    }
    sendJson(res, 200, {
      ok: true,
      rxSeq: outSeq,
      data: outBuf.length ? outBuf.toString('base64') : '',
      closed: isClosed
    });
  };

  if (session.unacked || session.chunks.length > 0 || session.closed || waitMs === 0) {
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
  for (const [oid, meta] of openIdMap.entries()) {
    if (now - meta.createdAt > 120000) {
      openIdMap.delete(oid);
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


function wsEncodeFrame(opcode, payloadBuf) {
  const len = payloadBuf ? payloadBuf.length : 0;
  let hdr;
  if (len < 126) {
    hdr = Buffer.allocUnsafe(2);
    hdr[0] = 0x80 | (opcode & 0x0f);
    hdr[1] = len;
  } else if (len < 65536) {
    hdr = Buffer.allocUnsafe(4);
    hdr[0] = 0x80 | (opcode & 0x0f);
    hdr[1] = 126;
    hdr.writeUInt16BE(len, 2);
  } else {
    hdr = Buffer.allocUnsafe(10);
    hdr[0] = 0x80 | (opcode & 0x0f);
    hdr[1] = 127;
    hdr.writeBigUInt64BE(BigInt(len), 2);
  }
  return len > 0 ? Buffer.concat([hdr, payloadBuf]) : hdr;
}

server.on('upgrade', (req, clientSocket, head) => {
  try {
    const u = new URL(req.url, 'http://localhost');
    if (u.pathname !== '/ws') {
      clientSocket.write('HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n');
      return clientSocket.destroy();
    }
    const providedKey = req.headers['x-auth-key'] || u.searchParams.get('authKey') || '';
    if (!safeEqual(String(providedKey), AUTH_KEY)) {
      clientSocket.write('HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n');
      return clientSocket.destroy();
    }
    const targetHost = String(u.searchParams.get('host') || '').trim();
    const targetPort = parseInt(u.searchParams.get('port') || '0', 10);
    if (!targetHost || !Number.isInteger(targetPort) || targetPort < 1 || targetPort > 65535) {
      clientSocket.write('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
      return clientSocket.destroy();
    }
    const wsKey = req.headers['sec-websocket-key'];
    if (!wsKey) {
      clientSocket.write('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');
      return clientSocket.destroy();
    }

    const family = net.isIP(targetHost) === 6 ? 6 : 4;
    const targetSock = net.createConnection({ host: targetHost, port: targetPort, family });
    targetSock.setNoDelay(true);
    clientSocket.setNoDelay(true);

    let upgraded = false;
    let wsBuf = head && head.length ? Buffer.from(head) : Buffer.alloc(0);
    let closed = false;

    const cleanup = () => {
      if (closed) return;
      closed = true;
      try { targetSock.destroy(); } catch (_) {}
      try { clientSocket.destroy(); } catch (_) {}
    };

    const connectTimer = setTimeout(() => {
      if (!upgraded) {
        try { clientSocket.write('HTTP/1.1 504 Gateway Timeout\r\nConnection: close\r\n\r\n'); } catch (_) {}
        cleanup();
      }
    }, 15000);

    const processWsFrames = () => {
      while (wsBuf.length >= 2) {
        const b0 = wsBuf[0];
        const b1 = wsBuf[1];
        const opcode = b0 & 0x0f;
        const masked = (b1 & 0x80) !== 0;
        let payloadLen = b1 & 0x7f;
        let offset = 2;
        if (payloadLen === 126) {
          if (wsBuf.length < 4) return;
          payloadLen = wsBuf.readUInt16BE(2);
          offset = 4;
        } else if (payloadLen === 127) {
          if (wsBuf.length < 10) return;
          payloadLen = Number(wsBuf.readBigUInt64BE(2));
          offset = 10;
        }
        const maskLen = masked ? 4 : 0;
        if (wsBuf.length < offset + maskLen + payloadLen) return;
        const maskKey = masked ? wsBuf.subarray(offset, offset + 4) : null;
        offset += maskLen;
        const payload = Buffer.from(wsBuf.subarray(offset, offset + payloadLen));
        wsBuf = wsBuf.subarray(offset + payloadLen);
        if (masked && maskKey) {
          for (let i = 0; i < payload.length; i++) {
            payload[i] ^= maskKey[i & 3];
          }
        }
        if (opcode === 0x8) {
          try { clientSocket.write(wsEncodeFrame(0x8, Buffer.alloc(0))); } catch (_) {}
          return cleanup();
        }
        if (opcode === 0x9) {
          try { clientSocket.write(wsEncodeFrame(0xA, payload)); } catch (_) {}
          continue;
        }
        if ((opcode === 0x2 || opcode === 0x1 || opcode === 0x0) && payload.length > 0) {
          if (!targetSock.destroyed) targetSock.write(payload);
        }
      }
    };

    targetSock.on('connect', () => {
      clearTimeout(connectTimer);
      upgraded = true;
      const accept = crypto
        .createHash('sha1')
        .update(String(wsKey) + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
        .digest('base64');
      clientSocket.write(
        'HTTP/1.1 101 Switching Protocols\r\n' +
        'Upgrade: websocket\r\n' +
        'Connection: Upgrade\r\n' +
        `Sec-WebSocket-Accept: ${accept}\r\n\r\n`
      );
      if (wsBuf.length > 0) processWsFrames();
    });

    clientSocket.on('data', (chunk) => {
      wsBuf = wsBuf.length ? Buffer.concat([wsBuf, chunk]) : chunk;
      if (upgraded) processWsFrames();
    });

    targetSock.on('data', (chunk) => {
      if (!upgraded || closed) return;
      try {
        const ok = clientSocket.write(wsEncodeFrame(0x2, chunk));
        if (!ok) targetSock.pause();
      } catch (_) {
        cleanup();
      }
    });

    clientSocket.on('drain', () => {
      if (!closed && !targetSock.destroyed) targetSock.resume();
    });

    targetSock.on('end', () => {
      if (upgraded && !closed) {
        try { clientSocket.end(wsEncodeFrame(0x8, Buffer.alloc(0))); } catch (_) {}
      }
      cleanup();
    });
    targetSock.on('error', () => {
      if (!upgraded) {
        try { clientSocket.write('HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n'); } catch (_) {}
      }
      cleanup();
    });
    clientSocket.on('error', cleanup);
    clientSocket.on('close', cleanup);
  } catch (_) {
    try { clientSocket.destroy(); } catch (__) {}
  }
});

server.keepAliveTimeout = 60000;
server.headersTimeout = 65000;

server.listen(PORT, '0.0.0.0', () => {
  console.log(`FreeDnet server listening on port ${PORT}`);
});
