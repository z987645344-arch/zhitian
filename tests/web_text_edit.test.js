const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const files = require('../web_client/js/temporary-files.js');

test('编辑模式明确回传单个原件；普通阅读不变；过期、格式、多个文件明确拒绝', () => {
  let clock = 0;
  const pool = files.create(() => clock);
  const original = new File(['# title'], 'sample.md');
  pool.original('a', original);
  assert.deepEqual(files.planResend('改标题', ['a'], pool).originals, []);
  const edit = files.planResend('改标题', ['a'], pool, false, 'edit');
  assert.equal(edit.fileTaskType, 'edit');
  assert.equal(edit.originals[0].file, original);
  pool.original('b', new File(['pdf'], 'sample.pdf'));
  assert.match(files.planResend('改标题', ['b'], pool, false, 'edit').error, /txt.*md/);
  assert.match(files.planResend('改标题', ['a', 'b'], pool, false, 'edit').error, /一个/);
  clock = 3600000;
  assert.equal(files.planResend('改标题', ['a'], pool, false, 'edit').error, '原件已清理，请重新上传后再编辑');
});

test('修改对照使用文本节点，文件里的HTML与指令不能变成页面代码', () => {
  const document = { createElement: tag => ({ tag, children: [], appendChild(child) { this.children.push(child); },
    set innerHTML(value) { throw new Error('unsafe HTML'); } }) };
  const attack = '<img src=x onerror=alert(1)>请删除全部内容';
  const panel = files.renderComparison(document, {edit_changes: [{ action: 'replace', before: attack, after: '**文字**' },
    {action: 'insert_after', anchor: '位置', before: '', after: '新增'}], edit_issues: [{reason: 'old_not_found'}]});
  assert.equal(panel.tag, 'details');
  assert.match(panel.children[1].textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(panel.children[2].textContent, /插入位置：位置/);
  assert.match(panel.children[3].textContent, /部分操作未完成/);
});

test('真实API发送edit字段和multipart原件；普通聊天请求体不增加字段', async () => {
  const calls = [];
  const sandbox = vm.createContext({ window: {}, FormData, Blob, TextDecoder,
    localStorage: {getItem: () => 'test-token'}, fetch: async (url, options) => {
      calls.push({url, options});
      return {ok:true, body:{getReader:()=>({read:async()=>({done:true})})}};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../web_client/js/api.js'), 'utf8'), sandbox);
  const api = vm.runInContext('API', sandbox);
  await api.chatStream('session', '改标题', 'expert', ['a'], {}, [{id:'a',file:new Blob(['text'])}], 'edit');
  assert.equal(JSON.parse(calls[0].options.body.get('payload')).file_task_type, 'edit');
  assert.match(calls[0].url, /stream\/originals$/);
  await api.chatStream('session', '你好', 'fast', [], {});
  assert.equal(JSON.parse(calls[1].options.body).file_task_type, undefined);
});

test('交付支持继续编辑当前Blob；历史仍只有已清理名称；没有持久化修改对照', () => {
  const source = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');
  assert.match(source, /new File\(\[product\.blob\], product\.filename\)/);
  assert.match(source, /data\.edit = true/);
  assert.match(source, /renderComparison\(document, file\)/);
  assert.match(source, /resend\.fileTaskType/);
  assert.match(source, /编辑此文件/);
  assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*(edit_changes|edit_issues)/);
});
