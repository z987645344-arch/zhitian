// 聊天主界面：流式对话、引用来源展示、聊天附件。
// 只走fast模式；后端返回"未找到可靠依据"等拒答结果时如实展示，不做任何包装。
(() => {
  if (!API.token()) {
    location.replace('./login.html');
    return;
  }

  const logInner = document.querySelector('#chatLogInner');
  const logArea = document.querySelector('#chatLog');
  const form = document.querySelector('#composer');
  const input = document.querySelector('#messageInput');
  const sendButton = document.querySelector('#sendButton');
  const attachButton = document.querySelector('#attachButton');
  const attachmentInput = document.querySelector('#attachmentInput');
  const chips = document.querySelector('#attachmentChips');
  const hint = document.querySelector('#composerHint');

  // 每个浏览器会话固定一个session_id，使后端能串起多轮上下文
  const SESSION_KEY = 'zt_web_session_id';
  let sessionId = sessionStorage.getItem(SESSION_KEY);
  if (!sessionId) {
    sessionId = `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    sessionStorage.setItem(SESSION_KEY, sessionId);
  }

  let pendingAttachments = [];
  let sending = false;

  document.querySelector('#currentUser').textContent = API.currentUsername() || '-';
  document.querySelector('#logoutButton').addEventListener('click', () => {
    API.logout();
    sessionStorage.removeItem(SESSION_KEY);
    location.replace('./login.html');
  });

  function clearEmptyState() {
    const empty = logInner.querySelector('.chat-empty');
    if (empty) empty.remove();
  }

  function scrollToBottom() {
    logArea.scrollTop = logArea.scrollHeight;
  }

  function addBubble(role, text, extraClass) {
    clearEmptyState();
    const bubble = document.createElement('div');
    bubble.className = `bubble ${role}${extraClass ? ` ${extraClass}` : ''}`;
    const label = document.createElement('div');
    label.className = 'bubble-role';
    label.textContent = role === 'user' ? '我' : '知天';
    const body = document.createElement('div');
    body.className = 'bubble-body';
    body.textContent = text;
    bubble.append(label, body);
    logInner.appendChild(bubble);
    scrollToBottom();
    return { bubble, body };
  }

  // 引用来源如实展示后端字段：文件名、doc_id前8位与相关度分数。
  // 后端没有返回引用时不显示该区块，也不编造任何来源。
  function renderCitations(bubble, citations) {
    const existing = bubble.querySelector('.citations');
    if (existing) existing.remove();
    if (!citations || !citations.length) return;
    const box = document.createElement('div');
    box.className = 'citations';
    const title = document.createElement('div');
    title.className = 'citations-title';
    title.textContent = `引用来源（${citations.length}）`;
    box.appendChild(title);
    citations.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'citation-item';
      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = item.source || '(未命名文档)';
      const meta = document.createElement('span');
      meta.className = 'meta';
      const docId = String(item.doc_id || '');
      const score = Number(item.score);
      meta.textContent = [
        docId ? `文档 ${docId.slice(0, 8)}` : '',
        Number.isFinite(score) ? `相关度 ${score.toFixed(3)}` : '',
        Number.isFinite(Number(item.chunk_index)) ? `片段 #${Number(item.chunk_index)}` : '',
      ].filter(Boolean).join(' · ');
      row.append(name, meta);
      box.appendChild(row);
    });
    bubble.appendChild(box);
    scrollToBottom();
  }

  function renderChips() {
    chips.innerHTML = '';
    pendingAttachments.forEach((item, index) => {
      const chip = document.createElement('span');
      chip.className = 'attachment-chip';
      const label = document.createElement('span');
      label.textContent = `${item.original_filename}（${item.char_count} 字）`;
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.setAttribute('aria-label', `移除 ${item.original_filename}`);
      remove.addEventListener('click', () => {
        pendingAttachments.splice(index, 1);
        renderChips();
      });
      chip.append(label, remove);
      chips.appendChild(chip);
    });
  }

  attachButton.addEventListener('click', () => attachmentInput.click());

  attachmentInput.addEventListener('change', async () => {
    const file = attachmentInput.files && attachmentInput.files[0];
    attachmentInput.value = '';
    if (!file) return;
    hint.textContent = `正在上传 ${file.name}...`;
    attachButton.disabled = true;
    try {
      const data = await API.uploadAttachment(sessionId, file);
      if (!data.success) {
        hint.textContent = `上传失败：${data.error_type || '未知原因'}`;
        return;
      }
      pendingAttachments.push(data);
      renderChips();
      hint.textContent = `已附加 ${data.original_filename}，提取 ${data.char_count} 字`;
    } catch (error) {
      hint.textContent = `上传失败：${briefError(error)}`;
    } finally {
      attachButton.disabled = false;
    }
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (sending) return;
    const text = input.value.trim();
    if (!text) return;

    sending = true;
    sendButton.disabled = true;
    attachButton.disabled = true;
    addBubble('user', text);
    input.value = '';

    const attachmentIds = pendingAttachments.map((item) => item.attachment_id);
    pendingAttachments = [];
    renderChips();

    const { bubble, body } = addBubble('assistant', '正在思考...', 'pending');
    let answer = '';

    try {
      await API.chatStream(sessionId, text, attachmentIds, {
        onChunk(chunk) {
          if (!answer) bubble.classList.remove('pending');
          answer += chunk;
          body.textContent = answer;
          scrollToBottom();
        },
        onCitations(citations) {
          renderCitations(bubble, citations);
        },
        onError(errorText) {
          bubble.classList.remove('pending');
          bubble.classList.add('failed');
          body.textContent = errorText || '服务暂时异常，请重试';
        },
        onDone() {
          bubble.classList.remove('pending');
          if (!answer) {
            // 后端未产生任何正文时如实说明，不伪造成功回答
            bubble.classList.add('failed');
            body.textContent = '本次没有返回内容，请重试。';
          }
        },
      });
    } catch (error) {
      bubble.classList.remove('pending');
      bubble.classList.add('failed');
      body.textContent = briefError(error);
    } finally {
      sending = false;
      sendButton.disabled = false;
      attachButton.disabled = false;
      input.focus();
    }
  });
})();
