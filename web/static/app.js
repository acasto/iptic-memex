(function(){
  var log = document.getElementById('log');
  var msg = document.getElementById('msg');
  var sendBtn = document.getElementById('send');
  var statusEl = document.getElementById('status');
  var panel = document.getElementById('panel');

  // Fetch status (model/provider)
  try {
    function refreshStatus(){
      fetch('/api/status')
        .then(function(r){ return r.json(); })
        .then(function(data){
          var model = data && data.model ? data.model : 'unknown';
          var provider = data && data.provider ? data.provider : '';
          statusEl.textContent = 'Ready. Model: ' + model + (provider ? (' - Provider: ' + provider) : '');
        })
        .catch(function(){ statusEl.textContent = 'Ready.'; });
    }
    window.__refreshStatus = refreshStatus;
    refreshStatus();
    // Also fetch current params to set initial UI state (e.g., stream checkbox)
    fetch('/api/params')
      .then(function(r){ return r.json(); })
      .then(function(p){
        try {
          var params = (p && p.params) ? p.params : {};
          var streamEl = document.getElementById('stream');
          if (streamEl && typeof params.stream !== 'undefined') {
            streamEl.checked = !!params.stream;
          }
        } catch (e) { /* ignore */ }
      })
      .catch(function(){ /* ignore */ });
  } catch(e) { statusEl.textContent = 'Ready.'; }

  // Enter to send, Shift+Enter for newline
  msg.addEventListener('keydown', function(e){
    var key = e.key || e.keyCode;
    if ((key === 'Enter' || key === 13) && !(e.shiftKey)) {
      e.preventDefault();
      sendBtn.click();
    }
  });

  // Attach button starts load_file action
  var attach = document.getElementById('attach');
  if (attach) {
    attach.addEventListener('click', function(){
      // Open a native file dialog, upload, then call load_file with server paths
      try {
        var picker = document.createElement('input');
        picker.type = 'file';
        picker.multiple = true;
        picker.style.display = 'none';
        document.body.appendChild(picker);
        picker.addEventListener('change', function(){
          if (!picker.files || !picker.files.length) { try { picker.remove(); } catch(e){} return; }
          var form = new FormData();
          Array.prototype.forEach.call(picker.files, function(f){ form.append('files', f); });
          fetch('/api/upload', { method: 'POST', body: form })
            .then(function(res){ return res.json().catch(function(){ return { ok: false, error: { message: 'Invalid JSON' }, _status: res.status }; }).then(function(body){ body._status = res.status; return body; }); })
            .then(function(up){
              if (!up || up.ok === false || up._status !== 200) {
                // Fallback: start interactive load_file if upload not available
                renderStatus((up && up.error && up.error.message) ? up.error.message : 'Upload not supported; please enter a path', 'warn');
                return fetch('/api/action/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'load_file', args: {}, content: null }) })
                  .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
                  .then(function(data){ if (data && data.needs_interaction && data.state_token) { renderInteractionInPanel(data.needs_interaction, data.state_token); } });
              }
              var paths = (up && up.files) ? up.files.map(function(x){ return x.path; }) : [];
              if (!paths.length) { renderStatus('No files uploaded'); return; }
              return fetch('/api/action/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'load_file', args: { files: paths }, content: null }) })
                .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
                .then(function(data){
                  if (data && data.ok && data.done) {
                    renderStatus('Loaded ' + paths.length + ' file(s).', 'info');
                  } else if (data && data.needs_interaction && data.state_token) {
                    // Fallback: show interaction if action still requests input
                    renderInteractionInPanel(data.needs_interaction, data.state_token);
                  } else {
                    renderStatus('Load result: ' + JSON.stringify(data));
                  }
                });
            })
            .catch(function(err){ renderStatus('Attach failed: ' + (err && err.message ? err.message : err), 'error'); })
            .finally(function(){ try { picker.remove(); } catch(e){} });
        });
        picker.click();
      } catch(e) { renderStatus('Attach error: ' + e, 'error'); }
    });
  }

  // Options button: compact form to set options via set_option action
  var optionsBtn = document.getElementById('options');
  if (optionsBtn) {
    optionsBtn.addEventListener('click', function(){
      try {
        clearPanel(); panel.style.display = 'block';
        var wrap = document.createElement('div'); wrap.className = 'interaction';
        var hdr = document.createElement('div'); hdr.style.display = 'flex'; hdr.style.justifyContent = 'space-between'; hdr.style.alignItems = 'center'; hdr.style.marginBottom = '.5rem';
        var title = document.createElement('div'); title.textContent = 'Set Option'; title.style.fontWeight = '600';
        var close = document.createElement('button'); close.textContent = '×'; close.title = 'Close'; close.onclick = function(){ clearPanel(); };
        hdr.appendChild(title); hdr.appendChild(close); wrap.appendChild(hdr);

        var modeRow = document.createElement('div');
        var modeLbl = document.createElement('label'); modeLbl.textContent = 'Mode:'; modeLbl.style.marginRight = '.5rem';
        var modeSel = document.createElement('select');
        var opt1 = document.createElement('option'); opt1.value = 'params'; opt1.textContent = 'Params'; modeSel.appendChild(opt1);
        var opt2 = document.createElement('option'); opt2.value = 'tools'; opt2.textContent = 'Tools'; modeSel.appendChild(opt2);
        modeRow.appendChild(modeLbl); modeRow.appendChild(modeSel);
        wrap.appendChild(modeRow);

        var nameRow = document.createElement('div'); nameRow.style.marginTop = '.5rem';
        var nameLbl = document.createElement('label'); nameLbl.textContent = 'Option:'; nameLbl.style.marginRight = '.5rem';
        var nameInp = document.createElement('input'); nameInp.type = 'text'; nameInp.placeholder = 'e.g., model or stream'; nameInp.style.minWidth = '40%'; nameInp.setAttribute('list', 'option-names');
        nameRow.appendChild(nameLbl); nameRow.appendChild(nameInp);
        wrap.appendChild(nameRow);

        var valRow = document.createElement('div'); valRow.style.marginTop = '.5rem';
        var valLbl = document.createElement('label'); valLbl.textContent = 'Value:'; valLbl.style.marginRight = '.5rem';
        var valField = document.createElement('span');
        valRow.appendChild(valLbl); valRow.appendChild(valField);
        wrap.appendChild(valRow);

        // Datalist for known option names populated from /api/params
        var dataList = document.createElement('datalist'); dataList.id = 'option-names';
        wrap.appendChild(dataList);

        // Fetch current params and inject into datalist; also show current value hint
        var currentParams = {};
        fetch('/api/params').then(function(r){ return r.json(); }).then(function(p){
          try {
            var params = (p && p.params) ? p.params : {};
            currentParams = params;
            dataList.innerHTML = '';
            Object.keys(params || {}).forEach(function(k){ var opt = document.createElement('option'); opt.value = k; dataList.appendChild(opt); });
            nameInp.addEventListener('input', function(){ var k = nameInp.value; updateValueControl(k, currentParams); });
          } catch(e) { /* ignore */ }
        }).catch(function(){ /* ignore */ });

        // Models/providers cached for selects
        var cachedModels = null, cachedProviders = null;
        function fetchModels(){
          return fetch('/api/models').then(function(r){ return r.json(); }).then(function(d){ cachedModels = (d && d.models) ? d.models : []; cachedProviders = (d && d.providers) ? d.providers : []; }).catch(function(){});
        }

        // Build appropriate value control based on option name
        var currentValueEl = null;
        function updateValueControl(optionName, params){
          var p = params || {};
          var hint = (p && optionName in p) ? ('current: ' + String(p[optionName])) : '';
          // Clear field
          valField.innerHTML = '';
          currentValueEl = null;
          if (!optionName) {
            var inp = document.createElement('input'); inp.type = 'text'; inp.placeholder = 'value'; inp.style.minWidth = '40%';
            valField.appendChild(inp); currentValueEl = inp; return;
          }
          var lower = optionName.toLowerCase();
          if (lower === 'stream' || lower === 'colors' || lower === 'highlighting') {
            var sel = document.createElement('select');
            ['true','false'].forEach(function(v){ var o=document.createElement('option'); o.value=v; o.textContent=v; sel.appendChild(o); });
            valField.appendChild(sel); currentValueEl = sel; return;
          }
          if (lower === 'temperature' || lower === 'top_p') {
            var wrapCtl = document.createElement('span');
            var rng = document.createElement('input'); rng.type='range'; rng.min='0'; rng.max='1'; rng.step='0.01'; rng.value = (lower === 'temperature' ? '0.7' : '1.0');
            var out = document.createElement('input'); out.type='number'; out.min='0'; out.max='1'; out.step='0.01'; out.value=rng.value; out.style.width='5rem'; out.style.marginLeft='.5rem';
            rng.oninput = function(){ out.value = rng.value; };
            out.oninput = function(){ rng.value = out.value; };
            wrapCtl.appendChild(rng); wrapCtl.appendChild(out);
            if (hint) { var h = document.createElement('span'); h.textContent = '  ('+hint+')'; h.style.marginLeft='.5rem'; wrapCtl.appendChild(h); }
            valField.appendChild(wrapCtl); currentValueEl = out; return;
          }
          if (lower === 'max_tokens') {
            var num = document.createElement('input'); num.type='number'; num.min='1'; num.max='128000'; num.step='1'; num.placeholder = hint || 'e.g., 4096'; num.style.minWidth='10rem';
            valField.appendChild(num); currentValueEl = num; return;
          }
          if (lower === 'model' || lower === 'provider') {
            var sel2 = document.createElement('select'); sel2.style.minWidth='40%';
            var ensure = function(){ if (lower === 'model') { (cachedModels||[]).forEach(function(m){ var o=document.createElement('option'); o.value=m; o.textContent=m; sel2.appendChild(o); }); }
                                   else { (cachedProviders||[]).forEach(function(pv){ var o=document.createElement('option'); o.value=pv; o.textContent=pv; sel2.appendChild(o); }); } };
            if (cachedModels === null || cachedProviders === null) { fetchModels().then(function(){ ensure(); }); } else { ensure(); }
            if (hint) sel2.title = hint;
            valField.appendChild(sel2); currentValueEl = sel2; return;
          }
          // Default text input
          var inp2 = document.createElement('input'); inp2.type='text'; inp2.placeholder = hint || 'value'; inp2.style.minWidth='40%';
          valField.appendChild(inp2); currentValueEl = inp2;
        }

        // Initialize control
        updateValueControl('', currentParams);

        var btnRow = document.createElement('div'); btnRow.style.marginTop = '.75rem';
        var submit = document.createElement('button'); submit.textContent = 'Apply'; submit.onclick = function(){
          var mode = modeSel.value || 'params';
          var option = (nameInp.value || '').trim();
          var value = '';
          if (currentValueEl) {
            if (currentValueEl.tagName === 'SELECT') value = currentValueEl.value;
            else value = (currentValueEl.value || '').trim();
          }
          if (!option) { renderStatus('Please enter an option name', 'warn'); return; }
          var spin = showSpinner('Applying option...');
          fetch('/api/action/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'set_option', args: { mode: mode, option: option, value: value }, content: null }) })
            .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
            .then(function(data){ hideSpinner(spin); if (data && data.ok) { renderStatus('Option applied', 'info'); clearPanel(); if (window.__refreshStatus) window.__refreshStatus(); } else { renderStatus('Failed to apply option', 'error'); } })
            .catch(function(err){ hideSpinner(spin); renderStatus('Apply failed: ' + (err && err.message ? err.message : err), 'error'); });
        };
        var cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.style.marginLeft = '.5rem'; cancel.onclick = function(){ clearPanel(); };
        btnRow.appendChild(submit); btnRow.appendChild(cancel);
        wrap.appendChild(btnRow);

        panel.appendChild(wrap);
      } catch(e) { renderStatus('Options error: ' + e, 'error'); }
    });
  }

  // Drag & drop quick attach
  (function(){
    var dragDepth = 0;
    function prevent(e){ e.preventDefault(); e.stopPropagation(); }
    function showHint(){ if (!panel) return; panel.style.display='block'; panel.classList.add('dragover'); if (!panel.textContent.trim()) renderStatus('Drop files anywhere to attach', 'info'); }
    function hideHint(){ if (!panel) return; panel.classList.remove('dragover'); }
    document.addEventListener('dragenter', function(e){ prevent(e); dragDepth++; showHint(); });
    document.addEventListener('dragleave', function(e){ prevent(e); dragDepth = Math.max(0, dragDepth-1); if (dragDepth===0) hideHint(); });
    document.addEventListener('dragover', prevent);
    document.addEventListener('drop', function(e){
      prevent(e); dragDepth = 0; hideHint();
      var files = e.dataTransfer && e.dataTransfer.files; if (!files || !files.length) return;
      uploadAndLoadFiles(files);
    });
  })();

  function uploadAndLoadFiles(fileList){
    var form = new FormData();
    Array.prototype.forEach.call(fileList, function(f){ form.append('files', f); });
    var spin = showSpinner('Uploading ' + fileList.length + ' file(s)...');
    fetch('/api/upload', { method: 'POST', body: form })
      .then(function(res){ return res.json().catch(function(){ return { ok: false, error: { message: 'Invalid JSON' }, _status: res.status }; }).then(function(body){ body._status = res.status; return body; }); })
      .then(function(up){
        if (!up || up.ok === false || up._status !== 200) {
          hideSpinner(spin);
          renderStatus((up && up.error && up.error.message) ? up.error.message : 'Upload not supported; please enter a path', 'warn');
          return fetch('/api/action/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'load_file', args: {}, content: null }) })
            .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
            .then(function(data){ if (data && data.needs_interaction && data.state_token) { renderInteractionInPanel(data.needs_interaction, data.state_token); } });
        }
        var paths = (up && up.files) ? up.files.map(function(x){ return x.path; }) : [];
        if (!paths.length) { hideSpinner(spin); renderStatus('No files uploaded', 'warn'); return; }
        return fetch('/api/action/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action: 'load_file', args: { files: paths }, content: null }) })
          .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
          .then(function(data){ hideSpinner(spin); if (data && data.ok && data.done) { renderStatus('Loaded ' + paths.length + ' file(s).', 'info'); } });
      })
      .catch(function(err){ hideSpinner(spin); renderStatus('Attach failed: ' + (err && err.message ? err.message : err), 'error'); });
  }

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
          if (data && data.updates && data.updates.length) {
            renderUpdatesInPanel(data.updates);
          }
          if (data && data.handled && data.needs_interaction && data.state_token) {
            // Render prompt widget for interaction
            var ui = renderInteractionInPanel(data.needs_interaction, data.state_token);
            // Show any text alongside
            append('assistant', textOut || '[interaction requested]');
          } else if (data && data.needs_interaction && data.state_token) {
            // Generic needs_interaction from action/start
            var ui2 = renderInteractionInPanel(data.needs_interaction, data.state_token);
            append('assistant', textOut || '[interaction requested]');
          } else {
            append('assistant', textOut || '[empty]');
          }
          // Refresh status if command affected model/provider
          var cmd = data && data.command ? String(data.command) : '';
          if (cmd === 'set_model' || cmd === 'set_option') { if (window.__refreshStatus) window.__refreshStatus(); }
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
      fetch('/api/stream/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) })
        .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
        .then(function(init){
          var token = init && init.token; if (!token) throw new Error('No stream token');
          var es = new EventSource('/api/stream?token=' + encodeURIComponent(token));
          es.addEventListener('token', function(ev){
            try { var data = JSON.parse(ev.data); target.textContent += data.text; } catch (e) {}
          });
          es.addEventListener('error', function(ev){
            try { var data = JSON.parse(ev.data); target.textContent += ' [error] ' + (data.message || ''); } catch (e) { target.textContent += ' [error]'; }
            try { es.close(); } catch (e) {}
            fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) })
              .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
              .then(function(data){ if (!target.textContent.trim()) target.textContent = (data && data.text) ? data.text : ''; })
              .catch(function(){ /* ignore */ });
          });
          es.addEventListener('done', function(ev){
            try {
              var data = JSON.parse(ev.data);
              if (!target.textContent) target.textContent = data.text || '';
              if (data && data.updates && data.updates.length) { renderUpdatesInPanel(data.updates); }
              if (data && data.handled && data.needs_interaction && data.state_token) {
                renderInteractionInPanel(data.needs_interaction, data.state_token);
              } else if (data && data.needs_interaction && data.state_token) {
                renderInteractionInPanel(data.needs_interaction, data.state_token);
              }
              var cmd = data && data.command ? String(data.command) : '';
              if (cmd === 'set_model' || cmd === 'set_option') { if (window.__refreshStatus) window.__refreshStatus(); }
            } catch (e) {}
            es.close();
          });
        })
        .catch(function(err){
          fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) })
            .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
            .then(function(data){ if (!target.textContent.trim()) target.textContent = (data && data.text) ? data.text : ''; })
            .catch(function(){ /* ignore */ });
        });
    } catch(err) {
      fetch('/api/chat', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: text}) })
        .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
        .then(function(data){ if (!target.textContent.trim()) target.textContent = (data && data.text) ? data.text : ''; })
        .catch(function(){ /* ignore */ });
    }
  }
})();

// Minimal interaction renderer for needs_interaction specs
function renderInteractionInPanel(needs, token) {
  try {
    clearPanel();
    panel.style.display = 'block';
    var container = document.createElement('div');
    container.className = 'interaction';
    // Header with close button
    var hdr = document.createElement('div'); hdr.style.display = 'flex'; hdr.style.justifyContent = 'space-between'; hdr.style.alignItems = 'center'; hdr.style.marginBottom = '.5rem';
    var title = document.createElement('div'); title.textContent = 'Interaction'; title.style.fontWeight = '600';
    var close = document.createElement('button'); close.textContent = '×'; close.title = 'Close'; close.onclick = function(){ clearPanel(); };
    hdr.appendChild(title); hdr.appendChild(close); container.appendChild(hdr);
    var kind = needs && needs.kind ? needs.kind : 'text';
    var spec = needs && needs.spec ? needs.spec : {};

    var label = document.createElement('div');
    label.textContent = spec.prompt || 'Provide input:';
    container.appendChild(label);

    var input = null;
    var multi = !!(spec && (spec.multi || spec.multiline));
    // Choose input control based on kind and spec
    if (kind === 'text' && spec && spec.multiline) {
      input = document.createElement('textarea');
      input.style.width = '80%';
      input.style.height = '120px';
    } else if (kind === 'choice' && Array.isArray(spec.options)) {
      // Single or multi-select
      if (spec.multi) {
        // Render checkboxes
        input = document.createElement('div');
        spec.options.forEach(function(opt){
          var id = 'opt_' + Math.random().toString(36).slice(2);
          var wrap = document.createElement('div');
          var cb = document.createElement('input'); cb.type = 'checkbox'; cb.id = id; cb.value = opt;
          var lab = document.createElement('label'); lab.setAttribute('for', id); lab.textContent = ' ' + opt;
          wrap.appendChild(cb); wrap.appendChild(lab);
          input.appendChild(wrap);
        });
      } else {
        // Render a select dropdown
        var sel = document.createElement('select');
        sel.style.minWidth = '60%';
        spec.options.forEach(function(opt){ var o = document.createElement('option'); o.value = opt; o.textContent = opt; sel.appendChild(o); });
        input = sel;
      }
    } else {
      input = document.createElement('input');
      input.type = 'text';
      input.style.minWidth = '60%';
    }

    if (kind === 'bool') {
      var yes = document.createElement('button'); yes.textContent = 'Yes'; yes.onclick = function(){ submitResponse(true); };
      var no = document.createElement('button'); no.textContent = 'No'; no.onclick = function(){ submitResponse(false); };
      container.appendChild(yes); container.appendChild(no);
    } else {
      if (spec.default && input && input.tagName !== 'DIV') input.value = spec.default;
      // If file prompt, render a file input and upload
      if (kind === 'files') {
        input = document.createElement('input');
        input.type = 'file';
        if (spec.accept && Array.isArray(spec.accept)) input.accept = spec.accept.join(',');
        if (spec.multiple) input.multiple = true;
      }
      container.appendChild(input);
      var submit = document.createElement('button'); submit.textContent = (kind === 'files' ? 'Upload' : 'Submit'); submit.onclick = function(){
        var value = null;
        if (kind === 'choice' && spec.multi && input && input.tagName === 'DIV') {
          var vals = [];
          Array.prototype.forEach.call(input.querySelectorAll('input[type="checkbox"]'), function(cb){ if (cb.checked) vals.push(cb.value); });
          value = vals;
        } else if (kind === 'choice' && input && input.tagName === 'SELECT') {
          value = input.value;
        } else if (kind === 'files' && input && input.files) {
          var form = new FormData();
          Array.prototype.forEach.call(input.files, function(f){ form.append('files', f); });
          fetch('/api/upload', { method: 'POST', body: form })
            .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
            .then(function(data){ var paths = (data && data.files) ? data.files.map(function(x){ return x.path; }) : []; submitResponse(paths); })
            .catch(function(err){ renderStatus('Upload failed: ' + (err && err.message ? err.message : err)); });
          return;
        } else if (input && (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT')) {
          value = input.value;
        } else {
          value = input && input.value !== undefined ? input.value : null;
        }
        submitResponse(value);
      };
      container.appendChild(submit);
      var cancel = document.createElement('button'); cancel.textContent = 'Cancel'; cancel.onclick = function(){ cancelInteraction(token); };
      container.appendChild(cancel);
    }

    panel.appendChild(container);
    panel.scrollTop = panel.scrollHeight;

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
        clearPanel();
        // Render any updates text as assistant message
        var textOut = (data && data.text) ? data.text : '';
        if (!textOut && data && data.updates && data.updates.length) {
          var lines = [];
          data.updates.forEach(function(u){ if (u && (u.message || u.text)) lines.push(u.message || u.text); });
          textOut = lines.join('\n');
        }
        if (data && data.updates && data.updates.length) {
          renderUpdatesInPanel(data.updates);
        }
        if (data && data.done && data.ok) {
          if (!textOut && data.payload) textOut = JSON.stringify(data.payload);
          append('assistant', textOut || '[done]');
        } else if (data && data.needs_interaction && data.state_token) {
          // Chain another interaction
          renderInteractionInPanel(data.needs_interaction, data.state_token);
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

// Panel helpers
function renderUpdatesInPanel(updates) {
  try {
    if (!updates || !updates.length) return;
    updates.forEach(function(ev){
      var msg = null;
      if (ev && ev.message) msg = ev.message;
      else if (ev && ev.text) msg = ev.text;
      if (msg) renderStatus(msg, ev.type);
    });
  } catch (e) { /* ignore */ }
}

function renderStatus(text, kind) {
  if (!panel) return;
  panel.style.display = 'block';
  var d = document.createElement('div');
  d.className = 'status-line';
  if (kind === 'warning' || kind === 'warn') d.classList.add('status-warn');
  else if (kind === 'error') d.classList.add('status-error');
  else d.classList.add('status-info');
  d.textContent = text;
  panel.appendChild(d);
}

function showSpinner(text) {
  if (!panel) return null;
  panel.style.display = 'block';
  var d = document.createElement('div');
  d.className = 'status-line spinner';
  d.textContent = '⏳ ' + (text || 'Working...');
  panel.appendChild(d);
  return d;
}

function hideSpinner(spinEl) {
  if (!spinEl) return;
  try { spinEl.remove(); } catch(e){}
}

function clearPanel() {
  if (!panel) return;
  panel.innerHTML = '';
  panel.style.display = 'none';
}

function cancelInteraction(token) {
  fetch('/api/action/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state_token: token }) })
    .then(function(res){ if(!res.ok) return res.text().then(function(t){ throw new Error('HTTP '+res.status+': '+t); }); return res.json(); })
    .then(function(){ clearPanel(); })
    .catch(function(err){ renderStatus('Cancel failed: ' + (err && err.message ? err.message : err)); });
}
