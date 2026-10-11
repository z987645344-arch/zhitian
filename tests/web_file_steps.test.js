const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');

function timeline() {
  const rows = [], heading = {};
  const panel = { querySelectorAll: () => rows, querySelector: () => heading, appendChild: row => rows.push(row) };
  function element() { return { dataset: {}, children: [], append(...items) { this.children.push(...items); },
    querySelector(selector) { return this.children.find(item => '.' + item.className === selector); } }; }
  const context = vm.createContext({ document: { createElement: element }, scrollToBottom() {}, REASON_LABELS: {} });
  const labelsStart = source.indexOf('  const TOOL_LABELS =');
  const labelsEnd = source.indexOf('  const REASON_LABELS =', labelsStart);
  const renderStart = source.indexOf('  function renderToolStatus(');
  const renderEnd = source.indexOf('  function renderSourcePolicy(', renderStart);
  vm.runInContext(source.slice(labelsStart, labelsEnd) + source.slice(renderStart, renderEnd), context);
  return { rows, render: event => vm.runInContext('renderToolStatus', context)({querySelector: () => panel}, event) };
}

test('文件正文显示文件生成12.5秒，落盘显示文件保存，不重复零秒文件生成', () => {
  const { rows, render } = timeline();
  for (const [tool, code, elapsed] of [['llm_chat', 'file_generation', 12500], ['generate_file', 'file_saving', 0]]) {
    render({ tool, display_code: code, phase: 'started' });
    render({ tool, display_code: code, phase: 'succeeded', elapsed_ms: elapsed });
  }
  assert.equal(rows.length, 2);
  assert.equal(rows[0].children[0].textContent, '文件生成');
  assert.equal(rows[0].children[1].textContent, '已完成 · 12.5 秒');
  assert.equal(rows[1].children[0].textContent, '文件保存');
  assert.equal(rows[1].children[1].textContent, '已完成 · 0.0 秒');
});

test('旧历史的回答生成和文件生成标签仍正常显示，编辑转换标签不变', () => {
  const { rows, render } = timeline();
  for (const [tool, code] of [['llm_chat', 'answer_generation'], ['generate_file', 'file_generation'],
      ['edit_document', 'file_editing'], ['convert_document', 'document_conversion']]) {
    render({ tool, display_code: code, phase: 'succeeded', elapsed_ms: 12500 });
  }
  assert.deepEqual(rows.map(row => row.children[0].textContent), ['回答生成', '文件生成', '文件编辑与校验', '格式转换']);
  assert.ok(rows.every(row => row.children[1].textContent === '已完成 · 12.5 秒'));
});
