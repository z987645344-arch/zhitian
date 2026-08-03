// 登录页逻辑：customer角色固定，不提供角色选择。
(() => {
  const form = document.querySelector('#loginForm');
  const message = document.querySelector('#message');
  const button = document.querySelector('#loginButton');
  const passwordInput = document.querySelector('#password');

  const notice = sessionStorage.getItem('zt_web_notice');
  if (notice) {
    message.textContent = notice;
    message.className = 'message error';
    sessionStorage.removeItem('zt_web_notice');
  }

  document.querySelector('#togglePassword').addEventListener('click', (event) => {
    const shown = passwordInput.type === 'text';
    passwordInput.type = shown ? 'password' : 'text';
    event.currentTarget.textContent = shown ? '显示' : '隐藏';
    event.currentTarget.setAttribute('aria-pressed', String(!shown));
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const username = document.querySelector('#username').value.trim();
    const password = passwordInput.value;
    message.textContent = '登录中...';
    message.className = 'message';
    button.disabled = true;
    try {
      const data = await API.login(username, password);
      API.saveSession(data.token, username);
      location.replace('./chat.html');
    } catch (error) {
      message.textContent = briefError(error);
      message.className = 'message error';
      button.disabled = false;
    }
  });
})();
