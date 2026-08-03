// 注册页逻辑：邮箱验证码 → 自助注册 → 自动登录。
// 后端对customer_register用途不要求企业密码。
(() => {
  const form = document.querySelector('#registerForm');
  const message = document.querySelector('#message');
  const emailInput = document.querySelector('#email');
  const codeInput = document.querySelector('#verificationCode');
  const passwordInput = document.querySelector('#password');
  const sendButton = document.querySelector('#sendCodeButton');
  const registerButton = document.querySelector('#registerButton');

  const COOLDOWN_SECONDS = 180;
  let cooldownTimer = null;

  function setMessage(text, kind) {
    message.textContent = text;
    message.className = kind ? `message ${kind}` : 'message';
  }

  document.querySelector('#togglePassword').addEventListener('click', (event) => {
    const shown = passwordInput.type === 'text';
    passwordInput.type = shown ? 'password' : 'text';
    event.currentTarget.textContent = shown ? '显示' : '隐藏';
    event.currentTarget.setAttribute('aria-pressed', String(!shown));
  });

  function startCooldown() {
    let left = COOLDOWN_SECONDS;
    sendButton.disabled = true;
    sendButton.textContent = `${left}秒后可重发`;
    cooldownTimer = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(cooldownTimer);
        sendButton.disabled = false;
        sendButton.textContent = '获取验证码';
        return;
      }
      sendButton.textContent = `${left}秒后可重发`;
    }, 1000);
  }

  sendButton.addEventListener('click', async () => {
    const email = emailInput.value.trim();
    if (!email || !email.includes('@')) {
      setMessage('请先填写有效邮箱', 'error');
      return;
    }
    setMessage('正在发送验证码...', '');
    sendButton.disabled = true;
    try {
      await API.sendCode(email);
      setMessage('验证码已发送，5分钟内有效，请查收邮箱（含垃圾箱）', 'success');
      startCooldown();
    } catch (error) {
      setMessage(briefError(error), 'error');
      sendButton.disabled = false;
    }
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const email = emailInput.value.trim();
    const code = codeInput.value.trim();
    const password = passwordInput.value;
    setMessage('正在注册...', '');
    registerButton.disabled = true;
    try {
      await API.register(email, password, code);
      // 注册成功后直接用同一组凭据登录，省去用户再输一次
      const data = await API.login(email, password);
      API.saveSession(data.token, email);
      location.replace('./chat.html');
    } catch (error) {
      setMessage(briefError(error), 'error');
      registerButton.disabled = false;
    }
  });
})();
