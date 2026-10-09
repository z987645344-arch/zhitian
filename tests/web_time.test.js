const test = require('node:test');
const assert = require('node:assert/strict');
process.env.TZ = 'Asia/Shanghai';
const { parseTimestamp, formatLocalTime } = require('../web_client/js/time.js');

test('Asia/Shanghai: 带Z和旧UTC-naive都显示10:46', () => {
  assert.equal(new Date('2026-09-30T02:46:13Z').getTimezoneOffset(), -480);
  const legacy = '2026-09-30T02:46:13';
  const tagged = legacy + 'Z';
  assert.equal(formatLocalTime(legacy), formatLocalTime(tagged));
  assert.match(formatLocalTime(tagged), /10:46/);
  assert.equal(formatLocalTime(tagged), formatLocalTime('2026-09-30T10:46:13+08:00'));
  assert.equal(formatLocalTime(tagged), formatLocalTime('2026-09-30 02:46:13'));
  console.log(tagged + ' / ' + legacy + ' -> ' + formatLocalTime(tagged));
});

test('跨日、空值、非法值和显式偏移都按同一口径处理', () => {
  assert.match(formatLocalTime('2026-09-30T20:46:13Z'), /10[\/\-]01.*04:46/);
  assert.equal(parseTimestamp('2026-09-30T02:46:13').toISOString(), '2026-09-30T02:46:13.000Z');
  assert.equal(formatLocalTime('invalid'), '-');
  assert.equal(formatLocalTime(null), '-');
  assert.equal(formatLocalTime('', {}, '时间未知'), '时间未知');
  assert.equal(parseTimestamp('2026-09-30'), null);
});

const fs = require('node:fs');
const path = require('node:path');

test('四个页面包含同一版权行并更新样式缓存', () => {
  for (const page of ['login', 'register', 'chat', 'settings']) {
    const html = fs.readFileSync(path.join(__dirname, `../web_client/${page}.html`), 'utf8');
    assert.equal(html.split('© 2026 知了 · 保留所有权利</footer>').length - 1, 1);
    assert.match(html, /<footer class="[^"]*copyright-line/);
    assert.match(html, /style\.css\?v=copyright-20261009/);
  }
});

test('robots仅限制指定训练爬虫且镜像复制为可直接访问的静态文件', () => {
  const read = name => fs.readFileSync(path.join(__dirname, '../web_client', name), 'utf8');
  const robots = read('robots.txt');
  const agents = [...robots.matchAll(/^User-agent: (.+)$/gm)].map(m => m[1].trim());
  assert.deepEqual(agents, ['GPTBot', 'CCBot', 'Google-Extended', 'ClaudeBot', 'anthropic-ai', 'Bytespider', 'Applebot-Extended', 'meta-externalagent']);
  assert.deepEqual(robots.trim().split(/\r?\n/).slice(8), ['Disallow: /']);
  assert.doesNotMatch(robots, /sitemap|https?:|www\.|[\w-]+\.[a-z]{2,}/i);
  assert.match(read('Dockerfile'), /COPY --chown=nginx:nginx robots\.txt \/usr\/share\/nginx\/html\/robots\.txt/);
  assert.match(read('nginx.conf'), /location \/ \{\s*try_files \$uri =404;/);
});
test('显示和排序都经统一入口，页面实际加载该脚本', () => {
  for (const page of ['chat', 'settings']) {
    const html = fs.readFileSync(path.join(__dirname, '../web_client/' + page + '.html'), 'utf8');
    assert.ok(html.indexOf('./js/time.js?') < html.indexOf('./js/' + page + '.js?'));
    const js = fs.readFileSync(path.join(__dirname, '../web_client/js/' + page + '.js'), 'utf8');
    assert.doesNotMatch(js, /new Date\(value\)|Date\.parse\(/);
    assert.doesNotMatch(js, /\$\{(?:item|status)\.(?:last_active|created_at|enterprise_password_locked_until)\}/);
    assert.match(js, /ZhitianTime\.formatLocalTime\(/);
  }
});
