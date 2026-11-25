/**
 * FreeDnet - Google Apps Script Relay
 * ----------------------------------
 * Deploy as Web App:
 *   - Execute as: Me
 *   - Who has access: Anyone
 *
 * DEFAULT_SERVER_URL is used when the client does not specify a serverUrl.
 * When multiple Backend Servers (Render / SnappDeploy / Oracle) are configured
 * in the Python client, the client passes serverUrl dynamically for auto-failover.
 */

const DEFAULT_SERVER_URL = "https://your-app.onrender.com/tunnel";
const AUTH_KEY = "change-me-freednet-key";

function normalizeTunnelUrl(rawUrl) {
  const trimmed = String(rawUrl || "").trim().replace(/\/+$/, "");
  if (!trimmed) return DEFAULT_SERVER_URL;
  return trimmed.endsWith("/tunnel") ? trimmed : trimmed + "/tunnel";
}

function doGet(e) {
  return jsonResponse({
    ok: true,
    service: "FreeDnet GAS Relay",
    ts: Date.now()
  });
}

function doPost(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) {
      return jsonResponse({ ok: false, error: "Empty request body" });
    }

    const payload = JSON.parse(e.postData.contents);
    if (!payload || payload.authKey !== AUTH_KEY) {
      return jsonResponse({ ok: false, error: "Unauthorized" });
    }

    const targetUrl = normalizeTunnelUrl(payload.serverUrl);

    const options = {
      method: "post",
      contentType: "application/json",
      headers: {
        "X-Auth-Key": AUTH_KEY
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };

    const response = UrlFetchApp.fetch(targetUrl, options);
    const code = response.getResponseCode();
    const text = response.getContentText();

    if (code < 200 || code >= 300) {
      return jsonResponse({
        ok: false,
        upstreamError: true,
        statusCode: code,
        error: "Upstream HTTP " + code,
        detail: text ? text.slice(0, 300) : ""
      });
    }

    return ContentService
      .createTextOutput(text)
      .setMimeType(ContentService.MimeType.JSON);
  } catch (err) {
    return jsonResponse({
      ok: false,
      upstreamError: true,
      error: err && err.message ? err.message : String(err)
    });
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
