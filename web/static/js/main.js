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
const panelOverlay = document.getElementById('panelOverlay');
const streamEl = document.getElementById('stream');
const newChatBtn = document.getElementById('newchat');
const attachBtn = document.getElementById('attach');
const optionsBtn = document.getElementById('options');
const darkToggle = document.getElementById('darkmode');
const stopBtn = document.getElementById('stop');

// Auto-expanding textarea
function updateTextareaHeight() {
  if (!msg) return;
  msg.style.height = 'auto';
  const scrollHeight = msg.scrollHeight;
  const maxHeight = 120; // matches CSS max-height
  msg.style.height = Math.min(scrollHeight, maxHeight) + 'px';
}

msg?.addEventListener('input', updateTextareaHeight);

// Store-backed message rendering with modern chat bubbles
const _nodes = new Map(); // id -> content span
let _typingIndicator = null;

function isAtBottom(el) { return (el.scrollHeight - el.scrollTop - el.clientHeight) < 8; }
function scrollToBottom(el) { try { el.scrollTop = el.scrollHeight; } catch {} }
function updateJumpVisibility() { if (!jumpBtn) return; jumpBtn.style.display = isAtBottom(log) ? 'none' : 'inline-block'; }

function showTypingIndicator() {
  if (_typingIndicator) return;
  
  _typingIndicator = document.createElement('div');
  _typingIndicator.className = 'typing-indicator';
  _typingIndicator.innerHTML = `
    <div class="avatar">AI</div>
    <div class="typing-dots">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>
  `;
  
  log.appendChild(_typingIndicator);
  const auto = isAtBottom(log);
  if (auto) scrollToBottom(log);
  else updateJumpVisibility();
}

function hideTypingIndicator() {
  if (_typingIndicator) {
    _typingIndicator.remove();
    _typingIndicator = null;
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => {
      showToast('Copied to clipboard');
    });
  } else {
    // Fallback for older browsers
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showToast('Copied to clipboard');
  }
}

function showToast(message) {
  // Simple toast notification
  const toast = document.createElement('div');
  toast.textContent = message;
  toast.style.cssText = `
    position: fixed;
    top: 2rem;
    right: 2rem;
    background: var(--panel-bg);
    color: var(--fg);
    padding: 0.75rem 1rem;
    border-radius: 6px;
    box-shadow: var(--shadow-md);
    z-index: 1000;
    animation: fadeIn 0.3s ease;
  `;
  document.body.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'fadeOut 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, 2000);
}

function clearMessagesDOM() {
  const kids = Array.from(log.childNodes);
  for (const k of kids) {
    if (k.nodeType === 1 && (k.id === 'jumpLatest' || k.classList?.contains('typing-indicator'))) continue;
    log.removeChild(k);
  }
  _nodes.clear();
  updateJumpVisibility();
}

function renderMessageNode(msg) {
  const auto = isAtBottom(log);
  const hadAny = _nodes.size > 0;
  let contentSpan = _nodes.get(msg.id);
  
  if (contentSpan) {
    contentSpan.textContent = (msg.text || '');
    if (auto) scrollToBottom(log); 
    else updateJumpVisibility();
    return contentSpan;
  }

  // Create new message bubble
  const messageEl = document.createElement('div');
  messageEl.className = `msg ${msg.role}`;
  
  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = msg.role === 'user' ? 'U' : 'AI';
  
  const content = document.createElement('div');
  content.className = 'content';
  content.textContent = (msg.text || '');
  
  // Add message actions
  const actions = document.createElement('div');
  actions.className = 'message-actions';
  
  const copyBtn = document.createElement('button');
  copyBtn.className = 'action-btn';
  copyBtn.innerHTML = '📋';
  copyBtn.title = 'Copy message';
  copyBtn.onclick = (e) => {
    e.stopPropagation();
    copyToClipboard(content.textContent);
  };
  
  actions.appendChild(copyBtn);
  content.appendChild(actions);
  
  messageEl.appendChild(avatar);
  messageEl.appendChild(content);
  
  // Insert before typing indicator if it exists, otherwise at end
  if (_typingIndicator) {
    log.insertBefore(messageEl, _typingIndicator);
  } else {
    log.appendChild(messageEl);
  }
  
  if (auto && hadAny) scrollToBottom(log); 
  else updateJumpVisibility();
  
  _nodes.set(msg.id, content);
  return content;
}

function addAndRenderMessage(role, text) {
  const id = addMessage({ role, text });
  const st = getState(); 
  const msg = st.messages.find(m => m.id === id);
  if (msg) renderMessageNode(msg);
  return id;
}

async function refreshStatus() {
  try {
    const d = await apiStatus();
    const model = d && d.model ? d.model : 'unknown';
    const provider = d && d.provider ? d.provider : '';
    const text = model + (provider ? ` (${provider})` : '');
    setState({ status: text });
  } catch {
    setState({ status: 'Ready' });
  }
  try {
    const p = await apiParams();
    if (p && p.params && 'stream' in p.params) setState({ stream: !!p.params.stream });
  } catch {}
}

function sendNonStream(text) { 
  emit('controller:chat:send', { text }); 
}

function sendStream(text) {
  // Show typing indicator
  showTypingIndicator();
  
  // Create assistant message and a rAF-batched appender
  const msgId = addAndRenderMessage('assistant', '');
  const target = _nodes.get(msgId);
  const appender = new RafTextAppender(target);
  const messageId = Math.random().toString(36).slice(2);

  // Wire bus listeners scoped by messageId
  const offToken = on('sse:token', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    hideTypingIndicator(); // Hide typing indicator on first token
    const auto = isAtBottom(log);
    appender.append(d && d.text ? d.text : '');
    if (auto) requestAnimationFrame(() => { scrollToBottom(log); updateJumpVisibility(); });
    else updateJumpVisibility();
  });
  
  const offDone = on('sse:done', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    try {
      hideTypingIndicator();
      if (d && d.updates) setPanelUpdates(d.updates);
      if (d && d.needs_interaction && d.state_token) renderInteraction(d.needs_interaction, d.state_token);
      if (!target.textContent) target.textContent = (d && d.text) ? d.text : '';
      appendMessage(msgId, target.textContent || '');
    } finally { offToken(); offDone(); offErr(); }
  });
  
  const offErr = on('sse:error', (ev) => {
    const d = ev && ev.detail; if (!d || d.messageId !== messageId) return;
    try {
      hideTypingIndicator();
      target.textContent += ' [error] ' + (d && d.message ? d.message : '');
      appendMessage(msgId, target.textContent || '');
    } finally { offToken(); offDone(); offErr(); }
  });

  // Show Stop while active
  if (stopBtn) stopBtn.style.display = 'inline-block';

  emit('controller:stream:start', { text, messageId });
}

// Global bus bindings for non-stream responses and actions
on('chat:response', (ev) => {
  const d = ev && ev.detail; if (!d) return;
  hideTypingIndicator();
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
  hideTypingIndicator();
  if (d.updates) setPanelUpdates(d.updates);
  if (d.needs && d.stateToken) renderInteraction(d.needs, d.stateToken);
  if (d.text) addAndRenderMessage('assistant', d.text);
});

on('action:done', (ev) => {
  const d = ev && ev.detail; if (!d) return;
  hideTypingIndicator();
  if (d.updates) setPanelUpdates(d.updates);
  if (d.text) addAndRenderMessage('assistant', d.text);
  if (d.payload && d.payload.cleared === true) {
    clearMessages();
    setState({ updates: [] });
  }
});

on('action:error', (ev) => {
  const d = ev && ev.detail; 
  hideTypingIndicator();
  const msg = d && d.message ? d.message : 'Action error';
  showToast('Error: ' + msg);
});

on('upload:error', (ev) => {
  const d = ev && ev.detail; 
  const msg = d && d.message ? d.message : 'Upload error';
  showToast('Upload error: ' + msg);
});

// Send button and enter key handling
function handleSend() {
  const text = (msg.value || '').trim();
  if (!text) return;
  
  addAndRenderMessage('user', text);
  msg.value = '';
  updateTextareaHeight();
  
  if (getState().stream) sendStream(text); 
  else {
    showTypingIndicator();
    sendNonStream(text);
  }
}

sendBtn?.addEventListener('click', handleSend);

msg?.addEventListener('keydown', (e) => { 
  if ((e.key === 'Enter' || e.keyCode === 13) && !e.shiftKey) { 
    e.preventDefault(); 
    handleSend();
  }
});

// Store subscriptions: keep status and stream toggle in sync with state
subscribe((state, patch) => {
  if ('status' in (patch || {})) {
    if (statusEl) statusEl.textContent = state.status || '';
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
      if (!ups.length) { closePanel(); }
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
  updateJumpVisibility();
}

// Stop button wiring
if (stopBtn) {
  stopBtn.addEventListener('click', () => emit('controller:stream:stop'));
  const hide = () => { 
    stopBtn.style.display = 'none';
    hideTypingIndicator();
  };
  on('sse:done', hide);
  on('sse:error', hide);
  on('stream:stopped', hide);
}

// ---- Panel helpers (now modal) ----
function openPanel() {
  panel.style.display = 'block';
  panelOverlay.style.display = 'block';
  document.body.style.overflow = 'hidden';
}

function closePanel() {
  panel.style.display = 'none';
  panelOverlay.style.display = 'none';
  document.body.style.overflow = '';
  panel.innerHTML = '';
}

// Close panel when clicking overlay
panelOverlay?.addEventListener('click', closePanel);

function renderStatus(message, level='info') {
  const d = document.createElement('div'); 
  d.className = `status-line status-${level}`;
  d.textContent = message; 
  panel.appendChild(d);
}

function renderUpdates(updates) {
  closePanel();
  if (!updates || !updates.length) return;
  
  openPanel();
  
  const header = document.createElement('div');
  header.className = 'panel-header';
  header.innerHTML = `
    <h3 class="panel-title">Updates</h3>
    <button class="panel-close" onclick="document.getElementById('panelOverlay').click()">×</button>
  `;
  panel.appendChild(header);
  
  for (const u of updates) {
    if (u && u.message && (u.type === 'status' || u.type === 'warning' || u.type === 'error')) {
      renderStatus(u.message, u.type === 'warning' ? 'warn' : (u.type === 'error' ? 'error' : 'info'));
    }
  }
}

function setPanelUpdates(updates) {
  setState({ updates: Array.isArray(updates) ? updates : [] });
}

function renderInteraction(needs, stateToken) {
  closePanel();
  openPanel();
  
  const container = document.createElement('div');
  container.innerHTML = `
    <div class="panel-header">
      <h3 class="panel-title">Interaction Required</h3>
      <button class="panel-close" onclick="document.getElementById('panelOverlay').click()">×</button>
    </div>
  `;
  
  const kind = (needs && needs.kind) || 'text';
  const spec = (needs && needs.spec) || {};
  
  const formGroup = document.createElement('div');
  formGroup.className = 'form-group';
  
  const label = document.createElement('label');
  label.className = 'form-label';
  label.textContent = spec.prompt || 'Provide input:';
  formGroup.appendChild(label);
  
  let input;
  if (kind === 'choice' && Array.isArray(spec.options)) {
    if (spec.multi) {
      input = document.createElement('div');
      for (const opt of spec.options) {
        const wrapper = document.createElement('div');
        wrapper.style.marginBottom = '0.5rem';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = opt;
        checkbox.id = `opt_${Math.random().toString(36).slice(2)}`;
        const checkLabel = document.createElement('label');
        checkLabel.htmlFor = checkbox.id;
        checkLabel.textContent = ' ' + opt;
        checkLabel.style.marginLeft = '0.5rem';
        wrapper.appendChild(checkbox);
        wrapper.appendChild(checkLabel);
        input.appendChild(wrapper);
      }
    } else {
      input = document.createElement('select');
      input.className = 'form-select';
      for (const opt of spec.options) {
        const option = document.createElement('option');
        option.value = opt;
        option.textContent = opt;
        input.appendChild(option);
      }
    }
  } else if (kind === 'bool') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!spec.default;
  } else if (kind === 'text' && spec.multiline) {
    input = document.createElement('textarea');
    input.className = 'form-textarea';
    input.value = spec.default || '';
  } else {
    input = document.createElement('input');
    input.className = 'form-input';
    input.type = 'text';
    input.value = spec.default || '';
  }
  
  formGroup.appendChild(input);
  container.appendChild(formGroup);
  
  const actions = document.createElement('div');
  actions.className = 'form-actions';
  actions.innerHTML = `
    <button class="btn btn-secondary" id="cancelInteraction">Cancel</button>
    <button class="btn btn-primary" id="submitInteraction">Submit</button>
  `;
  
  container.appendChild(actions);
  panel.appendChild(container);
  
  // Focus first input
  setTimeout(() => {
    if (input.focus) input.focus();
  }, 100);
  
  document.getElementById('submitInteraction').onclick = async () => {
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
    closePanel();
  };
  
  document.getElementById('cancelInteraction').onclick = async () => {
    emit('controller:action:cancel', { stateToken });
    closePanel();
  };
}

// ---- Attach (upload) ----
if (attachBtn) {
  attachBtn.addEventListener('click', () => {
    const picker = document.createElement('input'); 
    picker.type = 'file'; 
    picker.multiple = true; 
    picker.style.display = 'none'; 
    document.body.appendChild(picker);
    
    picker.addEventListener('change', async () => {
      if (!picker.files || !picker.files.length) { 
        picker.remove(); 
        return; 
      }
      emit('controller:upload', { files: picker.files });
      picker.remove();
    });
    picker.click();
  });
}

// ---- New chat (clear chat) ----
if (newChatBtn) {
  newChatBtn.addEventListener('click', () => {
    confirmPanel('Clear the entire chat history?').then(ok => {
      if (!ok) { 
        showToast('Cancelled clear chat'); 
        return; 
      }
      clearMessagesDOM();
      setState({ updates: [] });
      emit('controller:chat:send', { text: 'clear chat' });
    });
  });
}

on('upload:done', (ev) => {
  const files = (ev && ev.detail && ev.detail.files) || [];
  const paths = files.map(x => x.path);
  if (!paths.length) { 
    showToast('No files uploaded');
    return; 
  }
  showToast(`Uploaded ${paths.length} file(s)`);
  emit('controller:action:start', { action: 'load_file', args: { files: paths }, content: null });
});

// ---- Options panel ----
if (optionsBtn) {
  optionsBtn.addEventListener('click', async () => {
    closePanel();
    openPanel();
    
    const container = document.createElement('div');
    container.innerHTML = `
      <div class="panel-header">
        <h3 class="panel-title">Options</h3>
        <button class="panel-close" onclick="document.getElementById('panelOverlay').click()">×</button>
      </div>
      
      <div class="form-group">
        <label class="form-label">Mode:</label>
        <select class="form-select" id="optionMode">
          <option value="params">Parameters</option>
          <option value="tools">Tools</option>
        </select>
      </div>
      
      <div class="form-group">
        <label class="form-label">Option:</label>
        <input class="form-input" id="optionName" type="text" placeholder="e.g., model or stream" />
      </div>
      
      <div class="form-group">
        <label class="form-label">Value:</label>
        <input class="form-input" id="optionValue" type="text" placeholder="value" />
      </div>
      
      <div class="form-actions">
        <button class="btn btn-secondary" onclick="document.getElementById('panelOverlay').click()">Cancel</button>
        <button class="btn btn-primary" id="applyOption">Apply</button>
      </div>
    `;
    
    panel.appendChild(container);
    
    document.getElementById('applyOption').onclick = async () => {
      const mode = document.getElementById('optionMode').value;
      const option = document.getElementById('optionName').value;
      const value = document.getElementById('optionValue').value;
      
      if (!option) {
        showToast('Please enter an option name');
        return;
      }
      
      emit('controller:action:start', { 
        action: 'set_option', 
        args: { mode, option, value }, 
        content: null 
      });
      closePanel();
      showToast('Option updated');
      await refreshStatus();
    };
    
    // Focus first input
    setTimeout(() => document.getElementById('optionName').focus(), 100);
  });
}

// ---- Theme (dark mode) via store ----
function applyTheme(theme) { 
  document.documentElement.setAttribute('data-theme', theme); 
}

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
window.addEventListener('dragenter', (e) => { 
  e.preventDefault(); 
  dragDepth++; 
  document.body.classList.add('dragover'); 
});

window.addEventListener('dragover', (e) => { e.preventDefault(); });

window.addEventListener('dragleave', (e) => { 
  e.preventDefault(); 
  dragDepth = Math.max(0, dragDepth - 1); 
  if (dragDepth === 0) { 
    document.body.classList.remove('dragover'); 
  } 
});

window.addEventListener('drop', (e) => {
  e.preventDefault(); 
  dragDepth = 0; 
  document.body.classList.remove('dragover');
  const files = e.dataTransfer && e.dataTransfer.files ? e.dataTransfer.files : null;
  if (files && files.length) handleFilesDrop(files);
});

// Simple client-side confirmation using the panel
function confirmPanel(message) {
  return new Promise(resolve => {
    closePanel();
    openPanel();
    
    const container = document.createElement('div');
    container.innerHTML = `
      <div class="panel-header">
        <h3 class="panel-title">Confirm</h3>
        <button class="panel-close" onclick="document.getElementById('panelOverlay').click()">×</button>
      </div>
      
      <div class="form-group">
        <p>${message || 'Are you sure?'}</p>
      </div>
      
      <div class="form-actions">
        <button class="btn btn-secondary" id="confirmNo">Cancel</button>
        <button class="btn btn-primary" id="confirmYes">Yes</button>
      </div>
    `;
    
    panel.appendChild(container);
    
    document.getElementById('confirmYes').onclick = () => { closePanel(); resolve(true); };
    document.getElementById('confirmNo').onclick = () => { closePanel(); resolve(false); };
    
    // Handle overlay click
    const originalOverlayClick = panelOverlay.onclick;
    panelOverlay.onclick = () => { 
      panelOverlay.onclick = originalOverlayClick;
      resolve(false); 
    };
  });
}
