const test = require('node:test');
const assert = require('node:assert/strict');
const { renderMarkdown: render } = require('../web_client/js/markdown.js');
test('支持标题、强调、列表、代码、换行和安全链接', () => {
  assert.equal(render('# 标题\n\n**粗体** *斜体* ***嵌套***\n下一行'), '<h1>标题</h1><p><strong>粗体</strong> <em>斜体</em> <strong><em>嵌套</em></strong><br>下一行</p>');
  assert.equal(render('- 一\n- 二\n\n1. 三\n2. 四'), '<ul><li>一</li><li>二</li></ul><ol><li>三</li><li>四</li></ol>');
  assert.equal(render('`<img>`\n\n```html\n<script>\n```'), '<p><code>&lt;img&gt;</code></p><pre><code>&lt;script&gt;</code></pre>');
  assert.match(render('[链接](https://sample.invalid/a)'), /href="https:\/\/sample.invalid\/a" target="_blank" rel="noopener noreferrer"/);
});
test('XSS、事件属性、协议注入和嵌套星号不能产生可执行标签', () => {
  for (const input of ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', '**<img onerror=alert(1)>**', '***<script>***', '[x](javascript:alert(1))', '[x](data:text/html,abc)', '[x](jav&#x61;script:abc)']) {
    const html = render(input);
    assert.doesNotMatch(html, /<script|<img|href="(?:javascript|data):/i);
  }
  assert.equal(render('**未完成'), '<p>**未完成</p>');
  for (let n = 0; n <= '**完整**'.length; n++) {
    const html = render('**完整**'.slice(0, n));
    assert.equal((html.match(/<strong>/g) || []).length, (html.match(/<\/strong>/g) || []).length);
  }
  assert.doesNotMatch(render('[x](https://sample.invalid/"onerror="x)'), /href="[^"]*"onerror/);
});
