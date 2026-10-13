// 注销UI只传当前密码与验证码；不持久化敏感字段。
(() => {
  const notice = document.querySelector('#accountLifecycleNotice');
  if (notice) {
    const text = sessionStorage.getItem('zt_web_login_notice');
    if (text) {
      notice.textContent = text;
      notice.hidden = false;
      sessionStorage.removeItem('zt_web_login_notice');
    }
  }
  const form = document.querySelector('#accountDeletionForm');
  if (!form) return;
  const password = document.querySelector('#deletionPassword');
  const code = document.querySelector('#deletionCode');
  const send = document.querySelector('#sendDeletionCode');
  const submit = document.querySelector('#submitAccountDeletion');
  const cancel = document.querySelector('#cancelAccountDeletion');
  const message = document.querySelector('#accountDeletionMessage');
  let busy = false;
  function setBusy(value) {
    busy = value;
    [password, code, send, submit, cancel].forEach(item => { item.disabled = value; });
  }
  cancel.addEventListener('click', () => {
    if (busy) return;
    password.value = ''; code.value = ''; message.textContent = '';
  });
  send.addEventListener('click', async () => {
    if (busy) return;
    setBusy(true);
    try {
      await API.sendDeletionCode();
      message.textContent = '注销验证码已发送，请查收邮箱。';
    } catch (error) { message.textContent = briefError(error); }
    finally { setBusy(false); }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (!password.value || !/^\d{6}$/.test(code.value.trim())) {
      message.textContent = '请填写当前密码和 6 位邮箱验证码。'; return;
    }
    if (!window.confirm('确认申请注销账号？所有登录会立即失效，7 天后生效；冷静期内重新登录即撤销。')) return;
    setBusy(true);
    try {
      const result = await API.requestAccountDeletion(password.value, code.value.trim());
      API.logout();
      sessionStorage.setItem('zt_web_notice', result.detail + (result.confirmation_email_sent ? '' : '；确认邮件暂未发送成功，系统将重试。'));
      location.replace('./login.html');
    } catch (error) { message.textContent = briefError(error); }
    finally { password.value = ''; code.value = ''; setBusy(false); }
  });
})();
