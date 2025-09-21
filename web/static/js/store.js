// Minimal shared state store with subscribe/notify and bus bridge
import { emit } from './bus.js';

// Fallback UUID generator for non-secure contexts
function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Fallback for non-secure contexts
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    const r = Math.random() * 16 | 0;
    const v = c == 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

let state = {
  messages: [], // [{id, role, text}]
  stream: false,
  pendingInteraction: null, // {kind, spec, stateToken} | null
  updates: [],
  status: '',
};

const listeners = new Set();

export function getState() {
  return state;
}

export function setState(patch) {
  state = { ...state, ...(patch || {}) };
  // Notify direct subscribers
  for (const fn of Array.from(listeners)) {
    try { fn(state, patch); } catch {}
  }
  // Also emit on the bus for any loosely-coupled listeners
  emit('store:change', { state, patch });
}

export function subscribe(fn) {
  if (!fn) return () => {};
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Convenience helpers for messages
export function addMessage(msg) {
  const m = {
    id: msg.id || generateUUID(),
    role: msg.role,
    text: msg.text || '',
    title: msg.title || '',
    anchorId: msg.anchorId || null,
    scopeId: msg.scopeId || null,
    toolCallId: msg.toolCallId || null,
    origin: msg.origin || null,
  };
  state = { ...state, messages: [...state.messages, m] };
  emit('store:change', { state, patch: { messages: state.messages } });
  return m.id;
}

export function appendMessage(id, chunk) {
  const idx = state.messages.findIndex(m => m.id === id);
  if (idx >= 0) {
    const m = { ...state.messages[idx], text: (state.messages[idx].text || '') + String(chunk || '') };
    const n = state.messages.slice(); n[idx] = m;
    state = { ...state, messages: n };
    emit('store:change', { state, patch: { messages: n } });
  }
}

export function updateMessage(id, patch) {
  if (!patch) return;
  const idx = state.messages.findIndex(m => m.id === id);
  if (idx < 0) return;
  const next = { ...state.messages[idx], ...patch };
  const messages = state.messages.slice();
  messages[idx] = next;
  state = { ...state, messages };
  emit('store:change', { state, patch: { messages } });
}

export function clearMessages() {
  state = { ...state, messages: [] };
  emit('store:change', { state, patch: { messages: state.messages } });
}
