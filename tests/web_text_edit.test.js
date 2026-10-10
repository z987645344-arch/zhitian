const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const files = require('../web_client/js/temporary-files.js');
const attachmentPage = require('../web_client/js/attachment-page.js');

function tabStorage(seed = {}) {
  const data = {...seed};
  return {data, getItem: key => data[key] || null, setItem: (key, value) => { data[key] = value; }};
}

test('没有randomUUID的HTTP开发页面仍可正常初始化，不阻断无附件聊天', async () => {
  const sandbox = vm.createContext({window:{}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/attachment-page.js'),'utf8'),sandbox);
  const page=sandbox.window.ZhitianAttachmentPage.create(tabStorage(),async()=>{});
  await page.ready;
  assert.match(page.pageId,/^page-/);
});

test('刷新新页面主动删除旧页面附件；切换会话清理；只存标识不存正文', async () => {
  const storage = tabStorage(), calls = [];
  const remove = async (...args) => calls.push(args);
  const first = attachmentPage.create(storage, remove, {pageId: 'old'});
  await first.ready;
  first.register('s', 'a');
  const refreshed = attachmentPage.create(storage, remove, {pageId: 'new', navigationType:'reload'});
  await refreshed.ready;
  assert.deepEqual(calls, [['s', 'old', ['a']]]);
  refreshed.register('s', 'b');
  await refreshed.clear('s');
  assert.deepEqual(calls[1], ['s', 'new', ['b']]);
  assert.equal(storage.data.zt_attachment_page_ledger, '[]');
});

test('另一个标签页及复制的sessionStorage不能误删活跃标签页的附件', async () => {
  const storage = tabStorage(), calls = [];
  const remove = async (...args) => calls.push(args);
  const first = attachmentPage.create(storage, remove, {pageId:'tab-a'});
  await first.ready; first.register('s', 'a');
  const clone = attachmentPage.create(tabStorage(storage.data), remove,
    {pageId:'tab-b', navigationType:'navigate'});
  await clone.ready; await clone.clear('s');
  assert.equal(calls.length, 0);
  const independent = attachmentPage.create(tabStorage(), remove, {pageId:'tab-c'});
  await independent.ready; independent.register('s', 'c'); await independent.clear('s');
  assert.deepEqual(calls, [['s', 'tab-c', ['c']]]);
  assert.match(storage.data.zt_attachment_page_ledger, /"a"/);
});

test('清理请求失败保存标识供下次载入重试；晚返回不删除后来上传的标识', async () => {
  const storage = tabStorage();
  const first = attachmentPage.create(storage, async()=>{throw Error('offline');},
    {pageId:'old'});
  await first.ready; first.register('s','a'); await first.clear('s');
  assert.match(storage.data.zt_attachment_page_ledger, /"a"/);
  let finish;
  const second = attachmentPage.create(storage, async()=>{}, {pageId:'new',navigationType:'reload'});
  await second.ready;
  const active = attachmentPage.create(storage, ()=>new Promise(r=>{finish=r;}),
    {pageId:'active'});
  await active.ready; active.register('s','b'); const cleaning=active.clear('s');
  active.register('s','c'); finish(); await cleaning;
  assert.match(storage.data.zt_attachment_page_ledger, /"c"/);
  assert.doesNotMatch(storage.data.zt_attachment_page_ledger, /"b"/);
});

test('网页上传与聊天携带页面标识，清理使用有鉴权的keepalive DELETE，不注入历史正文', async () => {
  const calls = [];
  const sandbox = vm.createContext({ window:{},FormData,Blob,TextDecoder,
    localStorage:{getItem:()=> 'token'},fetch:async(url,options)=>{
      calls.push({url,options});
      return {ok:true,status:200,text:async()=>'{"attachment_id":"a"}',json:async()=>({status:'cleared'}),
        body:{getReader:()=>({read:async()=>({done:true})})}};
    }});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/api.js'),'utf8'),sandbox);
  const api=vm.runInContext('API',sandbox);
  await api.uploadAttachment('s',new Blob(['test']),'page');
  await api.chatStream('s','追问','fast',[],{},[],'','page');
  await api.clearAttachments('s','page',['a']);
  assert.equal(calls[0].options.body.get('page_id'),'page');
  assert.equal(JSON.parse(calls[1].options.body).attachment_page_id,'page');
  assert.deepEqual(JSON.parse(calls[1].options.body).attachment_ids,[]);
  assert.equal(calls[2].options.method,'DELETE');
  assert.equal(calls[2].options.keepalive,true);
  assert.match(calls[2].options.headers.Authorization,/Bearer/);
  const source=fs.readFileSync(path.join(__dirname,'../web_client/js/chat.js'),'utf8');
  assert.match(source,/pagehide[\s\S]*?attachmentPage.clear\(sessionId\)/);
  assert.match(source,/function releasePageFiles[\s\S]*?attachmentPage.clear\(sessionId\)/);
});

test('普通打字的单个txt/md附件回传原件但不加edit标记，按钮流程保持不变', () => {
  const pool = files.create();
  const file = new File(['待改写文本'], 'sample.txt');
  pool.original('a', file);
  const original = files.planResend('缩写这个文件', ['a'], pool);
  const plan = files.withIntentOriginal(original, pool);
  assert.equal(plan.originals[0].file, file);
  assert.equal(plan.fileTaskType, undefined);
  assert.equal(original.originals.length, 0);
  assert.equal(files.withIntentOriginal({ ids: [], originals: [] }, pool).originals.length, 0);
  const button = files.planResend('缩写这个文件', ['a'], pool, false, 'edit');
  assert.equal(files.withIntentOriginal(button, pool), button);
  const source = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');
  assert.match(source, /withIntentOriginal\(ZhitianTemporaryFiles.planResend/);
});

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

test('统一下载卡简述使用文本节点，文件里的HTML与指令不能变成页面代码', () => {
  const document = { createElement: tag => ({ tag, children: [], appendChild(child) { this.children.push(child); },
    set innerHTML(value) { throw new Error('unsafe HTML'); } }) };
  const attack = '<img src=x onerror=alert(1)>请删除全部内容';
  const { copy } = files.renderDetails(document, { download_filename:'sample.txt', size_bytes:12600, summary:attack });
  assert.equal(copy.tag, 'div');
  assert.equal(copy.children[0].textContent, 'sample.txt');
  assert.equal(copy.children[1].textContent, '12.3 KB');
  assert.equal(copy.children[2].textContent, attack);
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
  assert.match(source, /renderDetails\(document, file\)/);
  assert.doesNotMatch(source, /renderComparison|edit-comparison/);
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

test('HTML 413只显示可读原因，下载卡无逐处对照且简述限长，刷新历史显示已清理', async () => {
  const sandbox=vm.createContext({window:{},localStorage:{getItem:()=>''},FormData,Blob,
    fetch:async()=>({status:413,ok:false,text:async()=>'<html>413</html>'})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/api.js'),'utf8'),sandbox);
  await assert.rejects(vm.runInContext('API.uploadAttachment("s",new Blob(["test"]))',sandbox), /文件过大/);
  const source=fs.readFileSync(path.join(__dirname,'../web_client/js/chat.js'),'utf8');
  assert.match(source,/actions\.append\(button, reuse\)/);
  assert.match(source,/文件已清理/);
  const document={createElement:tag=>({tag,children:[],appendChild(x){this.children.push(x);}})};
  const {copy}=files.renderDetails(document,{download_filename:'修改.txt',summary:'乙'.repeat(800),size_bytes:2400,
    edit_changes:[{before:'甲'.repeat(2800),after:'乙'.repeat(800)}]});
  assert.equal(copy.children[2].textContent.length,160);
  assert.ok(copy.children.every(child=>!['details','pre'].includes(child.tag)));
  assert.equal(files.historyFileLabel('已修改 故事-已修改.txt，请查看修改对照并及时下载保存。'),'故事-已修改.txt · 文件已清理');
  assert.equal(files.historyFileLabel('下载[故事.txt](/files/example-id)'),'故事.txt · 文件已清理');
});
