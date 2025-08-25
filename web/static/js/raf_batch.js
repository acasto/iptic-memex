// requestAnimationFrame-batched text appender to smooth streaming updates
export class RafTextAppender {
  constructor(el) {
    this.el = el;
    this._buf = '';
    this._scheduled = false;
  }

  append(text) {
    if (text == null) return;
    this._buf += String(text);
    if (!this._scheduled) {
      this._scheduled = true;
      requestAnimationFrame(() => {
        try {
          if (this._buf) {
            this.el.textContent += this._buf;
          }
        } finally {
          this._buf = '';
          this._scheduled = false;
        }
      });
    }
  }
}

