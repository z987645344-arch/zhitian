const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const apiSource = fs.readFileSync(path.join(__dirname, '../web_client/js/api.js'), 'utf8');
const chatSource = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');

test('刷新历史显示持久化中断标记，不显示半截回答，用户问题仍可见', () => {
  const calls = [];
  const sandbox = vm.createContext({ logInner: { replaceChildren() {} }, showWelcome() {},
    addBubble: (...args) => calls.push(args) });
  const start = chatSource.indexOf('  function renderHistory(');
  const end = chatSource.indexOf('  // 引用来源', start);
  vm.runInContext(chatSource.slice(start, end), sandbox);
  const render = vm.runInContext('renderHistory', sandbox);
  render([{ role: 'user', content: '测试问题', message_type: 'interrupted_user' },
    { role: 'assistant', content: '回答已中断', message_type: 'interrupted' }]);
  assert.deepEqual(calls.map(args => args.slice(0, 3)), [
    ['user', '测试问题', ''], ['assistant', '回答已中断', 'interrupted']]);
  assert.doesNotMatch(calls.flat().join(''), /半截回答/);
});

test('SSE无DONE的EOF或读错误都表示中断，不能当作回答完成', async () => {
  for (const readError of [false, true]) {
    let reads = 0, done = 0, interrupted = 0;
    const sandbox = vm.createContext({ window: {}, localStorage: { getItem: () => 'test-token' }, TextDecoder,
      fetch: async () => ({ ok: true, status: 200, body: { getReader: () => ({ read: async () => {
        if (reads++ === 0) return { done: false, value: new TextEncoder().encode('data: {"chunk":"半截回答"}\n\n') };
        if (readError) throw new Error('network stopped');
        return { done: true };
      } }) } }) });
    vm.runInContext(apiSource, sandbox);
    await vm.runInContext('API', sandbox).chatStream('session', '测试请求', 'expert', [], {
      onDone: () => done++, onInterrupted: () => interrupted++,
    });
    assert.equal(done, 0);
    assert.equal(interrupted, 1);
  }
});

test('中断显示替换半截正文并移除未完成的来源和文件', () => {
  const start = chatSource.indexOf('  function renderInterrupted(');
  const end = chatSource.indexOf('  // 引用来源', start);
  const sandbox = vm.createContext({});
  vm.runInContext(chatSource.slice(start, end), sandbox);
  const flags = [], removed = [], body = { innerHTML: '半截回答' };
  const bubble = { classList: { remove: item => flags.push('remove:' + item), add: item => flags.push('add:' + item) },
    querySelectorAll: () => [{ remove: () => removed.push(1) }, { remove: () => removed.push(2) }] };
  vm.runInContext('renderInterrupted', sandbox)(bubble, body);
  assert.equal(body.textContent, '回答已中断');
  assert.deepEqual(flags, ['remove:pending', 'add:interrupted']);
  assert.deepEqual(removed, [1, 2]);
});

test('无分数的补取来源标为同节补充，正常相关度包括零分均如实显示', () => {
  function element() { return { children: [], appendChild(child) { this.children.push(child); },
    append(...children) { this.children.push(...children); } }; }
  const sandbox = vm.createContext({ document: { createElement: element }, scrollToBottom: () => {} });
  const start = chatSource.indexOf('  function renderCitations(');
  const end = chatSource.indexOf('  function renderToolStatus(', start);
  vm.runInContext(chatSource.slice(start, end), sandbox);
  const render = vm.runInContext('renderCitations', sandbox);
  for (const [item, expected] of [[{}, '同节补充'], [{ score: null }, '同节补充'],
    [{ score: '' }, '同节补充'], [{ score: 0 }, '相关度 0.000'],
    [{ score: .625 }, '相关度 0.625'], [{ score: 'invalid' }, '']]) {
    const bubble = element();
    bubble.querySelector = () => null;
    render(bubble, [{ source: '<script>source</script>', doc_id: 'abcdefgh1234', chunk_index: 2, ...item }]);
    const row = bubble.children[0].children[1];
    assert.equal(row.children[0].textContent, '<script>source</script>');
    assert.ok(row.children[1].textContent.includes('资料位置 #2'));
    assert.ok(row.children[1].textContent.includes('文档 abcdefgh'));
    if (expected) assert.ok(row.children[1].textContent.includes(expected));
    else assert.doesNotMatch(row.children[1].textContent, /同节补充|相关度/);
    if (expected === '同节补充') assert.doesNotMatch(row.children[1].textContent, /相关度|NaN/);
  }
});

test('实际SSE解析传递来源详情，正文与DONE不受影响，不转发额外内容', async () => {
  const events = [
    { type: 'source_policy', source: 'public', time_sensitivity: 'general', only_materials: false,
      classification_valid: true, evidence: 'miss', answer_source: 'general', reason: 'fast_general', query: 'PRIVATE_MARKER' },
    { type: 'tool_status', tool: 'search_documents', display_code: 'knowledge_search', phase: 'started', occurrence: 2 },
    { chunk: '服务端备注。正文' }, { chunk: '[DONE]' },
  ];
  let read = false;
  const sandbox = vm.createContext({ window: {}, localStorage: { getItem: () => 'test-token' }, TextDecoder,
    fetch: async () => ({ ok: true, status: 200, body: { getReader: () => ({ read: async () => {
      if (read) return { done: true };
      read = true;
      return { done: false, value: new TextEncoder().encode(events.map(e => 'data: ' + JSON.stringify(e) + '\n\n').join('')) };
    } }) } }) });
  vm.runInContext(apiSource, sandbox);
  const api = vm.runInContext('API', sandbox);
  const received = [], chunks = [], tools = [];
  let done = 0;
  await api.chatStream('test-session', '测试请求', 'fast', [], {
    onSourcePolicy: e => received.push(e), onChunk: e => chunks.push(e), onDone: () => done++, onToolStatus: e => tools.push(e),
  });
  assert.equal(received.length, 1);
  assert.equal(received[0].reason, 'fast_general');
  assert.equal(received[0].answer_source, 'general');
  assert.ok(!JSON.stringify(received).includes('PRIVATE_MARKER'));
  assert.deepEqual(chunks, ['服务端备注。正文']);
  assert.equal(done, 1);
  assert.equal(tools[0].occurrence, 2);
});

test('两轮检索、反思和生成依次保留，完成事件不覆盖另一轮', () => {
  const start = chatSource.indexOf('  function renderToolStatus(');
  const end = chatSource.indexOf('  function renderSourcePolicy(', start);
  const rows = [], heading = {};
  const timeline = { querySelectorAll: () => rows, querySelector: () => heading, appendChild: row => rows.push(row) };
  function element() { return { dataset: {}, children: [], append(...items) { this.children.push(...items); },
    querySelector(selector) { return this.children.find(item => '.' + item.className === selector); } }; }
  const sandbox = vm.createContext({ document: { createElement: element }, scrollToBottom: () => {},
    TOOL_LABELS: { knowledge_search: '知识库检索', reflection: '判断：资料不够充分，换个问法再查', answer_generation: '回答生成' },
    TOOL_PHASE_LABELS: {}, REASON_LABELS: {} });
  vm.runInContext(chatSource.slice(start, end), sandbox);
  const render = vm.runInContext('renderToolStatus', sandbox), bubble = { querySelector: () => timeline };
  render(bubble, { tool: 'search_documents', display_code: 'knowledge_search', phase: 'started', occurrence: 1 });
  render(bubble, { tool: 'search_documents', display_code: 'knowledge_search', phase: 'succeeded', occurrence: 1 });
  render(bubble, { tool: 'reflection', display_code: 'reflection', phase: 'succeeded' });
  render(bubble, { tool: 'search_documents', display_code: 'knowledge_search', phase: 'started', occurrence: 2 });
  render(bubble, { tool: 'llm_chat', display_code: 'answer_generation', phase: 'started' });
  assert.deepEqual(rows.map(row => row.querySelector('.execution-name').textContent), [
    '知识库检索（第 1 次）', '判断：资料不够充分，换个问法再查', '知识库检索（第 2 次）', '回答生成']);
});

test('实际详情只显示访客可理解的依据，API详细字段不显示，不使用HTML注入', () => {
  const start = chatSource.indexOf('  function renderSourcePolicy(');
  const end = chatSource.indexOf('  function renderRequestStatus(', start);
  const name = { textContent: '' }, detail = { textContent: '' };
  const row = { dataset: { executionKey: 'source_policy-source_policy' },
    querySelector: key => key === '.execution-name' ? name : detail };
  const sandbox = vm.createContext({ renderToolStatus: () => {}, scrollToBottom: () => {} });
  vm.runInContext(chatSource.slice(start, end), sandbox);
  const render = vm.runInContext('renderSourcePolicy', sandbox);
  render({ querySelectorAll: () => [row] }, { source: 'public', time_sensitivity: 'general',
    only_materials: false, classification_valid: true, evidence: 'miss', answer_source: 'general', reason: 'fast_general' });
  assert.equal(name.textContent, '回答依据');
  assert.equal(detail.textContent, '依据：通用知识（未联网）');
  render({ querySelectorAll: () => [row] }, { source: 'internal', time_sensitivity: 'general',
    only_materials: false, classification_valid: true, evidence: 'weak', answer_source: 'knowledge', reason: 'knowledge_weak' });
  assert.equal(detail.textContent, '依据：知识库资料');
  for (const [source, expected] of [['web', '依据：联网搜索'], ['refusal', '未找到依据'],
    ['conversation', '依据：本次对话'], ['unknown', '依据：暂未标明'], [undefined, '依据：暂未标明']]) {
    render({ querySelectorAll: () => [row] }, { answer_source: source });
    assert.equal(detail.textContent, expected);
  }
  assert.ok(!chatSource.slice(start, end).includes('innerHTML'));
  assert.ok(!chatSource.includes('`片段 #'));
  assert.match(chatSource, /onSourcePolicy\(sourceEvent\)/);
});

test('附件依据使用supplied_context标签', () => {
  const start = chatSource.indexOf('  function renderSourcePolicy(');
  const end = chatSource.indexOf('  function renderRequestStatus(', start);
  const detail = { textContent: '' };
  const row = { dataset: { executionKey: 'source_policy-source_policy' },
    querySelector: key => key === '.execution-name' ? { textContent: '' } : detail };
  const sandbox = vm.createContext({ renderToolStatus: () => {}, scrollToBottom: () => {} });
  vm.runInContext(chatSource.slice(start, end), sandbox);
  vm.runInContext('renderSourcePolicy', sandbox)({ querySelectorAll: () => [row] },
    { answer_source: 'supplied_context' });
  assert.equal(detail.textContent, '依据：本轮附件资料');
});

// 文件能力回归放在CI已执行的网页测试文件中，不遗漏新测试。
const read = name => fs.readFileSync(path.join(__dirname, '../web_client', name), 'utf8');
const source = read('js/file_capabilities.js');
const chat = read('js/chat.js');

function library() {
  const context = vm.createContext({});
  vm.runInContext(source, context);
  return vm.runInContext('ZhitianFileCapabilities', context);
}
function payload(status, size = 17) {
  return { engines: [{ engine_name: 'libreoffice', status }], max_upload_size_mb: size };
}

test('就绪时保留格式提示并读取后端大小；pending/failed明确禁用Office，不影响原生格式', () => {
  const lib = library();
  for (const status of ['ready', 'pending', 'failed']) {
    const state = lib.parse(payload(status));
    assert.match(lib.hint(state), /17MB/);
    assert.equal(state.officeReady, status === 'ready');
    for (const extension of ['doc', 'xls', 'xlsx', 'ppt', 'pptx']) {
      const error = lib.validate({ name: `file.${extension.toUpperCase()}`, size: 1 }, state);
      assert.equal(Boolean(error), status !== 'ready');
      assert.equal(lib.accept(state).includes('.' + extension + ',' ) ||
        lib.accept(state).endsWith('.' + extension), status === 'ready');
    }
    for (const extension of ['txt', 'md', 'pdf', 'docx']) {
      assert.equal(lib.validate({ name: `file.${extension}`, size: 1 }, state), '');
      assert.ok(lib.accept(state).includes('.' + extension));
    }
  }
  assert.match(lib.hint(lib.parse(payload('failed'))), /暂时无法处理.*txt、md、pdf、docx 不受影响/);
});

test('大小检查采用服务器配置，边界允许；接口失败的静态回退为50MB', () => {
  const lib = library(), state = lib.parse(payload('ready', 2));
  assert.equal(lib.validate({ name: 'a.txt', size: 2 * 1024 * 1024 }, state), '');
  assert.match(lib.validate({ name: 'a.txt', size: 2 * 1024 * 1024 + 1 }, state), /2MB 的上限/);
  assert.match(lib.hint(lib.fallback()), /常见 Office 格式.*50MB/);
  assert.throws(() => lib.parse({ engines: [] }));
});

function uploadHarness(response, file) {
  const callbacks = {}, calls = { uploads: 0, capabilities: 0, sessions: 0 };
  const sandbox = vm.createContext({ ZhitianFileCapabilities: library(),
    fileCapabilities: library().fallback(), hint: {}, attachButton: { disabled: false },
    attachmentInput: { files: [file], addEventListener: (name, handler) => callbacks[name] = handler },
    sending: false, loadingSession: false, pendingAttachments: [], renderChips() {},
    attachmentPage: {ready: Promise.resolve(), pageId:'page', register() {}},
    ensureSessionId: () => { calls.sessions++; return 'session'; },
    briefError: error => error.message,
    API: {
      getFileEngines: async () => { calls.capabilities++; if (response instanceof Error) throw response; return response; },
      uploadAttachment: async () => { calls.uploads++; return { success: true, char_count: 1, original_filename: 'fixture' }; },
    } });
  vm.runInContext(chat.slice(chat.indexOf('  async function refreshFileCapabilities('),
    chat.indexOf("  input.addEventListener('keydown'")), sandbox);
  return { sandbox, calls, change: callbacks.change };
}

test('真实选择附件路径在上传前重检；不可用Office零上传，原生文件仍上传', async () => {
  for (const ext of ['doc', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'md', 'pdf', 'docx']) {
    const { calls, sandbox, change } = uploadHarness(payload('failed'), { name: 'file.' + ext, size: 1 });
    await change();
    assert.equal(calls.capabilities, 1);
    const native = ['txt', 'md', 'pdf', 'docx'].includes(ext);
    assert.equal(calls.uploads, native ? 1 : 0);
    assert.equal(calls.sessions, native ? 1 : 0);
    assert.equal(sandbox.attachButton.disabled, false);
    if (!native) assert.match(sandbox.hint.textContent, /暂时无法处理/);
  }
});

test('接口失败不抛错；恢复就绪后的新一次选择可上传；超大小在上传前拒绝', async () => {
  let harness = uploadHarness(new Error('offline'), { name: 'file.xlsx', size: 1 });
  await harness.change();
  assert.equal(harness.calls.uploads, 1);
  harness = uploadHarness(payload('ready', 1), { name: 'file.txt', size: 2 * 1024 * 1024 });
  await harness.change();
  assert.equal(harness.calls.uploads, 0);
  assert.match(harness.sandbox.hint.textContent, /1MB 的上限/);
  harness = uploadHarness(payload('failed'), { name: 'file.xlsx', size: 1 });
  await harness.change();
  harness.sandbox.attachmentInput.files = [{ name: 'file.xlsx', size: 1 }];
  harness.sandbox.API.getFileEngines = async () => payload('ready');
  await harness.change();
  assert.equal(harness.calls.uploads, 1);
});

test('附件加密的服务端可读原因显示给访客，不显示内部错误码', async () => {
  const harness = uploadHarness(payload('ready'), { name: 'synthetic.docx', size: 1 });
  harness.sandbox.API.uploadAttachment = async () => {
    throw new Error('文件已加密，请去掉打开密码后再上传');
  };
  await harness.change();
  assert.match(harness.sandbox.hint.textContent, /文件已加密，请去掉打开密码后再上传/);
  assert.doesNotMatch(harness.sandbox.hint.textContent, /encrypted_file|invalid_file/);
});

test('加载时探测不阻塞聊天；API封装带鉴权；四页缓存参数一致，模块在chat之前', async () => {
  assert.match(chat.slice(chat.indexOf('  async function initialize()')), /refreshFileCapabilities\(\);/);
  const requests = [];
  const context = vm.createContext({ window: {}, localStorage: { getItem: () => 'test-token' },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, status: 200, text: async () => JSON.stringify(payload('ready')) };
    } });
  vm.runInContext(read('js/api.js'), context);
  await vm.runInContext('API', context).getFileEngines();
  assert.equal(requests[0].url, '/api/file-processing/engines');
  assert.equal(requests[0].options.headers.Authorization, 'Bearer test-token');
  for (const page of ['chat', 'login', 'register', 'settings']) {
    assert.match(read(page + '.html'), /api\.js\?v=attachment-reread-20261009/);
  }
  assert.ok(read('chat.html').indexOf('file_capabilities.js') < read('chat.html').indexOf('js/chat.js'));
  assert.ok(read('chat.html').indexOf('temporary-files.js') < read('chat.html').indexOf('js/chat.js'));
});

test('原件和产物只在页面内存保存，回执不等于用户已保存；过期/切换全部失效', () => {
  const { create } = require('../web_client/js/temporary-files.js');
  let clock = 0;
  const pool = create(() => clock);
  const original = new Blob(['original']), product = new Blob(['product']);
  pool.original('a', original);
  pool.product('p', product, 'result.docx');
  assert.equal(pool.getOriginal('a'), original);
  assert.equal(pool.getProduct('p').blob, product);
  assert.equal(pool.hasUnsaved(), true);
  pool.saved('p');
  assert.equal(pool.hasUnsaved(), false);
  clock = 60 * 60 * 1000;
  assert.equal(pool.getOriginal('a'), undefined);
  assert.equal(pool.getProduct('p'), undefined);
  pool.original('a', original);
  pool.product('p', product, 'result.docx');
  pool.clear();
  assert.equal(pool.getOriginal('a'), undefined);
  assert.equal(pool.getProduct('p'), undefined);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, '../web_client/js/temporary-files.js'), 'utf8'),
    /localStorage\.|sessionStorage\.|indexedDB\.|caches\./);
});

test('普通阅读不重发原件；转换只重发选中原件；失效时明确提示且不发请求', () => {
  const { create, planResend, ORIGINAL_CLEARED } = require('../web_client/js/temporary-files.js');
  let clock = 0;
  const pool = create(() => clock), file = new Blob(['original']);
  pool.original('a', file);
  assert.deepEqual(planResend('阅读这个文档', ['a'], pool).originals, []);
  assert.equal(planResend('转换成PDF', ['a'], pool).originals[0].file, file);
  assert.deepEqual(planResend('转换成PDF', [], pool).ids, ['a']);
  clock = 3600000;
  assert.equal(planResend('转换成PDF', ['a'], pool).error, ORIGINAL_CLEARED);
  assert.equal(planResend('转换成PDF', [], pool, true).error, ORIGINAL_CLEARED);
  assert.deepEqual(planResend('讨论文件格式转换原理', [], pool, false).originals, []);
  pool.original('a', file); pool.original('b', file);
  assert.match(planResend('转换成PDF', [], pool, true).error, /选择/);
});

test('历史隐藏文件痕迹，历史产物标明已清理且不重建下载链接', () => {
  const calls = [];
  const start = chatSource.indexOf('  function renderHistory(');
  const end = chatSource.indexOf('  function renderInterrupted(', start);
  const sandbox = vm.createContext({ ZhitianTemporaryFiles: require('../web_client/js/temporary-files.js'),
    logInner: { replaceChildren() {} }, showWelcome() {},
    addBubble: (...args) => calls.push(args) });
  vm.runInContext(chatSource.slice(start, end), sandbox);
  vm.runInContext('renderHistory', sandbox)([
    { role: 'assistant', message_type: 'file_trace', content: 'hidden' },
    { role: 'assistant', message_type: 'file_delivery', content: '已生成 report.md，可通过 /files/abc 下载' },
  ]);
  assert.equal(calls.length, 1);
  assert.match(calls[0][1], /report.md.*文件已清理/);
  assert.doesNotMatch(calls[0][1], /\/files\//);
});

test('原件重传是带鉴权的multipart，普通阅读保持JSON；不把原件写入持久缓存', async () => {
  for (const resend of [false, true]) {
    const calls = [];
    const sandbox = vm.createContext({ window: {}, FormData, Blob, TextDecoder,
      localStorage: { getItem: () => 'test-token' },
      fetch: async (url, options) => {
        calls.push({ url, options });
        return { ok: true, body: { getReader: () => ({ read: async () => ({ done: true }) }) } };
      } });
    vm.runInContext(apiSource, sandbox);
    await vm.runInContext('API', sandbox).chatStream('session', 'test', 'expert', ['attachment'], {},
      resend ? [{ id: 'attachment', file: new Blob(['original']) }] : []);
    assert.equal(calls.length, 1);
    const { url, options } = calls[0];
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    if (resend) {
      assert.match(url, /\/chat\/stream\/originals$/);
      assert.equal(options.headers['Content-Type'], undefined);
      assert.equal(options.body.get('original_ids'), '["attachment"]');
      assert.equal(await options.body.get('files').text(), 'original');
      assert.equal(JSON.parse(options.body.get('payload')).session_id, 'session');
    } else {
      assert.match(url, /\/chat\/stream$/);
      assert.equal(options.headers['Content-Type'], 'application/json');
    }
  }
  const cache = read('js/temporary-files.js');
  assert.doesNotMatch(cache, /localStorage\.(setItem|getItem)|indexedDB\.|caches\./);
});

test('浏览器取完Blob才确认清理；离页请求keepalive，带归属鉴权', async () => {
  const events = [];
  const sandbox = vm.createContext({ window: {}, localStorage: { getItem: () => 'test-token' },
    fetch: async (url, options) => {
      events.push([url, options]);
      return { ok: true, status: 200, headers: { get: () => null }, text: async () => '{}',
        blob: async () => { events.push(['blob complete']); return new Blob(['done']); } };
    } });
  vm.runInContext(apiSource, sandbox);
  const api = vm.runInContext('API', sandbox);
  const result = await api.downloadFile('file', 'report.txt');
  assert.equal(await result.blob.text(), 'done');
  assert.equal(events.length, 2);
  await api.acknowledgeFile('file');
  assert.match(events[2][0], /\/files\/file\/receipt$/);
  await api.clearTemporaryFiles('session');
  assert.equal(events[3][1].keepalive, true);
  assert.equal(events[3][1].headers.Authorization, 'Bearer test-token');
  assert.match(chatSource, /beforeunload[\s\S]*hasUnsaved[\s\S]*preventDefault/);
  assert.match(chatSource, /pagehide[\s\S]*clearTemporaryFiles/);
});
