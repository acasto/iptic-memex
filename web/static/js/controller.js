// Controller: subscribes to UI intents on the bus and calls API
// Event map (bus types)
//  Intents from UI:
//   - controller:chat:send { text }
//   - controller:stream:start { text, messageId }
//   - controller:action:start { action, args, content }
//   - controller:action:resume { stateToken, response }
//   - controller:action:cancel { stateToken }
//   - controller:upload { files }
//  Outputs (consumed by UI):
//   - chat:response { text, updates }
//   - chat:error { message }
//   - sse:start|sse:token|sse:done|sse:error (emitted by sse.js)
//   - action:needs { needs, stateToken, updates, text }
//   - action:done { payload, updates, text }
//   - action:error { message }
//   - action:cancelled { ok, done }
//   - upload:done { files }
//   - upload:error { message }
import { on, emit } from './bus.js';
import { apiChat, apiStreamStart, apiStreamCancel, actionStart, actionResume, actionCancel, uploadFiles } from './api.js';
import { openEventSource } from './sse.js';

// Non-stream chat send
on('controller:chat:send', async (ev) => {
  const d = (ev && ev.detail) || {};
  const text = (d.text || '').trim();
  if (!text) return;
  try {
    const res = await apiChat(text);
    if (res && res.needs_interaction && res.state_token) {
      emit('action:needs', { needs: res.needs_interaction, stateToken: res.state_token, updates: res.updates || [], text: res.text || '' });
    } else {
      emit('chat:response', { text: (res && res.text) || '', updates: (res && res.updates) || [], handled: !!(res && res.handled), command: (res && res.command) || null });
    }
  } catch (e) {
    emit('chat:error', { message: e && e.message ? e.message : String(e) });
  }
});

// Stream start: acquire token and open SSE (sse.js emits token/done/error)
const _streams = new Map(); // messageId -> { es, token }
let _currentStreamId = null;

on('controller:stream:start', async (ev) => {
  const d = (ev && ev.detail) || {};
  const text = (d.text || '').trim();
  const messageId = d.messageId;
  if (!text) return;
  try {
    const init = await apiStreamStart(text);
    const token = init && init.token; if (!token) throw new Error('No stream token');
    const es = openEventSource(token, { messageId });
    _streams.set(messageId, { es, token });
    _currentStreamId = messageId;
  } catch (e) {
    // Fallback to non-stream
    emit('controller:chat:send', { text });
  }
});

// Track lifecycle to clear current mapping
on('sse:done', (ev) => {
  const d = (ev && ev.detail) || {};
  const id = d.messageId;
  if (id && _streams.has(id)) _streams.delete(id);
  if (_currentStreamId === id) _currentStreamId = null;
});
on('sse:error', (ev) => {
  const d = (ev && ev.detail) || {};
  const id = d.messageId;
  if (id && _streams.has(id)) _streams.delete(id);
  if (_currentStreamId === id) _currentStreamId = null;
});

// External stop request (e.g., Stop button)
on('controller:stream:stop', async () => {
  const id = _currentStreamId;
  if (!id) return;
  const rec = _streams.get(id);
  if (!rec) return;
  try {
    try { rec.es.close(); } catch {}
    try { await apiStreamCancel(rec.token); } catch {}
  } finally {
    _streams.delete(id);
    _currentStreamId = null;
    emit('stream:stopped', { messageId: id });
  }
});

// Action start
on('controller:action:start', async (ev) => {
  const d = (ev && ev.detail) || {};
  const action = (d.action || '').trim();
  const args = d.args || {};
  const content = d.content == null ? null : d.content;
  if (!action) return;
  try {
    const res = await actionStart(action, args, content);
    if (res && res.done) {
      emit('action:done', { payload: res.payload, updates: res.updates || [], text: res.text || '' });
    } else if (res && res.needs_interaction && res.state_token) {
      emit('action:needs', { needs: res.needs_interaction, stateToken: res.state_token, updates: res.updates || [], text: res.text || '' });
    } else {
      emit('action:error', { message: 'Unexpected action start result' });
    }
  } catch (e) {
    emit('action:error', { message: e && e.message ? e.message : String(e) });
  }
});

// Action resume
on('controller:action:resume', async (ev) => {
  const d = (ev && ev.detail) || {};
  const token = d.stateToken || d.state_token;
  const response = d.response;
  if (!token) return;
  try {
    const res = await actionResume(token, response);
    if (res && res.done) {
      emit('action:done', { payload: res.payload, updates: res.updates || [], text: res.text || '' });
    } else if (res && res.needs_interaction && res.state_token) {
      emit('action:needs', { needs: res.needs_interaction, stateToken: res.state_token, updates: res.updates || [], text: res.text || '' });
    } else {
      emit('action:error', { message: 'Unexpected action resume result' });
    }
  } catch (e) {
    emit('action:error', { message: e && e.message ? e.message : String(e) });
  }
});

// Action cancel
on('controller:action:cancel', async (ev) => {
  const d = (ev && ev.detail) || {};
  const token = d.stateToken || d.state_token;
  if (!token) return;
  try {
    const res = await actionCancel(token);
    emit('action:cancelled', { ok: !!(res && res.ok), done: !!(res && res.done) });
  } catch (e) {
    emit('action:error', { message: e && e.message ? e.message : String(e) });
  }
});

// Upload files
on('controller:upload', async (ev) => {
  const d = (ev && ev.detail) || {};
  const files = d.files;
  if (!files || !files.length) return;
  try {
    const res = await uploadFiles(files);
    if (!res || res.ok === false || res._status !== 200) {
      emit('upload:error', { message: (res && res.error && res.error.message) ? res.error.message : 'Upload failed' });
    } else {
      emit('upload:done', { files: res.files || [] });
    }
  } catch (e) {
    emit('upload:error', { message: e && e.message ? e.message : String(e) });
  }
});
