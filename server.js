import http from 'node:http';
import crypto from 'node:crypto';

const PORT = Number(process.env.PORT || 3000);
const HOST = '0.0.0.0';
const APP_ACCESS_KEY = process.env.APP_ACCESS_KEY || '';
const MAX_BODY_BYTES = 128 * 1024;
const REQUEST_TIMEOUT_MS = 12000;
const COOLDOWN_MS = 30_000;

// Fixed destinations only. This is a one-shot diagnostic, not a general-purpose proxy.
const TARGETS = [
  { name: 'WhatIsMyIPAddress', url: 'https://whatismyipaddress.com/' },
  { name: 'IPLocation', url: 'https://www.iplocation.net/' },
  { name: '2ip.io', url: 'https://2ip.io/' }
];

const PAGE = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="color-scheme" content="dark" />
  <title>Outbound HTTP Diagnostic</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background:#080d12; color:#e8eef5; }
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; padding:24px; }
    main { width:min(760px,100%); border:1px solid #263746; border-radius:18px; background:#0e1720; padding:clamp(20px,4vw,34px); box-shadow:0 24px 80px #0008; }
    .eyebrow { color:#73b7ff; font-size:12px; letter-spacing:.12em; text-transform:uppercase; font-weight:700; }
    h1 { margin:10px 0 8px; font-size:clamp(25px,4vw,34px); letter-spacing:-.03em; }
    p { color:#a9b8c7; line-height:1.6; }
    .note { padding:12px 14px; border:1px solid #314355; border-radius:12px; background:#111f2b; font-size:13px; }
    label { display:block; margin:22px 0 7px; color:#c8d5e1; font-size:13px; font-weight:650; }
    input { width:100%; padding:13px 14px; border:1px solid #3a4c5e; border-radius:10px; background:#08111a; color:#fff; outline:none; }
    input:focus { border-color:#54a6ff; box-shadow:0 0 0 3px #278bff24; }
    button { margin-top:14px; padding:12px 18px; border:0; border-radius:10px; background:#278bff; color:white; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.55; cursor:wait; }
    #status { min-height:24px; margin-top:14px; color:#a9b8c7; font-size:13px; }
    pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:16px; min-height:100px; max-height:460px; overflow:auto; border:1px solid #263746; border-radius:12px; background:#080f16; color:#cde2f5; font:12px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace; }
    footer { margin-top:14px; color:#728394; font-size:12px; }
  </style>
</head>
<body>
<main>
  <div class="eyebrow">SnapDeploy · outbound test</div>
  <h1>HTTP / CAPTCHA Diagnostic</h1>
  <p>Runs one ordinary GET request to each of the three fixed destinations and reports HTTP status, timing, and visible challenge indicators.</p>
  <div class="note">This tool does not solve CAPTCHAs, imitate a browser, rotate identities, accept arbitrary URLs, or forward user traffic. A server-side HTTP result is not identical to what a real browser would display.</div>
  <label for="key">Diagnostic access key</label>
  <input id="key" type="password" autocomplete="off" placeholder="Enter APP_ACCESS_KEY" />
  <button id="run">Run one diagnostic test</button>
  <div id="status" role="status"></div>
  <pre id="output">No test has been run yet.</pre>
  <footer>One request per destination · 30-second cooldown · response inspection capped at 128 KiB</footer>
</main>
<script>
const button = document.getElementById('run');
const statusEl = document.getElementById('status');
const output = document.getElementById('output');
button.addEventListener('click', async () => {
  const key = document.getElementById('key').value;
  if (!key) { statusEl.textContent = 'Enter the diagnostic access key first.'; return; }
  button.disabled = true;
  statusEl.textContent = 'Testing the three destinations sequentially…';
  output.textContent = 'Waiting for results…';
  try {
    const response = await fetch('/api/test', {
      method: 'POST',
      headers: { 'x-app-key': key }
    });
    const data = await response.json();
    output.textContent = JSON.stringify(data, null, 2);
    statusEl.textContent = response.ok ? 'Test completed.' : (data.error || 'Request failed.');
  } catch (error) {
    statusEl.textContent = 'Could not contact the diagnostic app.';
    output.textContent = String(error);
  } finally {
    button.disabled = false;
  }
});
</script>
</body>
</html>`;

let lastRunAt = 0;
let runInProgress = false;

function send(res, status, body, contentType = 'application/json; charset=utf-8') {
  res.writeHead(status, {
    'content-type': contentType,
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
  });
  res.end(body);
}

function authorized(req) {
  const supplied = String(req.headers['x-app-key'] || '');
  if (!APP_ACCESS_KEY || !supplied) return false;
  const a = Buffer.from(APP_ACCESS_KEY);
  const b = Buffer.from(supplied);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

async function readLimitedText(response, limit = MAX_BODY_BYTES) {
  if (!response.body) return { text: '', bytesRead: 0, truncated: false };
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let bytesRead = 0;
  let text = '';
  let truncated = false;

  try {
    while (bytesRead < limit) {
      const { value, done } = await reader.read();
      if (done) break;
      const remaining = limit - bytesRead;
      const chunk = value.subarray(0, remaining);
      bytesRead += chunk.byteLength;
      text += decoder.decode(chunk, { stream: true });
      if (chunk.byteLength < value.byteLength || bytesRead >= limit) {
        truncated = true;
        await reader.cancel();
        break;
      }
    }
    text += decoder.decode();
  } finally {
    try { reader.releaseLock(); } catch {}
  }
  return { text, bytesRead, truncated };
}

function inspectChallenge(body, status) {
  const html = body.toLowerCase();
  const indicators = [];
  const patterns = [
    ['captcha', /captcha/],
    ['reCAPTCHA', /recaptcha|g-recaptcha|grecaptcha/],
    ['hCaptcha', /hcaptcha/],
    ['Cloudflare challenge', /cf-chl-|challenge-platform|just a moment|checking your browser/],
    ['human verification', /verify (that )?you are human|are you human|unusual traffic/]
  ];
  for (const [name, pattern] of patterns) {
    if (pattern.test(html)) indicators.push(name);
  }

  let assessment = 'no_obvious_challenge_marker';
  if (status === 403 || status === 429) assessment = 'blocked_or_rate_limited';
  else if (indicators.length) assessment = 'challenge_indicators_found';
  else if (status >= 200 && status < 300) assessment = 'no_obvious_challenge_in_sample';
  else assessment = 'http_error';

  return { assessment, indicators };
}

async function testTarget(target) {
  const started = Date.now();
  try {
    const response = await fetch(target.url, {
      method: 'GET',
      redirect: 'follow',
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: {
        'user-agent': 'SnapDeploy-Connectivity-Diagnostic/1.0 (single-request diagnostic; no browser emulation)',
        'accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8'
      }
    });

    const sample = await readLimitedText(response);
    const challenge = inspectChallenge(sample.text, response.status);
    return {
      target: target.name,
      requested_url: target.url,
      final_url: response.url,
      status: response.status,
      latency_ms: Date.now() - started,
      content_type: response.headers.get('content-type'),
      sample_bytes_read: sample.bytesRead,
      sample_truncated: sample.truncated,
      ...challenge
    };
  } catch (error) {
    return {
      target: target.name,
      requested_url: target.url,
      latency_ms: Date.now() - started,
      error: error?.name === 'TimeoutError' ? 'request_timeout' : 'network_or_fetch_error',
      detail: String(error?.message || error).slice(0, 180)
    };
  }
}

const server = http.createServer(async (req, res) => {
  const pathname = new URL(req.url || '/', 'http://localhost').pathname;

  if (req.method === 'GET' && pathname === '/') {
    return send(res, 200, PAGE, 'text/html; charset=utf-8');
  }

  if (req.method === 'GET' && pathname === '/health') {
    return send(res, 200, JSON.stringify({ ok: true, service: 'fixed-target-http-diagnostic' }));
  }

  if (req.method === 'POST' && pathname === '/api/test') {
    if (!authorized(req)) return send(res, 401, JSON.stringify({ error: 'unauthorized' }));
    const now = Date.now();
    if (runInProgress) return send(res, 409, JSON.stringify({ error: 'test_already_running' }));
    if (now - lastRunAt < COOLDOWN_MS) {
      const retryAfter = Math.ceil((COOLDOWN_MS - (now - lastRunAt)) / 1000);
      res.setHeader('retry-after', String(retryAfter));
      return send(res, 429, JSON.stringify({ error: 'cooldown_active', retry_after_seconds: retryAfter }));
    }

    runInProgress = true;
    lastRunAt = now;
    try {
      const results = [];
      for (const target of TARGETS) results.push(await testTarget(target));
      return send(res, 200, JSON.stringify({
        tested_at: new Date().toISOString(),
        note: 'One server-side GET per fixed target. This is not a browser CAPTCHA verdict and does not bypass challenges.',
        results
      }, null, 2));
    } finally {
      runInProgress = false;
    }
  }

  return send(res, 404, JSON.stringify({ error: 'not_found' }));
});

server.listen(PORT, HOST, () => {
  console.log(`Diagnostic app listening on ${HOST}:${PORT}`);
  if (!APP_ACCESS_KEY) console.error('APP_ACCESS_KEY is missing. Set it in the SnapDeploy environment settings.');
});
