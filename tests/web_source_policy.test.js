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
  const received = [], chunks = [];
  let done = 0;
  await api.chatStream('test-session', '测试请求', 'fast', [], {
    onSourcePolicy: e => received.push(e), onChunk: e => chunks.push(e), onDone: () => done++,
  });
  assert.equal(received.length, 1);
  assert.equal(received[0].reason, 'fast_general');
  assert.equal(received[0].answer_source, 'general');
  assert.ok(!JSON.stringify(received).includes('PRIVATE_MARKER'));
  assert.deepEqual(chunks, ['服务端备注。正文']);
  assert.equal(done, 1);
});

test('实际详情渲染显示三轴、证据和原因，不使用HTML注入', () => {
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
  assert.equal(name.textContent, '来源与依据');
  assert.match(detail.textContent, /公开信息.*一般知识.*未命中.*通用知识.*快速模式未联网/);
  render({ querySelectorAll: () => [row] }, { source: 'internal', time_sensitivity: 'general',
    only_materials: false, classification_valid: true, evidence: 'weak', answer_source: 'knowledge', reason: 'knowledge_weak' });
  assert.match(detail.textContent, /内部事务.*弱证据.*片段相关但尚未确认充分/);
  assert.ok(!chatSource.slice(start, end).includes('innerHTML'));
  assert.match(chatSource, /onSourcePolicy\(sourceEvent\)/);
});
