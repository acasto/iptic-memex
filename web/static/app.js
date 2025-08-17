(function(){
  var log = document.getElementById('log');
  var msg = document.getElementById('msg');
  var sendBtn = document.getElementById('send');
  var statusEl = document.getElementById('status');

  // Fetch status (model/provider)
  try {
    fetch('/api/status')
      .then(function(r){ return r.json(); })
      .then(function(data){
        var model = data && data.model ? data.model : 'unknown';
        var provider = data && data.provider ? data.provider : '';
        statusEl.textContent = 'Ready. Model: ' + model + (provider ? (' - Provider: ' + provider) : '');
      })
      .catch(function(){ statusEl.textContent = 'Ready.'; });
  } catch(e) { statusEl.textContent = 'Ready.'; }

  // Enter to send, Shift+Enter for newline
  msg.addEventListener('keydown', function(e){
    var key = e.key || e.keyCode;
    if ((key === 'Enter' || key === 13) && !(e.shiftKey)) {
      e.preventDefault();
      sendBtn.click();
    }
  });

  sendBtn.addEventListener('click', function(){
    var text = (msg.value || '').trim();
    if (!text) return;
    append('user', text);
    msg.value = '';
    if (document.getElementById('stream').checked) {
      startStream(text);
    } else {
      try {
        fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text })
        })
        .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
        .then(function(data){ append('assistant', (data && data.text) ? data.text : '[empty]'); })
        .catch(function(err){ append('error', String(err && err.message ? err.message : err)); });
      } catch(err) {
        append('error', String(err));
      }
    }
  });

  function append(role, text) {
    var d = document.createElement('div');
    d.className = 'msg';
    var r = document.createElement('span');
    r.className = 'role';
    r.textContent = role + ':';
    var c = document.createElement('span');
    c.textContent = ' ' + text;
    c.className = 'content';
    d.appendChild(r); d.appendChild(c);
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return c;
  }

  function startStream(text) {
    var target = append('assistant', '');
    try {
      var es = new EventSource('/api/stream?message=' + encodeURIComponent(text));
      es.addEventListener('token', function(ev){
        try { var data = JSON.parse(ev.data); target.textContent += data.text; } catch (e) {}
      });
      es.addEventListener('error', function(ev){
        try { var data = JSON.parse(ev.data); target.textContent += ' [error] ' + (data.message || ''); } catch (e) { target.textContent += ' [error]'; }
        try { es.close(); } catch (e) {}
        // Fallback to non-stream single-shot
        fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) })
          .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
          .then(function(data){ if (!target.textContent.trim()) target.textContent = (data && data.text) ? data.text : ''; })
          .catch(function(){ /* ignore */ });
      });
      es.addEventListener('done', function(ev){
        try { var data = JSON.parse(ev.data); if (!target.textContent) target.textContent = data.text || ''; } catch (e) {}
        es.close();
      });
    } catch(err) {
      // Fallback immediately
      fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) })
        .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
        .then(function(data){ if (!target.textContent.trim()) target.textContent = (data && data.text) ? data.text : ''; })
        .catch(function(){ /* ignore */ });
    }
  }
})();

