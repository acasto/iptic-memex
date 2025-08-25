// Tiny EventTarget-based event bus for decoupling modules
export const bus = new EventTarget();

export function on(type, handler) {
  bus.addEventListener(type, handler);
  return () => bus.removeEventListener(type, handler);
}

export function off(type, handler) {
  bus.removeEventListener(type, handler);
}

export function emit(type, detail) {
  try {
    bus.dispatchEvent(new CustomEvent(type, { detail }));
  } catch (e) {
    // Swallow to avoid cascading UI errors
    // eslint-disable-next-line no-console
    console && console.warn && console.warn('bus emit failed', e);
  }
}

