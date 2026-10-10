const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const files = require('../web_client/js/temporary-files.js');

test('编辑、生成、转换实际接入同一下载卡，包含大小和纯文本简述，无修改对照', async () => {
  const source = fs.readFileSync(path.join(__dirname, '../web_client/js/chat.js'), 'utf8');
  const render = source.slice(source.indexOf('  function renderFileCard('), source.indexOf('  function renderHistory('));
  function element(tag) {
    return {tag, children: [], dataset: {}, isConnected: true,
      append(...items) { this.children.push(...items); }, appendChild(child) { this.children.push(child); },
      querySelector() { return null; }, setAttribute() {}, addEventListener() {},
      set innerHTML(_) { throw Error('HTML must not execute'); }};
  }
  for (const tool of ['edit_document', 'generate_file', 'convert_document']) {
    const bubble = element('div'), pool = files.create(), receipts = [];
    const sandbox = vm.createContext({document:{createElement:element}, CSS:{escape:x=>x},
      ZhitianTemporaryFiles:files, browserFiles:pool, sessionId:'s', scrollToBottom(){}, fileTypeLabel:()=> 'MD',
      API:{downloadFile:async()=>({blob:new Blob(['x'.repeat(12600)]),filename:'结果.md'}),
        acknowledgeFile:async id=>receipts.push(id)}});
    vm.runInContext(render, sandbox);
    sandbox.file = {file_id:tool,download_filename:'结果.md',file_type:'md',size_bytes:12600,
      summary:'<script>alert(1)</script>',edit_changes:[{before:'原文',after:'新文'}]};
    sandbox.bubble = bubble;
    vm.runInContext('renderFileCard(bubble, file)',sandbox);
    await new Promise(resolve=>setImmediate(resolve));
    const card = bubble.children[0], copy = card.children[1], actions=card.children[2];
    assert.equal(card.tag,'section');
    assert.equal(card.children.length,3);
    assert.equal(copy.children[0].textContent,'结果.md');
    assert.equal(copy.children[1].textContent,'12.3 KB');
    assert.equal(copy.children[2].textContent,'<script>alert(1)</script>');
    assert.equal(copy.children[3].textContent,'文件已生成　临时存储1小时，请及时保存');
    assert.deepEqual(actions.children.map(x=>x.textContent),['下载','继续处理']);
    assert.equal(actions.children[0].disabled,false);
    assert.deepEqual(receipts,[tool]);
    assert.ok(pool.getProduct(tool));
  }
});

test('SSE文件事件传递大小与简述，简述限长，未知大小不伪造', async () => {
  const event={type:'file',file_id:'id',download_filename:'结果.md',size_bytes:42,summary:'字'.repeat(250)};
  let read=false;
  const sandbox=vm.createContext({window:{},localStorage:{getItem:()=> 'token'},TextDecoder,
    fetch:async()=>({ok:true,body:{getReader:()=>({read:async()=>read?{done:true}:
      (read=true,{done:false,value:new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)})})}})});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web_client/js/api.js'),'utf8'),sandbox);
  const delivered=[];
  await vm.runInContext('API',sandbox).chatStream('s','test','expert',[],{onFile:f=>delivered.push(f)});
  assert.equal(delivered[0].size_bytes,42);
  assert.equal(delivered[0].summary.length,160);
  assert.equal(files.readableSize(undefined),'大小未知');
  assert.equal(files.readableSize(0),'0 B');
});
