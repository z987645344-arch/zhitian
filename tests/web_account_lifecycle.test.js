const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '../web_client', file), 'utf8');

function environment(confirm = true, error = false) {
  const elements = {};
  for (const id of ['accountDeletionForm', 'deletionPassword', 'deletionCode', 'sendDeletionCode',
    'submitAccountDeletion', 'cancelAccountDeletion', 'accountDeletionMessage']) {
    elements[id] = { value: '', disabled: false, textContent: '', handlers: {},
      addEventListener(kind, handler) { this.handlers[kind] = handler; } };
  }
  const calls = { code: 0, request: [], logout: 0, location: [], notices: {} };
  const context = vm.createContext({
    document: { querySelector: query => elements[query.slice(1)] || null },
    window: { confirm: () => confirm },
    sessionStorage: { setItem: (key, value) => { calls.notices[key] = value; } },
    location: { replace: value => calls.location.push(value) },
    briefError: error => error.message,
    API: { sendDeletionCode: async () => { calls.code++; },
      requestAccountDeletion: async (...args) => { calls.request.push(args); if (error) throw Error('未通过校验');
        return { detail: '注销申请已提交', confirmation_email_sent: true }; },
      logout: () => calls.logout++ },
  });
  vm.runInContext(read('js/account-lifecycle.js'), context);
  elements.deletionPassword.value = 'SyntheticPassword123!';
  elements.deletionCode.value = '123456';
  return { elements, calls, context };
}

test('设置页有注销卡片、密码和验证码，取消或拒绝确认不发请求', async () => {
  const html = read('settings.html');
  assert.match(html, /注销账号/);
  for (const id of ['accountDeletionForm', 'deletionPassword', 'deletionCode']) assert.match(html, new RegExp(`id="${id}"`));
  const env = environment(false);
  await env.elements.accountDeletionForm.handlers.submit({ preventDefault() {} });
  env.elements.cancelAccountDeletion.handlers.click();
  assert.equal(env.calls.request.length, 0);
  assert.equal(env.calls.code, 0);
  assert.equal(env.elements.deletionPassword.value, '');
});

test('发送验证码和申请各一次，成功退出且不持久化密码验证码', async () => {
  const env = environment();
  await env.elements.sendDeletionCode.handlers.click();
  await env.elements.accountDeletionForm.handlers.submit({ preventDefault() {} });
  assert.equal(env.calls.code, 1);
  assert.deepEqual(env.calls.request, [['SyntheticPassword123!', '123456']]);
  assert.equal(env.calls.logout, 1);
  assert.deepEqual(env.calls.location, ['./login.html']);
  assert.equal(env.elements.deletionPassword.value, '');
  assert.equal(env.elements.deletionCode.value, '');
  assert.doesNotMatch(JSON.stringify(env.calls.notices), /SyntheticPassword|123456/);
});

test('失败保持登录、显示原因、恢复按钮，未填写有效验证码零请求', async () => {
  const env = environment(true, true);
  env.elements.deletionCode.value = 'bad';
  await env.elements.accountDeletionForm.handlers.submit({ preventDefault() {} });
  assert.equal(env.calls.request.length, 0);
  env.elements.deletionCode.value = '123456';
  await env.elements.accountDeletionForm.handlers.submit({ preventDefault() {} });
  assert.equal(env.calls.logout, 0);
  assert.match(env.elements.accountDeletionMessage.textContent, /未通过校验/);
  assert.equal(env.elements.submitAccountDeletion.disabled, false);
});

test('撤销提示从登录返回带入聊天页，使用纯文本且只显示一次', async () => {
  const elements = { loginForm: {}, message: {}, loginButton: {}, password: { value: 'SyntheticPassword123!' },
    username: { value: 'synthetic@example.com' }, togglePassword: {} };
  for (const item of Object.values(elements)) item.addEventListener = (kind, callback) => { item[kind] = callback; };
  const storage = {};
  const saved = [];
  const context = vm.createContext({ document: { querySelector: name => elements[name.slice(1)] },
    sessionStorage: { getItem: name => storage[name] || null, setItem: (name, value) => { storage[name] = value; },
      removeItem: name => { delete storage[name]; } },
    API: { login: async () => ({ token: 'synthetic-token', detail: '已撤销注销申请' }), saveSession: (...args) => saved.push(args) },
    location: { replace() {} }, briefError: error => error.message });
  vm.runInContext(read('js/login.js'), context);
  await elements.loginForm.submit({ preventDefault() {} });
  assert.equal(storage.zt_web_login_notice, '已撤销注销申请');
  const notice = { textContent: '', hidden: true };
  context.document = { querySelector: name => name === '#accountLifecycleNotice' ? notice : null };
  vm.runInContext(read('js/account-lifecycle.js'), context);
  assert.equal(notice.textContent, '已撤销注销申请');
  assert.equal(notice.hidden, false);
  assert.equal(storage.zt_web_login_notice, undefined);
  assert.equal(saved.length, 1);
});

test('注销API仅发送规定字段并附鉴权，四页共享更新后的缓存参数', async () => {
  const requests = [];
  const context = vm.createContext({ window: {}, localStorage: { getItem: key => key === 'zt_web_username' ? 'synthetic@example.com' : 'synthetic-token' },
    fetch: async (url, options) => { requests.push([url, options]);
      return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' }; } });
  vm.runInContext(read('js/api.js'), context);
  const api = vm.runInContext('API', context);
  await api.sendDeletionCode();
  await api.requestAccountDeletion('SyntheticPassword123!', '123456');
  assert.equal(requests[0][0], '/api/auth/send-verification-code');
  assert.deepEqual(JSON.parse(requests[0][1].body), { email: 'synthetic@example.com', purpose: 'customer_delete_account' });
  assert.equal(requests[1][0], '/api/account/deletion');
  assert.deepEqual(JSON.parse(requests[1][1].body), { current_password: 'SyntheticPassword123!', verification_code: '123456' });
  assert.ok(requests.every(([, options]) => options.headers.Authorization === 'Bearer synthetic-token'));
  const versions = ['chat.html', 'settings.html', 'login.html', 'register.html'].map(file => read(file).match(/js\/api\.js\?v=([^"]+)/)[1]);
  assert.equal(new Set(versions).size, 1);
  assert.match(versions[0], /lifecycle=20261013/);
});
