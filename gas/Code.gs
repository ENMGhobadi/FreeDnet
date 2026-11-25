/**
 * FreeDnet - Google Apps Script Relay
 * ----------------------------------
 * Deploy as Web App:
 *   - Execute as: Me
 *   - Who has access: Anyone
 *
 * Set SERVER_URL to your Render / SnappDeploy / Oracle / Cloudflare Worker URL + "/tunnel"
 * Set AUTH_KEY to match the AUTH_KEY environment variable on your server and in the Python client.
 */

const SERVER_URL = "https://your-app.onrender.com/tunnel";
const AUTH_KEY = "change-me-freednet-key";

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

    const options = {
      method: "post",
      contentType: "application/json",
      headers: {
        "X-Auth-Key": AUTH_KEY
      },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true
    };

    const response = UrlFetchApp.fetch(SERVER_URL, options);
    const code = response.getResponseCode();
    const text = response.getContentText();

    if (code < 200 || code >= 300) {
      return jsonResponse({
        ok: false,
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
      error: err && err.message ? err.message : String(err)
    });
  }
}

function jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
