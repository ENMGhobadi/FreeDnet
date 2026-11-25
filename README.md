# SnapDeploy HTTP / CAPTCHA Diagnostic

A small Node.js diagnostic app. It makes exactly one ordinary server-side GET request to each of three hard-coded public destinations:

- https://whatismyipaddress.com/
- https://www.iplocation.net/
- https://2ip.io/

It reports status, response timing, a bounded response sample, and common CAPTCHA/challenge markers. It does not solve CAPTCHAs, imitate a browser, rotate identities, accept arbitrary URLs, or forward user traffic.

## Requirements

- Node.js 20 or newer
- Set `APP_ACCESS_KEY` as a runtime environment secret. Use a long random value (at least 32 bytes).
- SnapDeploy supplies `PORT`; the app listens on `0.0.0.0`.

## Run locally

```bash
npm start
```

Open `http://localhost:3000`, enter the same `APP_ACCESS_KEY`, and run one test.

## Deploy

Push `package.json`, `server.js`, and this README to a GitHub repository. In SnapDeploy choose **Deploy → GitHub repository**, select the repository and branch, and set the runtime variable `APP_ACCESS_KEY` during deployment or later in Container Settings. Do not commit the key to GitHub. SnapDeploy detects the Node.js app and its start script.

## Interpretation

- HTTP 2xx with no visible marker: no obvious challenge was found in the sampled response; this does not prove browser access will be CAPTCHA-free.
- HTTP 403 / 429: blocked or rate-limited response.
- Challenge marker: a likely CAPTCHA, browser challenge, or human-verification page was detected.
- Network error / timeout: no usable response was received.

The app inspects at most 128 KiB of each response and enforces a 30-second cooldown between test runs.
