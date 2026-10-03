const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const apiSource = fs.readFileSync(path.join(__dirname, '../web_client/js/api.js'), 'utf8');
const chatSource = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');

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
  for (const [source, expected] of [['web', '依据：联网搜索'], ['refusal', '未找到依据']]) {
    render({ querySelectorAll: () => [row] }, { answer_source: source });
    assert.equal(detail.textContent, expected);
  }
  assert.ok(!chatSource.slice(start, end).includes('innerHTML'));
  assert.ok(!chatSource.includes('`片段 #'));
  assert.match(chatSource, /onSourcePolicy\(sourceEvent\)/);
});
