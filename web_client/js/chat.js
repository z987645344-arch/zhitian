// customer工作台：会话管理、fast/expert切换、流式对话、引用来源与聊天附件。
// 权限边界仍由后端customer令牌控制；本页面不调用任何管理端接口。
(() => {
  if (!API.token()) {
    location.replace('./login.html');
    return;
  }

  const SESSION_KEY = 'zt_web_session_id';
  const MODE_KEY = 'zt_web_chat_mode';
  const LEGACY_SESSION_KEY = 'zt_web_session_id';
  const MAX_ATTACHMENT_MB = 1;
  const MODE_COPY = {
    fast: {
      label: '快速模式',
      description: '快速模式：优先简洁作答，需要时检索企业知识库。',
      waiting: '正在快速分析并核验相关知识…',
    },
    expert: {
      label: '专家模式',
      description: '专家模式：进行更完整的规划与工具调用，等待时间通常更长。',
      waiting: '专家模式正在规划、检索并组织答案，请耐心等待…',
    },
  };

  const logInner = document.querySelector('#chatLogInner');
  const logArea = document.querySelector('#chatLog');
  const form = document.querySelector('#composer');
  const input = document.querySelector('#messageInput');
  const sendButton = document.querySelector('#sendButton');
  const attachButton = document.querySelector('#attachButton');
  const attachmentInput = document.querySelector('#attachmentInput');
  const chips = document.querySelector('#attachmentChips');
  const hint = document.querySelector('#composerHint');
  const newChatButton = document.querySelector('#newChatButton');
  const refreshSessionsButton = document.querySelector('#refreshSessionsButton');
  const sessionList = document.querySelector('#sessionList');
  const sessionStatus = document.querySelector('#sessionStatus');
  const conversationTitle = document.querySelector('#conversationTitle');
  const modeDescription = document.querySelector('#modeDescription');
  const activeModeLabel = document.querySelector('#activeModeLabel');
  const modeButtons = Array.from(document.querySelectorAll('.mode-option'));
  const sidebar = document.querySelector('#chatSidebar');
  const sidebarToggle = document.querySelector('#sidebarToggle');
  const sidebarBackdrop = document.querySelector('#sidebarBackdrop');

  let sessionId = localStorage.getItem(SESSION_KEY) || '';
  const legacySessionId = sessionStorage.getItem(LEGACY_SESSION_KEY) || '';
  if (!sessionId && legacySessionId) {
    sessionId = legacySessionId;
    localStorage.setItem(SESSION_KEY, sessionId);
  }
  sessionStorage.removeItem(LEGACY_SESSION_KEY);

  let mode = localStorage.getItem(MODE_KEY) === 'expert' ? 'expert' : 'fast';
  let sessions = [];
  let pendingAttachments = [];
  let sending = false;
  let loadingSession = false;

  document.querySelector('#currentUser').textContent = API.currentUsername() || '-';

  function createSessionId() {
    const unique = typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    return `web-${unique}`;
  }

  function ensureSessionId() {
    if (!sessionId) {
      sessionId = createSessionId();
      localStorage.setItem(SESSION_KEY, sessionId);
    }
    return sessionId;
  }

  function visibleTitle(item) {
    const custom = String(item?.display_name || '').trim();
    if (custom) return custom;
    const title = String(item?.title || '').trim();
    return title || '未命名对话';
  }

  function formatSessionTime(value) {
    if (!value) return '时间未知';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
    return new Intl.DateTimeFormat('zh-CN', {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  }

  function setConversationTitle(title) {
    conversationTitle.textContent = title || '新对话';
  }

  function openSidebar() {
    document.body.classList.add('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', 'true');
  }

  function closeSidebar() {
    document.body.classList.remove('sidebar-open');
    sidebarToggle.setAttribute('aria-expanded', 'false');
  }

  function updateModeUi() {
    modeButtons.forEach((button) => {
      const selected = button.dataset.mode === mode;
      button.classList.toggle('active', selected);
      button.setAttribute('aria-pressed', String(selected));
    });
    modeDescription.textContent = MODE_COPY[mode].description;
    activeModeLabel.textContent = MODE_COPY[mode].label;
  }

  function setMode(nextMode) {
    if (sending || loadingSession || !MODE_COPY[nextMode] || nextMode === mode) return;
    mode = nextMode;
    localStorage.setItem(MODE_KEY, mode);
    updateModeUi();
  }

  function setInteractionState() {
    const busy = sending || loadingSession;
    sendButton.disabled = busy;
    attachButton.disabled = busy;
    newChatButton.disabled = busy;
    refreshSessionsButton.disabled = busy;
    modeButtons.forEach((button) => { button.disabled = busy; });
    sessionList.querySelectorAll('button').forEach((button) => { button.disabled = busy; });
  }

  function scrollToBottom() {
    logArea.scrollTop = logArea.scrollHeight;
  }

  function showWelcome(text) {
    logInner.replaceChildren();
    const box = document.createElement('section');
    box.className = 'chat-welcome';
    const mark = document.createElement('div');
    mark.className = 'welcome-mark';
    mark.textContent = '知';
    const title = document.createElement('h2');
    title.textContent = '今天想了解什么？';
    const copy = document.createElement('p');
    copy.textContent = text || '向知识库提问，回答会标注所依据的文档来源。若没有可靠依据，助手会如实说明。';
    const tips = document.createElement('div');
    tips.className = 'welcome-tips';
    ['核验企业知识与制度', '梳理复杂问题与材料', '结合附件继续追问'].forEach((item) => {
      const tip = document.createElement('span');
      tip.textContent = item;
      tips.appendChild(tip);
    });
    box.append(mark, title, copy, tips);
    logInner.appendChild(box);
  }

  function showWorkspaceMessage(text, failed = false) {
    logInner.replaceChildren();
    const message = document.createElement('p');
    message.className = failed ? 'chat-empty error' : 'chat-empty';
    message.textContent = text;
    logInner.appendChild(message);
  }

  function addAttachmentLabels(bubble, filenames) {
    if (!Array.isArray(filenames) || filenames.length === 0) return;
    const row = document.createElement('div');
    row.className = 'message-attachments';
    filenames.forEach((filename) => {
      const item = document.createElement('span');
      item.textContent = `附件：${filename}`;
      row.appendChild(item);
    });
    bubble.appendChild(row);
  }

  function addBubble(role, text, extraClass, attachmentFilenames) {
    const bubble = document.createElement('article');
    bubble.className = `bubble ${role}${extraClass ? ` ${extraClass}` : ''}`;
    const label = document.createElement('div');
    label.className = 'bubble-role';
    label.textContent = role === 'user' ? '我' : '知天';
    const body = document.createElement('div');
    body.className = 'bubble-body';
    body.textContent = text;
    bubble.append(label, body);
    addAttachmentLabels(bubble, attachmentFilenames);
    logInner.appendChild(bubble);
    scrollToBottom();
    return { bubble, body };
  }

  function renderHistory(history) {
    logInner.replaceChildren();
    if (!history.length) {
      showWelcome();
      return;
    }
    history.forEach((item) => {
      const role = item.role === 'user' ? 'user' : 'assistant';
      addBubble(role, String(item.content || ''), '', item.attachment_filenames || []);
    });
  }

  // 引用来源如实展示后端字段：文件名、doc_id前8位与相关度分数。
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

  function renderSessions() {
    sessionList.replaceChildren();
    if (!sessions.length) {
      sessionStatus.textContent = '暂无历史会话';
      return;
    }
    sessionStatus.textContent = `共 ${sessions.length} 个会话`;
    sessions.forEach((item) => {
      const row = document.createElement('div');
      row.className = `session-item${item.session_id === sessionId ? ' active' : ''}`;

      const open = document.createElement('button');
      open.className = 'session-open';
      open.type = 'button';
      open.setAttribute('aria-label', `打开会话：${visibleTitle(item)}`);
      const title = document.createElement('strong');
      title.textContent = visibleTitle(item);
      const meta = document.createElement('span');
      meta.textContent = `${formatSessionTime(item.last_active)} · ${Number(item.message_count || 0)} 条消息`;
      open.append(title, meta);
      open.addEventListener('click', () => openSession(item.session_id));

      const remove = document.createElement('button');
      remove.className = 'session-delete';
      remove.type = 'button';
      remove.textContent = '删除';
      remove.setAttribute('aria-label', `删除会话：${visibleTitle(item)}`);
      remove.addEventListener('click', () => deleteSession(item));

      row.append(open, remove);
      sessionList.appendChild(row);
    });
    setInteractionState();
  }

  async function refreshSessions() {
    sessionStatus.textContent = '正在加载会话…';
    try {
      sessions = (await API.getSessions()).slice().sort((left, right) => {
        const leftTime = Date.parse(left.last_active || left.created_at || '') || 0;
        const rightTime = Date.parse(right.last_active || right.created_at || '') || 0;
        return rightTime - leftTime;
      });
      renderSessions();
      const current = sessions.find((item) => item.session_id === sessionId);
      if (current) setConversationTitle(visibleTitle(current));
    } catch (error) {
      sessionStatus.textContent = `会话加载失败：${briefError(error)}`;
    }
  }

  async function refreshSessionsAfterMessage(targetSessionId) {
    // fast流会先发[DONE]再保存并绑定会话，第一次读取可能早于落库。
    // 只做两次有限补查，避免常驻轮询或改变既有SSE事件顺序。
    const delays = [0, 160, 520];
    for (const delay of delays) {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      await refreshSessions();
      if (sessions.some((item) => item.session_id === targetSessionId)) return;
    }
  }

  async function openSession(nextSessionId) {
    if (!nextSessionId || sending || loadingSession) return;
    loadingSession = true;
    setInteractionState();
    closeSidebar();
    showWorkspaceMessage('正在恢复会话记录…');
    try {
      const history = await API.getHistory(nextSessionId);
      sessionId = nextSessionId;
      localStorage.setItem(SESSION_KEY, sessionId);
      pendingAttachments = [];
      renderChips();
      renderHistory(history);
      const current = sessions.find((item) => item.session_id === sessionId);
      setConversationTitle(current ? visibleTitle(current) : '历史会话');
      renderSessions();
    } catch (error) {
      if (error.status === 404) {
        sessionId = '';
        localStorage.removeItem(SESSION_KEY);
        setConversationTitle('新对话');
        showWelcome('原会话已删除或不再可访问。你可以从这里开始新的对话。');
        await refreshSessions();
      } else {
        showWorkspaceMessage(`会话恢复失败：${briefError(error)}`, true);
      }
    } finally {
      loadingSession = false;
      setInteractionState();
      input.focus();
    }
  }

  function startNewChat() {
    if (sending || loadingSession) return;
    sessionId = createSessionId();
    localStorage.setItem(SESSION_KEY, sessionId);
    pendingAttachments = [];
    renderChips();
    setConversationTitle('新对话');
    showWelcome();
    renderSessions();
    closeSidebar();
    hint.textContent = '支持 txt / md / pdf / docx 及常见 Office 格式，单个文件不超过 1MB。';
    input.focus();
  }

  async function deleteSession(item) {
    if (sending || loadingSession) return;
    const confirmed = window.confirm(`确定删除“${visibleTitle(item)}”吗？\n删除后无法恢复。`);
    if (!confirmed) return;
    loadingSession = true;
    setInteractionState();
    sessionStatus.textContent = '正在删除会话…';
    try {
      const deleted = await API.deleteSession(item.session_id);
      if (!deleted) throw new Error('服务端未确认删除');
      sessions = sessions.filter((entry) => entry.session_id !== item.session_id);
      if (sessionId === item.session_id) {
        sessionId = '';
        localStorage.removeItem(SESSION_KEY);
        pendingAttachments = [];
        renderChips();
        setConversationTitle('新对话');
        showWelcome('会话已删除。你可以开始一段新的对话。');
      }
      renderSessions();
    } catch (error) {
      sessionStatus.textContent = `删除失败：${briefError(error)}`;
    } finally {
      loadingSession = false;
      setInteractionState();
    }
  }

  function renderChips() {
    chips.replaceChildren();
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

  document.querySelector('#logoutButton').addEventListener('click', () => {
    API.logout();
    localStorage.removeItem(SESSION_KEY);
    sessionStorage.removeItem(LEGACY_SESSION_KEY);
    location.replace('./login.html');
  });
  newChatButton.addEventListener('click', startNewChat);
  refreshSessionsButton.addEventListener('click', refreshSessions);
  modeButtons.forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));
  sidebarToggle.addEventListener('click', () => {
    if (document.body.classList.contains('sidebar-open')) closeSidebar();
    else openSidebar();
  });
  sidebarBackdrop.addEventListener('click', closeSidebar);

  attachButton.addEventListener('click', () => attachmentInput.click());

  attachmentInput.addEventListener('change', async () => {
    const file = attachmentInput.files && attachmentInput.files[0];
    attachmentInput.value = '';
    if (!file) return;
    if (file.size > MAX_ATTACHMENT_MB * 1024 * 1024) {
      const shown = (file.size / 1024 / 1024).toFixed(1);
      hint.textContent = `这个文件 ${shown}MB，超过了 ${MAX_ATTACHMENT_MB}MB 的上限，换个小一点的吧`;
      return;
    }
    const targetSessionId = ensureSessionId();
    hint.textContent = file.size > 512 * 1024
      ? `正在上传 ${file.name}，文件较大，解析可能要等一会儿…`
      : `正在上传 ${file.name}…`;
    attachButton.disabled = true;
    try {
      const data = await API.uploadAttachment(targetSessionId, file);
      if (!data.success) {
        hint.textContent = `上传失败：${data.detail || data.error_type || '未知原因'}`;
        return;
      }
      pendingAttachments.push(data);
      renderChips();
      hint.textContent = `已附加 ${data.original_filename}，提取 ${data.char_count} 字`;
    } catch (error) {
      hint.textContent = `上传失败：${briefError(error)}`;
    } finally {
      attachButton.disabled = sending || loadingSession;
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
    if (sending || loadingSession) return;
    const text = input.value.trim();
    if (!text) return;

    const targetSessionId = ensureSessionId();
    const requestMode = mode;
    sending = true;
    setInteractionState();
    logInner.querySelector('.chat-welcome, .chat-empty')?.remove();
    addBubble('user', text, '', pendingAttachments.map((item) => item.original_filename));
    input.value = '';

    const attachmentIds = pendingAttachments.map((item) => item.attachment_id);
    pendingAttachments = [];
    renderChips();

    const { bubble, body } = addBubble('assistant', MODE_COPY[requestMode].waiting, 'pending');
    bubble.dataset.mode = requestMode;
    let answer = '';
    let streamFailed = false;

    try {
      await API.chatStream(targetSessionId, text, requestMode, attachmentIds, {
        onChunk(chunk) {
          if (!answer) bubble.classList.remove('pending');
          answer += chunk;
          body.textContent = answer;
          scrollToBottom();
        },
        onCitations(citations) {
          renderCitations(bubble, citations);
        },
        onReasoning() {
          if (!answer && requestMode === 'expert') {
            body.textContent = '专家模式正在进行多步分析，请继续等待…';
          }
        },
        onError(errorText) {
          streamFailed = true;
          bubble.classList.remove('pending');
          bubble.classList.add('failed');
          body.textContent = errorText || '服务暂时异常，请重试';
        },
        onDone() {
          bubble.classList.remove('pending');
          if (!answer && !streamFailed) {
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
      await refreshSessionsAfterMessage(targetSessionId);
      sending = false;
      setInteractionState();
      input.focus();
    }
  });

  async function initialize() {
    updateModeUi();
    setInteractionState();
    showWelcome();
    await refreshSessions();
    if (!sessionId) {
      input.focus();
      return;
    }
    const known = sessions.some((item) => item.session_id === sessionId);
    if (known) {
      await openSession(sessionId);
      return;
    }
    // 新建但尚未发送过消息的session不会进入后端列表；仍按契约尝试恢复一次。
    try {
      const history = await API.getHistory(sessionId);
      renderHistory(history);
    } catch (error) {
      if (error.status === 404) {
        sessionId = '';
        localStorage.removeItem(SESSION_KEY);
      } else {
        showWorkspaceMessage(`会话恢复失败：${briefError(error)}`, true);
      }
    }
    input.focus();
  }

  initialize();
})();
