export async function getJSON(url) {
  const r = await fetch(url, { credentials: 'same-origin' });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    const t = await r.text().catch(() => '');
    throw new Error(`HTTP ${r.status}: ${t}`);
  }
  return r.json();
}

export function apiStatus() { return getJSON('/api/status'); }
export function apiParams() { return getJSON('/api/params'); }
export function apiModels() { return getJSON('/api/models'); }
export function apiChat(message) { return postJSON('/api/chat', { message }); }
export function apiStreamStart(message) { return postJSON('/api/stream/start', { message }); }
export function actionStart(action, args, content=null) { return postJSON('/api/action/start', { action, args: args||{}, content }); }
export function actionResume(state_token, response) { return postJSON('/api/action/resume', { state_token, response }); }
export function actionCancel(state_token) { return postJSON('/api/action/cancel', { state_token }); }

export async function uploadFiles(files) {
  const form = new FormData();
  for (const f of files) form.append('files', f);
  const r = await fetch('/api/upload', { method: 'POST', body: form, credentials: 'same-origin' });
  const body = await r.json().catch(() => ({ ok: false, error: { message: 'Invalid JSON' } }));
  body._status = r.status;
  return body;
}

