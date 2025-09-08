import { apiStatus, apiParams } from './api.js';
import './controller.js';
import { on, emit } from './bus.js';
import { RafTextAppender } from './raf_batch.js';
import { getState, setState, subscribe, addMessage, appendMessage, clearMessages } from './store.js';

const log = document.getElementById('log');
const jumpBtn = document.getElementById('jumpLatest');
const msg = document.getElementById('msg');
const sendBtn = document.getElementById('send');
const statusEl = document.getElementById('status');
const panel = document.getElementById('panel');
const streamEl = document.getElementById('stream');
const newChatBtn = document.getElementById('newchat');
const attachBtn = document.getElementById('attach');
const optionsBtn = document.getElementById('options');
const darkToggle = document.getElementById('darkmode');
const stopBtn = document.getElementById('stop');

// Store-backed message rendering
const _nodes = new Map(); // id -> content span
function isAtBottom(el) { return (el.scrollHeight - el.scrollTop - el.clientHeight) < 8; }
function scrollToBottom(el) { try { el.scrollTop = el.scrollHeight; } catch {} }
function updateJumpVisibility() { if (!jumpBtn) return; jumpBtn.style.display = isAtBottom(log) ? 'none' : 'inline-block'; }
function clearMessagesDOM() {
  const kids = Array.from(log.childNodes);
  for (const k of kids) {
    if (k.nodeType === 1 && k.id === 'jumpLatest') continue;
    log.removeChild(k);
  }
  _nodes.clear();
  updateJumpVisibility();
}
function renderMessageNode(msg) {
  const auto = isAtBottom(log);
  const hadAny = _nodes.size > 0;
  let span = _nodes.get(msg.id);
  if (span) {
    span.textContent = (msg.text || '');
    // Ensure container has role-specific class for styling
    const container = span.closest('.msg');
    if (container) {
      container.classList.remove('assistant', 'user');
      if (msg.role === 'assistant' || msg.role === 'user') container.classList.add(msg.role);
    }
    if (auto) scrollToBottom(log); else updateJumpVisibility();
    return span;
  }
  const d = document.createElement('div'); d.className = 'msg';
  if (msg.role === 'assistant' || msg.role === 'user') d.classList.add(msg.role);
  const r = document.createElement('span'); r.className = 'role'; r.textContent = msg.role + ':';
  const c = document.createElement('span'); c.className = 'content'; c.textContent = (msg.text || '');
  d.appendChild(r); d.appendChild(c); log.appendChild(d);
  // Only auto-scroll on new insert when we already had some content (avoid jumping on the very first message)
  if (auto && hadAny) scrollToBottom(log); else updateJumpVisibility();
  _nodes.set(msg.id, c); return c;
}
function addAndRenderMessage(role, text) {
  const id = addMessage({ role, text });
  const st = getState(); const msg = st.messages.find(m => m.id === id);
  if (msg) renderMessageNode(msg);
  return id;
}

async function refreshStatus() {
  try {
    const d = await apiStatus();
    const model = d && d.model ? d.model : 'unknown';
    const provider = d && d.provider ? d.provider : '';
    const text = 'Ready. Model: ' + model + (provider ? (' - Provider: ' + provider) : '');
    setState({ status: text });
  } catch {
    setState({ status: 'Ready.' });
  }
  try {
    const p = await apiParams();
    if (p && p.params && 'stream' in p.params) setState({ stream: !!p.params.stream });
  } catch {}
}

function sendNonStream(text) { emit('controller:chat:send', { text }); }

function sendStream(text) {
  // Create assistant message and a rAF-batched appender
  const msgId = addAndRenderMessage('assistant', '');
  const target = _nodes.get(msgId);
  const appender = new RafTextAppender(target);
  const messageId = Math.random().toString(36).slice(2);

  // Wire bus listeners scoped by messageId
  const offToken = on('sse:token', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    const auto = isAtBottom(log);
    appender.append(d && d.text ? d.text : '');
    if (auto) requestAnimationFrame(() => { scrollToBottom(log); updateJumpVisibility(); });
    else updateJumpVisibility();
  });
  const offDone = on('sse:done', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    try {
      if (d && d.updates) setPanelUpdates(d.updates);
      if (d && d.needs_interaction && d.state_token) renderInteraction(d.needs_interaction, d.state_token);
      if (!target.textContent) target.textContent = (d && d.text) ? d.text : '';
      appendMessage(msgId, target.textContent || '');
    } finally { offToken(); offDone(); offErr(); }
  });
  const offErr = on('sse:error', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    try {
      target.textContent += ' [error] ' + (d && d.message ? d.message : '');
      appendMessage(msgId, target.textContent || '');
    } finally { offToken(); offDone(); offErr(); }
  });

  // Show Stop while active
  if (stopBtn) stopBtn.style.display = 'inline-block';

  emit('controller:stream:start', { text, messageId });
  // On immediate failure, controller will fall back by emitting non-stream send
  // Also provide local fallback after a microtask in case no event arrives
  Promise.resolve().then(() => {}).catch(() => {}).finally(() => {});
}

// Global bus bindings for non-stream responses and actions
on('chat:response', (ev) => {
  const d = ev && ev.detail; if (!d) return;
  const textOut = d.text || '';
  if (d.updates) setPanelUpdates(d.updates);
  addAndRenderMessage('assistant', textOut || '[empty]');
  if (d.handled && d.command === 'clear_chat') {
    clearMessages();
    setState({ updates: [] });
  }
});

on('action:needs', (ev) => {
  const d = ev && ev.detail; if (!d) return;
  if (d.updates) setPanelUpdates(d.updates);
  if (d.needs && d.stateToken) renderInteraction(d.needs, d.stateToken);
  if (d.text) addAndRenderMessage('assistant', d.text);
});

on('action:done', (ev) => {
  const d = ev && ev.detail; if (!d) return;
  if (d.updates) setPanelUpdates(d.updates);
  if (d.text) addAndRenderMessage('assistant', d.text);
  if (d.payload && d.payload.cleared === true) {
    clearMessages();
    setState({ updates: [] });
  }
});

on('action:error', (ev) => {
  const d = ev && ev.detail; const msg = d && d.message ? d.message : 'Action error';
  pushPanelStatus('error', 'Error: ' + msg);
});

on('upload:error', (ev) => {
  const d = ev && ev.detail; const msg = d && d.message ? d.message : 'Upload error';
  pushPanelStatus('error', 'Attach error: ' + msg);
});


sendBtn.addEventListener('click', () => {
  const text = (msg.value || '').trim();
  if (!text) return;
  addAndRenderMessage('user', text);
  msg.value = '';
  if (getState().stream) sendStream(text); else sendNonStream(text);
});

msg.addEventListener('keydown', (e) => { const k = e.key || e.keyCode; if ((k === 'Enter' || k === 13) && !e.shiftKey) { e.preventDefault(); sendBtn.click(); }});

// Store subscriptions: keep status and stream toggle in sync with state
subscribe((state, patch) => {
  if ('status' in (patch || {})) {
    statusEl.textContent = state.status || '';
  }
  if ('stream' in (patch || {})) {
    if (streamEl) streamEl.checked = !!state.stream;
  }
  if ('messages' in (patch || {})) {
    if (!state.messages || state.messages.length === 0) {
      clearMessagesDOM();
    } else {
      for (const m of state.messages) renderMessageNode(m);
    }
  }
  if ('updates' in (patch || {})) {
    try {
      const ups = state.updates || [];
      if (!ups.length) { clearPanel(); }
      else { renderUpdates(ups); }
    } catch {}
  }
});

// Reflect UI toggle back into the store
if (streamEl) {
  streamEl.addEventListener('change', () => setState({ stream: !!streamEl.checked }));
}

refreshStatus();

// Jump to latest control
if (jumpBtn) {
  jumpBtn.addEventListener('click', () => { scrollToBottom(log); updateJumpVisibility(); });
}
if (log) {
  log.addEventListener('scroll', () => updateJumpVisibility());
  // Initial state
  updateJumpVisibility();
}

// Stop button wiring: visible only during active stream
if (stopBtn) {
  stopBtn.addEventListener('click', () => emit('controller:stream:stop'));
  const hide = () => { stopBtn.style.display = 'none'; };
  on('sse:done', hide);
  on('sse:error', hide);
  on('stream:stopped', hide);
}

// ---- Panel helpers ----
function clearPanel() { panel.style.display = 'none'; panel.innerHTML = ''; }
function renderStatus(message, level='info') {
  const d = document.createElement('div'); d.className = 'status-line status-' + (level === 'warn' ? 'warn' : level === 'error' ? 'error' : 'info'); d.textContent = message; panel.appendChild(d); panel.style.display = 'block';
}
function renderUpdates(updates) {
  // Clear previous content
  clearPanel();
  if (!updates || !updates.length) return;
  // Header with Close control
  const hdr = document.createElement('div');
  hdr.style.display='flex'; hdr.style.justifyContent='space-between'; hdr.style.alignItems='center'; hdr.style.marginBottom='.5rem';
  const title = document.createElement('div'); title.textContent = 'Updates'; title.style.fontWeight='600';
  const close = document.createElement('button'); close.textContent='×'; close.title='Clear';
  close.onclick = () => { clearPanel(); setState({ updates: [] }); };
  hdr.appendChild(title); hdr.appendChild(close); panel.appendChild(hdr);
  for (const u of updates) {
    if (u && u.message && (u.type === 'status' || u.type === 'warning' || u.type === 'error')) {
      renderStatus(u.message, u.type === 'warning' ? 'warn' : (u.type === 'error' ? 'error' : 'info'));
    }
  }
  panel.style.display = 'block';
}

// Store-backed panel updates
function setPanelUpdates(updates) {
  setState({ updates: Array.isArray(updates) ? updates : [] });
}
function pushPanelStatus(level, message) {
  const st = getState();
  const next = (st.updates || []).concat([{ type: level === 'error' ? 'error' : (level === 'warn' ? 'warning' : 'status'), message: String(message || '') }]);
  setState({ updates: next });
}

function renderInteraction(needs, stateToken) {
  clearPanel();
  panel.style.display = 'block';
  const container = document.createElement('div');
  const hdr = document.createElement('div'); hdr.style.display='flex'; hdr.style.justifyContent='space-between'; hdr.style.alignItems='center'; hdr.style.marginBottom='.5rem';
  const title = document.createElement('div'); title.textContent = 'Interaction'; title.style.fontWeight='600';
  const close = document.createElement('button'); close.textContent = '×'; close.title = 'Close'; close.onclick = () => clearPanel();
  hdr.appendChild(title); hdr.appendChild(close); container.appendChild(hdr);
  const kind = (needs && needs.kind) || 'text';
  const spec = (needs && needs.spec) || {};
  const label = document.createElement('div'); label.textContent = spec.prompt || 'Provide input:'; container.appendChild(label);
  let input;
  if (kind === 'choice' && Array.isArray(spec.options)) {
    if (spec.multi) {
      input = document.createElement('div');
      for (const opt of spec.options) {
        const id = 'opt_' + Math.random().toString(36).slice(2);
        const wrap = document.createElement('div');
        const cb = document.createElement('input'); cb.type='checkbox'; cb.id=id; cb.value=opt;
        const lab = document.createElement('label'); lab.htmlFor=id; lab.textContent = ' ' + opt;
        wrap.appendChild(cb); wrap.appendChild(lab); input.appendChild(wrap);
      }
    } else {
      input = document.createElement('select'); input.style.minWidth='60%';
      for (const opt of spec.options) { const o=document.createElement('option'); o.value=opt; o.textContent=opt; input.appendChild(o); }
    }
  } else if (kind === 'bool') {
    input = document.createElement('input'); input.type='checkbox'; input.checked = !!spec.default;
  } else if (kind === 'text' && spec.multiline) {
    input = document.createElement('textarea'); input.style.width='80%'; input.style.height='120px';
  } else {
    input = document.createElement('input'); input.type='text'; input.style.minWidth='60%';
  }
  container.appendChild(input);
  const actions = document.createElement('div'); actions.style.marginTop='.5rem';
  const submit = document.createElement('button'); submit.textContent='Submit';
  const cancel = document.createElement('button'); cancel.textContent='Cancel'; cancel.style.marginLeft='.5rem';
  actions.appendChild(submit); actions.appendChild(cancel); container.appendChild(actions);
  panel.appendChild(container);

  submit.onclick = async () => {
    let response;
    if (kind === 'choice' && spec.multi) {
      response = Array.from(input.querySelectorAll('input[type="checkbox"]:checked')).map(x => x.value);
    } else if (kind === 'choice') {
      response = input.value;
    } else if (kind === 'bool') {
      response = !!input.checked;
    } else {
      response = input.value;
    }
    emit('controller:action:resume', { stateToken, response });
  };
  cancel.onclick = async () => {
    emit('controller:action:cancel', { stateToken });
    clearPanel();
  };
}

// ---- Attach (upload) ----
if (attachBtn) {
  attachBtn.addEventListener('click', () => {
    const picker = document.createElement('input'); picker.type='file'; picker.multiple=true; picker.style.display='none'; document.body.appendChild(picker);
    picker.addEventListener('change', async () => {
      if (!picker.files || !picker.files.length) { picker.remove(); return; }
      emit('controller:upload', { files: picker.files });
      picker.remove();
    });
    picker.click();
  });
}

// ---- New chat (clear chat) ----
if (newChatBtn) {
  newChatBtn.addEventListener('click', () => {
    // Client-side confirmation prompt; do not involve backend stepwise
    confirmPanel('Clear the entire chat history?').then(ok => {
      if (!ok) { pushPanelStatus('status', 'Cancelled clear chat.'); return; }
      // Clear UI state immediately
      clearMessagesDOM();
      setState({ updates: [] });
      // Send the user command so backend clears session contexts
      emit('controller:chat:send', { text: 'clear chat' });
    });
  });
}

on('upload:done', (ev) => {
  const files = (ev && ev.detail && ev.detail.files) || [];
  const paths = files.map(x => x.path);
  if (!paths.length) { pushPanelStatus('warn', 'No files uploaded'); return; }
  emit('controller:action:start', { action: 'load_file', args: { files: paths }, content: null });
});

// ---- Options panel ----
if (optionsBtn) {
  optionsBtn.addEventListener('click', async () => {
    clearPanel(); panel.style.display='block';
    const wrap = document.createElement('div');
    const hdr = document.createElement('div'); hdr.style.display='flex'; hdr.style.justifyContent='space-between'; hdr.style.alignItems='center'; hdr.style.marginBottom='.5rem';
    const title = document.createElement('div'); title.textContent = 'Set Option'; title.style.fontWeight='600';
    const close = document.createElement('button'); close.textContent='×'; close.title='Close'; close.onclick=() => clearPanel();
    hdr.appendChild(title); hdr.appendChild(close); wrap.appendChild(hdr);
    const modeRow = document.createElement('div');
    const modeLbl = document.createElement('label'); modeLbl.textContent='Mode:'; modeLbl.style.marginRight='.5rem';
    const modeSel = document.createElement('select'); ['params','tools'].forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=v; modeSel.appendChild(o); });
    modeRow.appendChild(modeLbl); modeRow.appendChild(modeSel); wrap.appendChild(modeRow);
    const nameRow = document.createElement('div'); nameRow.style.marginTop='.5rem';
    const nameLbl = document.createElement('label'); nameLbl.textContent='Option:'; nameLbl.style.marginRight='.5rem';
    const nameInp = document.createElement('input'); nameInp.type='text'; nameInp.placeholder='e.g., model or stream'; nameInp.style.minWidth='40%';
    nameRow.appendChild(nameLbl); nameRow.appendChild(nameInp); wrap.appendChild(nameRow);
    const valRow = document.createElement('div'); valRow.style.marginTop='.5rem';
    const valLbl = document.createElement('label'); valLbl.textContent='Value:'; valLbl.style.marginRight='.5rem';
    const valInp = document.createElement('input'); valInp.type='text'; valInp.placeholder='value'; valInp.style.minWidth='40%';
    valRow.appendChild(valLbl); valRow.appendChild(valInp); wrap.appendChild(valRow);
    const actions = document.createElement('div'); actions.style.marginTop='.5rem'; const applyBtn=document.createElement('button'); applyBtn.textContent='Apply'; actions.appendChild(applyBtn); wrap.appendChild(actions);
    panel.appendChild(wrap);
    applyBtn.onclick = async () => {
      emit('controller:action:start', { action: 'set_option', args: { mode: modeSel.value, option: nameInp.value, value: valInp.value }, content: null });
      // status will be updated via action:done/needs handlers
      await refreshStatus();
    };
  });
}

// ---- Theme (dark mode) via store ----
function applyTheme(theme) { document.documentElement.setAttribute('data-theme', theme); }
function initTheme() {
  const saved = localStorage.getItem('theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = saved || (prefersDark ? 'dark' : 'light');
  setState({ theme });
}
initTheme();
subscribe((state, patch) => {
  if ('theme' in (patch || {})) {
    applyTheme(state.theme || 'light');
    if (darkToggle) darkToggle.checked = (state.theme === 'dark');
    try { localStorage.setItem('theme', state.theme || 'light'); } catch {}
  }
});
if (darkToggle) {
  darkToggle.addEventListener('change', () => setState({ theme: darkToggle.checked ? 'dark' : 'light' }));
}

// ---- Drag & drop upload ----
function handleFilesDrop(fileList) {
  if (!fileList || !fileList.length) return;
  emit('controller:upload', { files: fileList });
}

let dragDepth = 0;
window.addEventListener('dragenter', (e) => { e.preventDefault(); dragDepth++; document.body.classList.add('dragover'); panel.classList.add('dragover'); });
window.addEventListener('dragover', (e) => { e.preventDefault(); });
window.addEventListener('dragleave', (e) => { e.preventDefault(); dragDepth = Math.max(0, dragDepth - 1); if (dragDepth === 0) { document.body.classList.remove('dragover'); panel.classList.remove('dragover'); } });
window.addEventListener('drop', (e) => {
  e.preventDefault(); dragDepth = 0; document.body.classList.remove('dragover'); panel.classList.remove('dragover');
  const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
  if (files && files.length) handleFilesDrop(files);
});

// Simple client-side confirmation using the panel; returns Promise<boolean>
function confirmPanel(message) {
  return new Promise(resolve => {
    clearPanel(); panel.style.display = 'block';
    const container = document.createElement('div');
    const hdr = document.createElement('div'); hdr.style.display='flex'; hdr.style.justifyContent='space-between'; hdr.style.alignItems='center'; hdr.style.marginBottom='.5rem';
    const title = document.createElement('div'); title.textContent = 'Confirm'; title.style.fontWeight='600';
    const close = document.createElement('button'); close.textContent = '×'; close.title = 'Close'; close.onclick = () => { clearPanel(); resolve(false); };
    hdr.appendChild(title); hdr.appendChild(close); container.appendChild(hdr);
    const label = document.createElement('div'); label.textContent = message || 'Are you sure?'; container.appendChild(label);
    const actions = document.createElement('div'); actions.style.marginTop='.5rem';
    const yes = document.createElement('button'); yes.textContent='Yes';
    const no = document.createElement('button'); no.textContent='Cancel'; no.style.marginLeft='.5rem';
    actions.appendChild(yes); actions.appendChild(no); container.appendChild(actions);
    panel.appendChild(container);
    yes.onclick = () => { clearPanel(); resolve(true); };
    no.onclick = () => { clearPanel(); resolve(false); };
  });
}
