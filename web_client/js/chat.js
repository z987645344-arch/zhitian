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
  let fileCapabilities = ZhitianFileCapabilities.fallback();
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
  const TOOL_LABELS = {
    file_editing: '文件编辑与校验',
    web_search: '联网检索',
    knowledge_search: '知识库检索',
    document_list: '文件清单',
    answer_generation: '回答生成',
    file_generation: '文件生成',
    document_conversion: '格式转换',
    reflection: '判断：资料不够充分，换个问法再查',
  };
  const TOOL_PHASE_LABELS = {
    started: '进行中',
    succeeded: '已完成',
    degraded: '降级完成',
    failed: '失败',
    skipped: '已跳过',
  };
  const REASON_LABELS = {
    text_edit_failed: '文件编辑未完成',
    text_edit_partial: '部分修改未完成，请核对修改对照',
    fast_general_answer_failed: '通用知识备用回答未能生成',
    web_low_relevance: '联网结果相关性不足',
    web_provider_failed: '搜索服务不可用',
    web_no_results: '搜索无结果',
    query_rewrite_timeout: '搜索词改写超时，已使用原问题',
    search_summary_timeout: '搜索结果整理超时',
    search_summary_failed: '搜索结果整理失败',
    document_rerank_timeout: '文档精排超时，已保留原排序',
    final_answer_timeout: '最终回答生成超时',
    classification_timeout: '请求分类超时',
    planning_timeout: '任务规划超时',
    reflection_timeout: '补充判断超时',
    reflection_failed: '补充判断失败',
    output_observation_timeout: '结果校验超时',
    deepseek_rate_limit: '模型服务当前请求繁忙，建议稍后重试',
    deepseek_upstream_unavailable: '暂时无法连接模型服务，建议稍后重试',
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
  let hasFileHistory = false;
  const browserFiles = ZhitianTemporaryFiles.create();
  window.addEventListener('beforeunload', (event) => {
    if (browserFiles.hasUnsaved()) {
      event.preventDefault();
      event.returnValue = '有未保存的文件，确定离开吗？';
    }
  });
  window.addEventListener('pagehide', () => {
    if (sessionId) API.clearTemporaryFiles(sessionId).catch(() => {});
    browserFiles.clear();
  });
  window.setInterval(() => {
    browserFiles.purge();
    document.querySelectorAll('.generated-file-card').forEach(card => {
      if (!browserFiles.getProduct(card.dataset.fileId)) {
        card.querySelector('.generated-file-status').textContent = '文件已清理';
        card.querySelectorAll('button').forEach(button => { button.disabled = true; });
      }
    });
  }, 30000);

  function releasePageFiles() {
    if (browserFiles.hasUnsaved() && !window.confirm('有未保存的文件，确定离开吗？')) return false;
    if (sessionId) API.clearTemporaryFiles(sessionId).catch(() => {});
    browserFiles.clear();
    return true;
  }

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
    const markImage = document.createElement('img');
    markImage.src = './css/brand/avatar-128.webp?v=20260930';
    markImage.alt = '';
    mark.append(markImage);
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
    if (role === 'assistant') body.innerHTML = ZhitianMarkdown.renderMarkdown(text);
    else body.textContent = text;
    bubble.append(label, body);
    addAttachmentLabels(bubble, attachmentFilenames);
    logInner.appendChild(bubble);
    scrollToBottom();
    return { bubble, body };
  }

  function fileTypeLabel(fileType, filename) {
    const normalized = String(fileType || '').trim().toUpperCase();
    if (normalized) return normalized;
    const extension = String(filename || '').split('.').pop();
    return extension && extension !== filename ? extension.toUpperCase() : '文件';
  }

  function saveDownloadedBlob(blob, filename) {
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename || '知天生成文件';
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }

  function renderFileCard(bubble, file) {
    if (!file?.file_id || !file?.download_filename) return;
    const existing = bubble.querySelector(`[data-file-id="${CSS.escape(file.file_id)}"]`);
    if (existing) return;

    const card = document.createElement('section');
    card.className = 'generated-file-card';
    card.dataset.fileId = file.file_id;

    const icon = document.createElement('span');
    icon.className = 'generated-file-icon';
    icon.textContent = fileTypeLabel(file.file_type, file.download_filename);
    icon.setAttribute('aria-hidden', 'true');

    const copy = document.createElement('div');
    copy.className = 'generated-file-copy';
    const title = document.createElement('strong');
    title.textContent = file.download_filename;
    const status = document.createElement('span');
    status.className = 'generated-file-status';
    status.textContent = '文件已生成　临时存储1小时，请及时保存';
    status.setAttribute('aria-live', 'polite');
    copy.append(title, status);

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary generated-file-download';
    button.textContent = '下载';
    button.setAttribute('aria-label', `下载 ${file.download_filename}`);
    button.disabled = true;
    const pageSession = sessionId;
    API.downloadFile(file.file_id, file.download_filename).then(async download => {
      // 页面切换后到达的旧请求不能重新塞回内存。
      if (sessionId !== pageSession || !card.isConnected) return;
      browserFiles.product(file.file_id, download.blob, download.filename);
      button.disabled = false;
      await API.acknowledgeFile(file.file_id);
    }).catch(error => { status.textContent = `文件获取失败：${briefError(error)}`; });
    button.addEventListener('click', async () => {
      button.disabled = true;
      button.textContent = '下载中…';
      status.textContent = '正在验证身份并准备文件…';
      try {
        const download = browserFiles.getProduct(file.file_id);
        if (!download) throw new Error('文件已清理');
        saveDownloadedBlob(download.blob, download.filename);
        browserFiles.saved(file.file_id);
        status.textContent = '已保存　临时存储1小时';
      } catch (error) {
        status.textContent = `下载失败：${briefError(error)}`;
        card.classList.add('failed');
      } finally {
        button.disabled = !browserFiles.getProduct(file.file_id);
        button.textContent = '下载';
      }
    });

    const reuse = document.createElement('button');
    reuse.type = 'button';
    reuse.className = 'secondary';
    reuse.textContent = '继续处理';
    reuse.addEventListener('click', async () => {
      const product = browserFiles.getProduct(file.file_id);
      if (!product) { hint.textContent = ZhitianTemporaryFiles.ORIGINAL_CLEARED; return; }
      if (sending || loadingSession) return;
      try {
        const original = new File([product.blob], product.filename);
        const data = await API.uploadAttachment(ensureSessionId(), original);
        if (!data.success) throw new Error(data.detail || '文件上传失败');
        browserFiles.original(data.attachment_id, original);
        if (ZhitianTemporaryFiles.continuationIsEdit(file)) data.edit = true;
        pendingAttachments.push(data);
        renderChips();
      } catch (error) { hint.textContent = briefError(error); }
    });
    const actions = document.createElement('div');
    actions.className = 'generated-file-actions';
    actions.append(button, reuse);
    card.append(icon, copy, actions);
    if ((file.edit_changes || []).length) card.appendChild(ZhitianTemporaryFiles.renderComparison(document, file));
    bubble.appendChild(card);
    scrollToBottom();
  }

  function renderHistory(history) {
    hasFileHistory = history.some(item => item.message_type === 'file_delivery' || (item.attachment_filenames || []).length);
    logInner.replaceChildren();
    if (!history.length) {
      showWelcome();
      return;
    }
    history.forEach((item) => {
      if (item.message_type === 'file_trace') return;
      const role = item.role === 'user' ? 'user' : 'assistant';
      const interrupted = item.message_type === 'interrupted';
      const content = item.message_type === 'file_delivery'
        ? ZhitianTemporaryFiles.historyFileLabel(item.content)
        : String(item.content || '');
      addBubble(role, interrupted ? '回答已中断' : content,
        interrupted ? 'interrupted' : '', item.attachment_filenames || []);
    });
  }

  function renderInterrupted(bubble, body) {
    bubble.classList.remove('pending');
    bubble.classList.add('interrupted');
    body.textContent = '回答已中断';
    bubble.querySelectorAll('.citations, .generated-file-card').forEach((item) => item.remove());
  }

  // 引用来源如实展示后端字段：文件名、doc_id前8位与相关度分数。
  function renderCitations(bubble, citations) {
    const existing = bubble.querySelector('.citations');
    if (existing) existing.remove();
    if (!citations || !citations.length) return;
    const box = document.createElement('details');
    box.className = 'citations';
    const title = document.createElement('summary');
    title.className = 'citations-title';
    title.textContent = `这段回答参考了 ${citations.length} 处资料 · 查看来源`;
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
      const hasScore = item.score !== undefined && item.score !== null && item.score !== '';
      const score = hasScore ? Number(item.score) : NaN;
      meta.textContent = [
        docId ? `文档 ${docId.slice(0, 8)}` : '',
        Number.isFinite(score) ? `相关度 ${score.toFixed(3)}` : (!hasScore ? '同节补充' : ''),
        Number.isFinite(Number(item.chunk_index)) ? `资料位置 #${Number(item.chunk_index)}` : '',
      ].filter(Boolean).join(' · ');
      row.append(name, meta);
      box.appendChild(row);
    });
    bubble.appendChild(box);
    scrollToBottom();
  }

  function renderToolStatus(bubble, event) {
    let timeline = bubble.querySelector('.execution-timeline');
    if (!timeline) {
      timeline = document.createElement('details');
      timeline.className = 'execution-timeline';
      timeline.setAttribute('aria-live', 'polite');
      const heading = document.createElement('summary');
      heading.className = 'execution-title';
      heading.textContent = '正在处理这次问题 · 查看步骤';
      timeline.appendChild(heading);
      bubble.appendChild(timeline);
    }
    const key = `${event.tool || 'tool'}-${event.display_code || 'execution'}${event.occurrence ? '-' + event.occurrence : ''}`;
    let row = Array.from(timeline.querySelectorAll('.execution-item'))
      .find((item) => item.dataset.executionKey === key);
    if (!row) {
      row = document.createElement('div');
      row.className = 'execution-item';
      row.dataset.executionKey = key;
      const name = document.createElement('span');
      name.className = 'execution-name';
      const detail = document.createElement('span');
      detail.className = 'execution-detail';
      row.append(name, detail);
      timeline.appendChild(row);
    }
    row.dataset.phase = event.phase || '';
    timeline.querySelector('.execution-title').textContent = event.phase === 'started'
      ? '正在处理这次问题 · 查看步骤'
      : '处理进展已更新 · 查看步骤';
    row.querySelector('.execution-name').textContent = (TOOL_LABELS[event.display_code] || '工具执行')
      + (event.display_code === 'knowledge_search' && event.occurrence ? `（第 ${event.occurrence} 次）` : '');
    const parts = [TOOL_PHASE_LABELS[event.phase] || '状态更新'];
    if (Number.isFinite(event.result_count)) parts.push(`${event.result_count} 项结果`);
    if (Number.isFinite(event.elapsed_ms)) parts.push(`${(event.elapsed_ms / 1000).toFixed(1)} 秒`);
    if (event.reason_code && REASON_LABELS[event.reason_code]) parts.push(REASON_LABELS[event.reason_code]);
    row.querySelector('.execution-detail').textContent = parts.join(' · ');
    scrollToBottom();
  }

  function renderSourcePolicy(bubble, event) {
    renderToolStatus(bubble, { tool: 'source_policy', display_code: 'source_policy', phase: 'succeeded' });
    const row = Array.from(bubble.querySelectorAll('.execution-item'))
      .find((item) => item.dataset.executionKey === 'source_policy-source_policy');
    if (!row) return;
    const basis = { knowledge: '依据：知识库资料', general: '依据：通用知识（未联网）',
      web: '依据：联网搜索', conversation: '依据：本次对话', supplied_context: '依据：本轮附件资料', refusal: '未找到依据', unknown: '依据：暂未标明' };
    row.querySelector('.execution-name').textContent = '回答依据';
    row.querySelector('.execution-detail').textContent = basis[event.answer_source] || basis.unknown;
    scrollToBottom();
  }

  function renderRequestStatus(bubble, event) {
    bubble.dataset.requestStatus = event.status || '';
    if (event.status !== 'degraded') return;
    let notice = bubble.querySelector('.request-status-notice');
    if (!notice) {
      notice = document.createElement('details');
      notice.className = 'request-status-notice';
      bubble.appendChild(notice);
    }
    const labels = (event.reason_codes || []).map((code) => REASON_LABELS[code]).filter(Boolean);
    const summary = document.createElement('summary');
    summary.textContent = '部分步骤未能完成，请结合正文核对回答。';
    const detail = document.createElement('p');
    detail.textContent = labels.length
      ? `具体原因：${labels.join('；')}`
      : '本次回答未能完整完成，暂时没有更多原因说明。';
    notice.replaceChildren(summary, detail);
  }

  function renderSessions() {
    sessionList.replaceChildren();
    if (!sessions.length) {
      sessionStatus.textContent = '还没有对话。从下方输入一个问题，之后可在这里继续。';
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
      meta.textContent = `${ZhitianTime.formatLocalTime(item.last_active, { year: undefined, month: 'numeric', day: 'numeric' }, '时间未知')} · ${Number(item.message_count || 0)} 条消息`;
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
        const leftTime = ZhitianTime.parseTimestamp(left.last_active || left.created_at)?.getTime() || 0;
        const rightTime = ZhitianTime.parseTimestamp(right.last_active || right.created_at)?.getTime() || 0;
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
    if (!releasePageFiles()) return;
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
    if (!releasePageFiles()) return;
    hasFileHistory = false;
    sessionId = createSessionId();
    localStorage.setItem(SESSION_KEY, sessionId);
    pendingAttachments = [];
    renderChips();
    setConversationTitle('新对话');
    showWelcome();
    renderSessions();
    closeSidebar();
    hint.textContent = ZhitianFileCapabilities.hint(fileCapabilities);
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
        browserFiles.clear();
        hasFileHistory = false;
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
      if (/\.(txt|md)$/i.test(item.original_filename || '')) {
        const edit = document.createElement('button');
        edit.type = 'button';
        edit.textContent = item.edit ? '编辑模式 ✓' : '编辑此文件';
        edit.setAttribute('aria-pressed', String(Boolean(item.edit)));
        edit.addEventListener('click', () => {
          item.edit = !item.edit;
          renderChips();
          hint.textContent = item.edit ? '请写明修改要求；只修改选中的 txt / md 文件，不检索或联网。' : '已取消编辑模式';
        });
        chip.appendChild(edit);
      }
      chips.appendChild(chip);
    });
  }

  document.querySelector('#logoutButton').addEventListener('click', () => {
    if (!releasePageFiles()) return;
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

  async function refreshFileCapabilities() {
    try {
      fileCapabilities = ZhitianFileCapabilities.parse(await API.getFileEngines());
    } catch (_error) {
      // 能力接口失败不妨碍聊天；由上传接口继续执行最终校验。
      fileCapabilities = ZhitianFileCapabilities.fallback();
    }
    attachmentInput.accept = ZhitianFileCapabilities.accept(fileCapabilities);
    hint.textContent = ZhitianFileCapabilities.hint(fileCapabilities);
    return fileCapabilities;
  }

  attachmentInput.addEventListener('change', async () => {
    const file = attachmentInput.files && attachmentInput.files[0];
    attachmentInput.value = '';
    if (!file) return;
    if (attachButton.disabled || sending || loadingSession) return;
    attachButton.disabled = true;
    try {
      const state = await refreshFileCapabilities();
      const error = ZhitianFileCapabilities.validate(file, state);
      if (error) {
        hint.textContent = error;
        return;
      }
      const targetSessionId = ensureSessionId();
      hint.textContent = file.size > 512 * 1024
        ? `正在上传 ${file.name}，文件较大，解析可能要等一会儿…`
        : `正在上传 ${file.name}…`;
      const data = await API.uploadAttachment(targetSessionId, file);
      if (!data.success) {
        hint.textContent = `上传失败：${data.detail || data.error_type || '未知原因'}`;
        return;
      }
      pendingAttachments.push(data);
      browserFiles.original(data.attachment_id, file);
      hasFileHistory = true;
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
    const resend = ZhitianTemporaryFiles.planResend(text,
      pendingAttachments.map((item) => item.attachment_id), browserFiles, hasFileHistory,
      pendingAttachments.some(item => item.edit) ? 'edit' : '');
    if (resend.error) {
      hint.textContent = resend.error;
      return;
    }
    const attachmentIds = resend.ids, originals = resend.originals;
    sending = true;
    setInteractionState();
    logInner.querySelector('.chat-welcome, .chat-empty')?.remove();
    addBubble('user', text, '', pendingAttachments.map((item) => item.original_filename));
    input.value = '';
    pendingAttachments = [];
    renderChips();

    const { bubble, body } = addBubble('assistant', resend.fileTaskType === 'edit'
      ? '正在制定修改方案，随后核对并生成文件，请稍候…' : MODE_COPY[requestMode].waiting, 'pending');
    bubble.dataset.mode = requestMode;
    let answer = '';
    let streamFailed = false;

    try {
      await API.chatStream(targetSessionId, text, requestMode, attachmentIds, {
        onChunk(chunk) {
          if (!answer) bubble.classList.remove('pending');
          answer += chunk;
          body.innerHTML = ZhitianMarkdown.renderMarkdown(answer);
          scrollToBottom();
        },
        onCitations(citations) {
          renderCitations(bubble, citations);
        },
        onFile(file) {
          renderFileCard(bubble, file);
        },
        onToolStatus(toolEvent) {
          renderToolStatus(bubble, toolEvent);
        },
        onSourcePolicy(sourceEvent) {
          renderSourcePolicy(bubble, sourceEvent);
        },
        onRequestStatus(statusEvent) {
          renderRequestStatus(bubble, statusEvent);
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
        onInterrupted() {
          answer = '';
          streamFailed = true;
          renderInterrupted(bubble, body);
        },
      }, originals, resend.fileTaskType || '');
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
    refreshFileCapabilities(); // 不让能力探测阻塞会话加载与聊天。
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
