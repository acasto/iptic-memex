import { emit } from './bus.js';

// Open SSE connection. Optional messageId is used for bus event correlation.
export function openEventSource(token, { onToken, onDone, onError, messageId } = {}) {
  const es = new EventSource('/api/stream?token=' + encodeURIComponent(token));
  es.addEventListener('token', ev => {
    let data = null; try { data = JSON.parse(ev.data); } catch {}
    // Callback path (back-compat)
    if (onToken) { try { onToken(data); } catch {} }
    // Bus path
    emit('sse:token', { ...(data || {}), messageId });
  });
  es.addEventListener('done', ev => {
    let data = null; try { data = JSON.parse(ev.data); } catch {}
    if (onDone) { try { onDone(data); } catch {} }
    emit('sse:done', { ...(data || {}), messageId });
    es.close();
  });
  es.addEventListener('error', ev => {
    let data = null; try { data = JSON.parse(ev.data); } catch {}
    if (onError) { try { onError(data); } catch {} }
    emit('sse:error', { ...(data || {}), messageId });
    es.close();
  });
  // Also notify start on the bus for completeness
  emit('sse:start', { messageId, token });
  return es;
}
