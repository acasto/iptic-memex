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
        .then(function(data){
          var textOut = (data && data.text) ? data.text : '';
          if (data && data.handled && data.needs_interaction && data.state_token) {
            // Render prompt widget for interaction
            var ui = renderInteraction(data.needs_interaction, data.state_token);
            // Show any text alongside
            append('assistant', textOut || '[interaction requested]');
          } else if (data && data.needs_interaction && data.state_token) {
            // Generic needs_interaction from action/start
            var ui2 = renderInteraction(data.needs_interaction, data.state_token);
            append('assistant', textOut || '[interaction requested]');
          } else {
            append('assistant', textOut || '[empty]');
          }
        })
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
        try {
          var data = JSON.parse(ev.data);
          if (!target.textContent) target.textContent = data.text || '';
          if (data && data.handled && data.needs_interaction && data.state_token) {
            renderInteraction(data.needs_interaction, data.state_token);
          } else if (data && data.needs_interaction && data.state_token) {
            renderInteraction(data.needs_interaction, data.state_token);
          }
        } catch (e) {}
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

// Minimal interaction renderer for needs_interaction specs
function renderInteraction(needs, token) {
  try {
    var container = document.createElement('div');
    container.className = 'interaction';
    var log = document.getElementById('log');
    var kind = needs && needs.kind ? needs.kind : 'text';
    var spec = needs && needs.spec ? needs.spec : {};

    var label = document.createElement('div');
    label.textContent = spec.prompt || 'Provide input:';
    container.appendChild(label);

    var input = document.createElement('input');
    input.type = 'text';
    input.style.minWidth = '60%';

    if (kind === 'bool') {
      var yes = document.createElement('button'); yes.textContent = 'Yes'; yes.onclick = function(){ submitResponse(true); };
      var no = document.createElement('button'); no.textContent = 'No'; no.onclick = function(){ submitResponse(false); };
      container.appendChild(yes); container.appendChild(no);
    } else {
      if (spec.default) input.value = spec.default;
      container.appendChild(input);
      var submit = document.createElement('button'); submit.textContent = 'Submit'; submit.onclick = function(){ submitResponse(input.value); };
      container.appendChild(submit);
    }

    log.appendChild(container);
    log.scrollTop = log.scrollHeight;

    function submitResponse(value) {
      // Call /api/action/resume
      fetch('/api/action/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ state_token: token, response: value })
      })
      .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
      .then(function(data){
        // Remove interaction UI
        try { container.remove(); } catch(e){}
        // Render any updates text as assistant message
        var textOut = (data && data.text) ? data.text : '';
        if (!textOut && data && data.updates && data.updates.length) {
          var lines = [];
          data.updates.forEach(function(u){ if (u && (u.message || u.text)) lines.push(u.message || u.text); });
          textOut = lines.join('\n');
        }
        if (data && data.done && data.ok) {
          if (!textOut && data.payload) textOut = JSON.stringify(data.payload);
          append('assistant', textOut || '[done]');
        } else if (data && data.needs_interaction && data.state_token) {
          // Chain another interaction
          renderInteraction(data.needs_interaction, data.state_token);
          append('assistant', textOut || '[interaction requested]');
        } else {
          append('assistant', textOut || '[empty]');
        }
      })
      .catch(function(err){ append('error', String(err && err.message ? err.message : err)); });
    }

    return container;
  } catch (e) { return null; }
}
