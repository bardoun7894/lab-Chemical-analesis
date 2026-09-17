/**
 * Chat UI — agent-powered conversational interface.
 * Handles SSE streaming, tool-step rendering, markdown, session management.
 */
(function () {
  'use strict';

  // --- DOM refs ---
  const messagesContainer = document.getElementById('chatMessages');
  const messagesInner = document.getElementById('chatMessagesInner');
  const composer = document.getElementById('chatComposer');
  const textarea = document.getElementById('chatInput');
  const sendBtn = document.getElementById('chatSendBtn');
  const emptyState = document.getElementById('chatEmptyState');
  const sidebar = document.getElementById('chatSidebar');
  const backdrop = document.getElementById('chatBackdrop');
  const toggleBtn = document.getElementById('chatToggleSidebar');
  const newChatBtn = document.getElementById('chatNewBtn');
  const sessionList = document.getElementById('chatSessionList');
  const searchInput = document.getElementById('chatSearchInput');

  let currentSessionId = window.CHAT_SESSION_ID || null;
  let isStreaming = false;
  let abortController = null;
  let streamBuffer = '';
  let activeToolChips = {};

  // --- Marked config ---
  if (window.marked) {
    marked.setOptions({
      gfm: true,
      breaks: true,
      headerIds: false,
      mangle: false,
    });
  }

  function sanitize(html) {
    if (window.DOMPurify) {
      return DOMPurify.sanitize(html, {
        ADD_ATTR: ['target'],
        ALLOW_DATA_ATTR: false,
      });
    }
    // Fallback: strip all tags
    const tmp = document.createElement('div');
    tmp.textContent = html;
    return tmp.innerHTML;
  }

  function renderMarkdown(text) {
    if (!window.marked) return sanitize(text);
    let html = marked.parse(text);
    // Wrap tables in scrollable container
    html = html.replace(/<table>/g, '<div class="table-wrap"><table>');
    html = html.replace(/<\/table>/g, '</table></div>');
    // Make internal links open in same tab
    html = html.replace(/(<a\s+href="\/[^"]*")/g, '$1');
    return sanitize(html);
  }

  // --- Auto-grow textarea ---
  function autoGrow() {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
  }

  textarea.addEventListener('input', autoGrow);
  textarea.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  // --- Sidebar toggle ---
  function toggleSidebar() {
    if (window.innerWidth < 992) {
      sidebar.classList.toggle('show');
      backdrop.classList.toggle('show');
    } else {
      sidebar.classList.toggle('collapsed');
    }
  }

  if (toggleBtn) toggleBtn.addEventListener('click', toggleSidebar);
  if (backdrop) backdrop.addEventListener('click', function () {
    sidebar.classList.remove('show');
    backdrop.classList.remove('show');
  });

  // --- Session search filter ---
  if (searchInput) {
    searchInput.addEventListener('input', function () {
      const q = this.value.toLowerCase();
      document.querySelectorAll('.chat-session-item').forEach(function (el) {
        const title = el.querySelector('.session-title').textContent.toLowerCase();
        el.style.display = title.includes(q) ? '' : 'none';
      });
    });
  }

  // --- New chat ---
  if (newChatBtn) {
    newChatBtn.addEventListener('click', function () {
      fetch(window.APP_ROOT + '/chatbot/new-session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          window.location.href = window.APP_ROOT + '/chatbot/?session_id=' + data.session_id;
        });
    });
  }

  // --- Session clicks ---
  if (sessionList) {
    sessionList.addEventListener('click', function (e) {
      const deleteBtn = e.target.closest('.btn-delete-session');
      if (deleteBtn) {
        e.stopPropagation();
        const sid = deleteBtn.dataset.sessionId;
        if (confirm(window.CHAT_LANG === 'ar' ? 'حذف هذه المحادثة؟' : 'Delete this conversation?')) {
          fetch(window.APP_ROOT + '/chatbot/delete-session/' + sid, { method: 'POST' })
            .then(function () {
              const item = deleteBtn.closest('.chat-session-item');
              if (item) item.remove();
              if (parseInt(sid) === currentSessionId) {
                window.location.href = window.APP_ROOT + '/chatbot/';
              }
            });
        }
        return;
      }
      const item = e.target.closest('.chat-session-item');
      if (item) {
        window.location.href = window.APP_ROOT + '/chatbot/?session_id=' + item.dataset.sessionId;
      }
    });
  }

  // --- Suggestion cards ---
  document.querySelectorAll('.suggestion-card').forEach(function (card) {
    card.addEventListener('click', function () {
      textarea.value = this.dataset.message;
      autoGrow();
      sendMessage();
    });
  });

  // --- Send message ---
  function sendMessage() {
    const text = textarea.value.trim();
    if (!text || isStreaming) return;

    // Hide empty state
    if (emptyState) emptyState.style.display = 'none';

    appendMessage('user', text);
    textarea.value = '';
    autoGrow();

    isStreaming = true;
    abortController = new AbortController();
    updateSendButton();

    const assistantEl = appendMessage('assistant', '', true);
    const bodyEl = assistantEl.querySelector('.md-content');
    const toolStepsEl = assistantEl.querySelector('.tool-steps');

    streamBuffer = '';
    activeToolChips = {};

    fetch(window.APP_ROOT + '/chatbot/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: currentSessionId }),
      signal: abortController.signal,
    })
      .then(function (response) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        function read() {
          return reader.read().then(function (result) {
            if (result.done) {
              finishStream(assistantEl);
              return;
            }
            buffer += decoder.decode(result.value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (let i = 0; i < lines.length; i++) {
              const line = lines[i].trim();
              if (!line.startsWith('data: ')) continue;
              try {
                const event = JSON.parse(line.substring(6));
                handleEvent(event, bodyEl, toolStepsEl, assistantEl);
              } catch (e) { /* ignore parse errors */ }
            }
            return read();
          });
        }
        return read();
      })
      .catch(function (err) {
        if (err.name === 'AbortError') {
          bodyEl.innerHTML = renderMarkdown(streamBuffer + '\n\n*[Stopped]*');
          bodyEl.classList.remove('streaming-cursor');
        } else {
          bodyEl.innerHTML = '<span style="color:var(--danger)">Connection error: ' + sanitize(err.message) + '</span>';
        }
        finishStream(assistantEl);
      });
  }

  function handleEvent(event, bodyEl, toolStepsEl, assistantEl) {
    if (event.session_id) {
      currentSessionId = event.session_id;
      // Update URL without reload
      if (window.history.replaceState) {
        window.history.replaceState(null, '', window.APP_ROOT + '/chatbot/?session_id=' + event.session_id);
      }
    }

    if (event.notice) {
      const notice = document.createElement('div');
      notice.className = 'text-muted small mb-2';
      notice.textContent = event.notice;
      toolStepsEl.appendChild(notice);
    }

    if (event.tool_call) {
      const tc = event.tool_call;
      const chip = createToolChip(tc);
      toolStepsEl.appendChild(chip);
      toolStepsEl.style.display = '';
      activeToolChips[tc.id] = chip;
      scrollToBottom();
    }

    if (event.tool_result) {
      const tr = event.tool_result;
      const chip = activeToolChips[tr.id];
      if (chip) {
        updateToolChip(chip, tr);
      }
    }

    if (event.chunk) {
      streamBuffer += event.chunk;
      bodyEl.innerHTML = renderMarkdown(streamBuffer);
      bodyEl.classList.add('streaming-cursor');
      scrollToBottom();
    }

    if (event.done) {
      bodyEl.classList.remove('streaming-cursor');
      if (streamBuffer) {
        bodyEl.innerHTML = renderMarkdown(streamBuffer);
      }
      // Collapse tool steps into summary
      collapseToolSteps(toolStepsEl, event);
      finishStream(assistantEl);
    }

    if (event.error) {
      bodyEl.innerHTML += '<div style="color:var(--danger);margin-top:8px">' + sanitize(event.error) + '</div>';
      finishStream(assistantEl);
    }
  }

  // --- Tool chips ---
  function createToolChip(tc) {
    const chip = document.createElement('span');
    chip.className = 'tool-chip';
    chip.dataset.toolId = tc.id;
    chip.innerHTML =
      '<span class="tool-spinner"></span>' +
      '<span class="tool-name">' + toolLabel(tc.name) + '</span>';

    const detail = document.createElement('div');
    detail.className = 'tool-detail';
    detail.innerHTML = '<strong>Arguments:</strong><pre>' + sanitize(JSON.stringify(tc.args, null, 2)) + '</pre>';

    const wrapper = document.createElement('div');
    wrapper.appendChild(chip);
    wrapper.appendChild(detail);

    chip.addEventListener('click', function () {
      detail.classList.toggle('open');
    });

    return wrapper;
  }

  function updateToolChip(wrapper, tr) {
    const chip = wrapper.querySelector('.tool-chip');
    if (!chip) return;

    const spinner = chip.querySelector('.tool-spinner');
    if (spinner) {
      const icon = document.createElement('span');
      icon.className = 'tool-icon ' + (tr.ok ? 'ok' : 'fail');
      icon.innerHTML = tr.ok ? '<i class="bi bi-check-lg"></i>' : '<i class="bi bi-exclamation-triangle"></i>';
      spinner.replaceWith(icon);
    }

    const nameEl = chip.querySelector('.tool-name');
    if (nameEl) nameEl.textContent = tr.summary || toolLabel(tr.name);

    if (tr.ms) {
      const ms = document.createElement('span');
      ms.className = 'tool-ms';
      ms.textContent = tr.ms + 'ms';
      chip.appendChild(ms);
    }

    // Add preview table if present
    if (tr.preview) {
      const detail = wrapper.querySelector('.tool-detail');
      if (detail) {
        detail.innerHTML += renderPreviewTable(tr.preview);
      }
    }
  }

  function renderPreviewTable(preview) {
    if (!preview || !preview.columns || !preview.rows) return '';
    let html = '<div class="tool-preview-wrap"><table class="tool-preview-table"><thead><tr>';
    preview.columns.forEach(function (c) { html += '<th>' + sanitize(String(c)) + '</th>'; });
    html += '</tr></thead><tbody>';
    preview.rows.forEach(function (row) {
      html += '<tr>';
      row.forEach(function (val) { html += '<td>' + sanitize(val == null ? '' : String(val)) + '</td>'; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    return html;
  }

  function collapseToolSteps(toolStepsEl, event) {
    if (!event || !event.tools_used || event.tools_used.length === 0) return;
    const chips = toolStepsEl.querySelectorAll('.tool-chip');
    if (chips.length <= 1) return;

    // Create summary line
    const summary = document.createElement('div');
    summary.className = 'tool-steps-summary';
    summary.innerHTML = '<i class="bi bi-tools"></i> ' +
      event.steps + ' steps &middot; ' + event.tools_used.join(', ');

    let collapsed = true;
    summary.addEventListener('click', function () {
      collapsed = !collapsed;
      Array.from(toolStepsEl.children).forEach(function (child) {
        if (child !== summary) child.style.display = collapsed ? 'none' : '';
      });
    });

    // Hide individual chips, show summary
    Array.from(toolStepsEl.children).forEach(function (child) {
      child.style.display = 'none';
    });
    toolStepsEl.appendChild(summary);
  }

  function toolLabel(name) {
    var labels = {
      get_context: 'Getting context…',
      describe_schema: 'Checking schema…',
      run_sql: 'Running query…',
      get_ladle: 'Looking up ladle…',
      get_pipe: 'Looking up pipe…',
      get_order: 'Looking up order…',
      search_pipes: 'Searching pipes…',
      production_summary: 'Production summary…',
      defect_analysis: 'Analyzing defects…',
      quality_stats: 'Quality statistics…',
      find_page: 'Finding page…',
    };
    return labels[name] || name + '…';
  }

  // --- Message rendering ---
  // Copy the message text. navigator.clipboard only exists on secure
  // origins (https / localhost); the app is served over plain http, so the
  // old handler threw before copying anything. Fall back to a hidden
  // textarea + execCommand, which works on http.
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).catch(function () { return copyTextLegacy(text); });
    }
    return copyTextLegacy(text);
  }

  function copyTextLegacy(text) {
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.top = '0';
      ta.style.left = '-9999px';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('copy failed'));
    });
  }

  function wireCopyButton(el) {
    var btn = el.querySelector('.btn-copy');
    if (!btn || btn.dataset.wired) return;
    btn.dataset.wired = '1';
    btn.addEventListener('click', function () {
      var body = el.querySelector('.md-content');
      var text = body ? body.innerText || body.textContent : '';
      copyText(text).then(function () {
        btn.innerHTML = '<i class="bi bi-check"></i>';
      }, function () {
        btn.innerHTML = '<i class="bi bi-x"></i>';
      });
      setTimeout(function () { btn.innerHTML = '<i class="bi bi-clipboard"></i>'; }, 1500);
    });
  }

  function appendMessage(role, content, streaming) {
    if (emptyState) emptyState.style.display = 'none';

    var el = document.createElement('div');
    el.className = 'chat-message ' + role;

    var isRTL = document.documentElement.dir === 'rtl';
    var userLabel = window.CHAT_USER_NAME || (isRTL ? 'أنت' : 'You');
    var assistantLabel = isRTL ? 'المساعد' : 'Assistant';
    var avatarText = role === 'user' ? userLabel.charAt(0).toUpperCase() : 'AI';

    var header = '<div class="chat-message-header">' +
      '<span class="chat-avatar ' + role + '-avatar">' + sanitize(avatarText) + '</span>' +
      '<span class="chat-message-label">' + sanitize(role === 'user' ? userLabel : assistantLabel) + '</span>' +
      '</div>';

    var toolSteps = '<div class="tool-steps" style="display:none"></div>';

    var bodyContent = content ? renderMarkdown(content) : '';
    var body = '<div class="chat-message-body">' +
      toolSteps +
      '<div class="md-content' + (streaming ? ' streaming-cursor' : '') + '">' + bodyContent + '</div>' +
      '<div class="message-actions">' +
      '<button class="btn-copy" title="Copy"><i class="bi bi-clipboard"></i></button>' +
      '</div></div>';

    el.innerHTML = header + body;

    wireCopyButton(el);

    messagesInner.appendChild(el);
    scrollToBottom();
    return el;
  }

  function renderStoredToolCalls(toolCalls, toolStepsEl) {
    if (!toolCalls || !toolCalls.length) return;
    toolStepsEl.style.display = '';

    toolCalls.forEach(function (item) {
      if (item.name && item.summary !== undefined) {
        // This is a tool_result
        var chip = document.createElement('span');
        chip.className = 'tool-chip';
        var iconClass = item.ok !== false ? 'ok' : 'fail';
        var iconHtml = item.ok !== false ? '<i class="bi bi-check-lg"></i>' : '<i class="bi bi-exclamation-triangle"></i>';
        chip.innerHTML =
          '<span class="tool-icon ' + iconClass + '">' + iconHtml + '</span>' +
          '<span class="tool-name">' + sanitize(item.summary || item.name) + '</span>';
        if (item.ms) {
          chip.innerHTML += '<span class="tool-ms">' + item.ms + 'ms</span>';
        }
        toolStepsEl.appendChild(chip);
      }
    });

    // Collapse if more than 1
    var chips = toolStepsEl.querySelectorAll('.tool-chip');
    if (chips.length > 1) {
      var toolNames = [];
      toolCalls.forEach(function (item) {
        if (item.name && item.summary !== undefined && toolNames.indexOf(item.name) === -1) {
          toolNames.push(item.name);
        }
      });

      var summary = document.createElement('div');
      summary.className = 'tool-steps-summary';
      summary.innerHTML = '<i class="bi bi-tools"></i> ' + chips.length + ' steps &middot; ' + toolNames.join(', ');

      var collapsed = true;
      summary.addEventListener('click', function () {
        collapsed = !collapsed;
        chips.forEach(function (c) { c.style.display = collapsed ? 'none' : ''; });
      });

      chips.forEach(function (c) { c.style.display = 'none'; });
      toolStepsEl.appendChild(summary);
    }
  }

  // --- Stream lifecycle ---
  function finishStream(assistantEl) {
    isStreaming = false;
    abortController = null;
    streamBuffer = '';
    activeToolChips = {};
    updateSendButton();
    scrollToBottom();
  }

  function updateSendButton() {
    if (isStreaming) {
      sendBtn.classList.add('stop');
      sendBtn.innerHTML = '<i class="bi bi-stop-fill"></i>';
      sendBtn.title = window.CHAT_LANG === 'ar' ? 'إيقاف' : 'Stop';
      sendBtn.onclick = function () {
        if (abortController) abortController.abort();
      };
    } else {
      sendBtn.classList.remove('stop');
      sendBtn.innerHTML = '<i class="bi bi-send"></i>';
      sendBtn.title = window.CHAT_LANG === 'ar' ? 'إرسال' : 'Send';
      sendBtn.onclick = null;
    }
  }

  sendBtn.addEventListener('click', function () {
    if (isStreaming) {
      if (abortController) abortController.abort();
    } else {
      sendMessage();
    }
  });

  function scrollToBottom() {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }

  // --- Initial render of stored messages: copy buttons + tool_calls ---
  // Server-rendered messages ship a .btn-copy with no handler; only
  // appendMessage() used to wire one, so copy was dead on reload.
  document.querySelectorAll('.chat-message').forEach(wireCopyButton);
  document.querySelectorAll('.chat-message.assistant').forEach(function (el) {
    // Stored replies are emitted as raw Markdown text; render them like
    // streamed ones so tables/links look the same after a reload.
    var stored = el.querySelector('.md-content');
    if (stored && !stored.dataset.rendered) {
      stored.dataset.rendered = '1';
      stored.innerHTML = renderMarkdown(stored.textContent);
    }
    var toolCallsJson = el.dataset.toolCalls;
    if (toolCallsJson) {
      try {
        var toolCalls = JSON.parse(toolCallsJson);
        var toolStepsEl = el.querySelector('.tool-steps');
        if (toolStepsEl) renderStoredToolCalls(toolCalls, toolStepsEl);
      } catch (e) { /* ignore */ }
    }
  });

  // --- Size the shell to the space actually left by the page chrome ---
  // Only .chat-messages should ever scroll. If the shell is taller than the
  // gap between the header and the footer, the document scrolls instead and
  // takes the topbar — and the sidebar toggle inside it — out of reach.
  function sizeShell() {
    var shell = document.querySelector('.chat-shell');
    if (!shell) return;
    var footer = document.querySelector('footer');
    // Measure against the document, not the offset parent: the shell sits
    // inside a positioned wrapper, so offsetTop reads ~0 there and the whole
    // header would be unaccounted for. Setting the height below does not move
    // the shell's own top, so this cannot feed back.
    var top = shell.getBoundingClientRect().top + window.scrollY;
    var chrome = top + (footer ? footer.offsetHeight : 0);
    var height = Math.max(320, window.innerHeight - chrome);
    shell.style.setProperty('--chat-shell-height', height + 'px');
  }

  sizeShell();
  window.addEventListener('resize', sizeShell);
  // Web fonts and the sidebar's own scripts can change the header height after
  // first paint, so take one more reading once layout has settled.
  window.addEventListener('load', function () {
    sizeShell();
    scrollToBottom();
  });

  // Focus textarea on load
  if (textarea) textarea.focus();

  // Scroll to bottom on load
  scrollToBottom();
})();
