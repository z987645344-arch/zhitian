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
