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

test('SSE文件保留对照，继续处理携带edit标记和当前文件，两次缩写不靠分类猜测', async () => {
  const calls = [], delivered = [];
  const event = {type:'file', file_id:'product', download_filename:'story-已修改.txt', file_type:'txt',
    edit_changes:[{action:'replace',before:'长故事',after:'短故事'}], edit_issues:[]};
  const sandbox = vm.createContext({window:{}, FormData, Blob, TextDecoder,
    localStorage:{getItem:()=> 'token'}, fetch:async (url, options)=> {
      calls.push(options);
      let read = false;
      return {ok:true, body:{getReader:()=>({read:async()=> read ? {done:true} :
        (read=true, {done:false,value:new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)} )})}};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/api.js'),'utf8'),sandbox);
  const api=vm.runInContext('API',sandbox), pool=files.create();
  pool.original('first',new File(['长故事'],'story.txt'));
  const first=files.planResend('缩写故事内容',['first'],pool,false,'edit');
  await api.chatStream('s','缩写故事内容','fast',first.ids,{onFile:f=>delivered.push(f)},first.originals,first.fileTaskType);
  assert.deepEqual(JSON.parse(JSON.stringify(delivered[0].edit_changes)),event.edit_changes);
  assert.equal(files.continuationIsEdit(delivered[0]),true);
  assert.equal(files.continuationIsEdit({download_filename:'converted.md'}),true);
  assert.equal(files.continuationIsEdit({download_filename:'converted.pdf'}),false);
  // 模拟继续处理重新上传手上的成品，显式标记沿用，不依赖“再缩写”关键词。
  pool.original('second',new File(['短故事'],delivered[0].download_filename));
  const second=files.planResend('再缩写',['second'],pool,true,files.continuationIsEdit(delivered[0])?'edit':'');
  await api.chatStream('s','再缩写','fast',second.ids,{},second.originals,second.fileTaskType);
  for(const request of calls) assert.equal(JSON.parse(request.body.get('payload')).file_task_type,'edit');
  assert.equal(await calls[1].body.get('files').text(),'短故事');
});

test('HTML 413只显示可读原因，长对照折叠且按钮在同一行，刷新历史显示已清理', async () => {
  const sandbox=vm.createContext({window:{},localStorage:{getItem:()=>''},FormData,Blob,
    fetch:async()=>({status:413,ok:false,text:async()=>'<html>413</html>'})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/api.js'),'utf8'),sandbox);
  await assert.rejects(vm.runInContext('API.uploadAttachment("s",new Blob(["test"]))',sandbox), /文件过大/);
  const source=fs.readFileSync(path.join(__dirname,'../web_client/js/chat.js'),'utf8');
  assert.match(source,/actions\.append\(button, reuse\)/);
  assert.match(source,/文件已清理/);
  const document={createElement:tag=>({tag,children:[],appendChild(x){this.children.push(x);}})};
  const panel=files.renderComparison(document,{edit_changes:[{before:'甲'.repeat(2800),after:'乙'.repeat(800)}]});
  assert.equal(panel.tag,'details');
  assert.match(panel.children[0].textContent,/1处.*点击展开/);
  assert.notEqual(panel.open,true);
  const css=fs.readFileSync(path.join(__dirname,'../web_client/css/style.css'),'utf8');
  assert.match(css,/\.edit-comparison pre[^}]*max-height:[^}]*overflow: auto/);
  assert.equal(files.historyFileLabel('已修改 故事-已修改.txt，请查看修改对照并及时下载保存。'),'故事-已修改.txt · 文件已清理');
  assert.equal(files.historyFileLabel('下载[故事.txt](/files/example-id)'),'故事.txt · 文件已清理');
});
