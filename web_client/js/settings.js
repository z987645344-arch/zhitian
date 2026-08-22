// customer API额度设置。个人Key仅存在于当前输入框和单次请求体中，绝不持久化到浏览器。
(() => {
  if (!API.token()) {
    location.replace('./login.html');
    return;
  }

  const sourceSummary = document.querySelector('#sourceSummary');
  const message = document.querySelector('#settingsMessage');
  const enterpriseCard = document.querySelector('#enterpriseCard');
  const enterpriseBadge = document.querySelector('#enterpriseBadge');
  const enterpriseForm = document.querySelector('#enterpriseForm');
  const enterprisePassword = document.querySelector('#enterprisePassword');
  const enterpriseHint = document.querySelector('#enterpriseHint');
  const enterpriseSubmit = document.querySelector('#enterpriseSubmit');
  const enterpriseSelect = document.querySelector('#enterpriseSelect');
  const personalCard = document.querySelector('#personalCard');
  const personalBadge = document.querySelector('#personalBadge');
  const personalForm = document.querySelector('#personalForm');
  const personalKey = document.querySelector('#personalKey');
  const personalSubmit = document.querySelector('#personalSubmit');
  const personalSelect = document.querySelector('#personalSelect');
  const personalClear = document.querySelector('#personalClear');
  const interactive = [
    enterprisePassword,
    enterpriseSubmit,
    enterpriseSelect,
    personalKey,
    personalSubmit,
    personalSelect,
    personalClear,
  ];

  let status = null;
  let busy = false;

  document.querySelector('#currentUser').textContent = API.currentUsername() || '-';

  function setBusy(nextBusy) {
    busy = nextBusy;
    interactive.forEach((element) => {
      element.disabled = busy;
    });
  }

  function setMessage(text, failed = false) {
    message.textContent = text || '';
    message.className = failed
      ? 'settings-message error'
      : 'settings-message success';
  }

  function formatLockTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  }

  function render(nextStatus) {
    status = nextStatus;
    const enterpriseSelected = status.source === 'enterprise';
    const personalSelected = status.source === 'personal';
    enterpriseCard.classList.toggle('selected', enterpriseSelected);
    personalCard.classList.toggle('selected', personalSelected);

    if (enterpriseSelected) {
      sourceSummary.textContent = '当前使用：企业额度';
    } else if (personalSelected) {
      sourceSummary.textContent = '当前使用：个人 DeepSeek Key';
    } else {
      sourceSummary.textContent = '尚未选择额度来源，完成下方任一设置后才能开始对话。';
    }

    enterpriseBadge.textContent = status.enterprise_authorized
      ? (enterpriseSelected ? '正在使用' : '已授权')
      : '未授权';
    enterpriseBadge.classList.toggle('active', enterpriseSelected);
    enterprisePassword.hidden = status.enterprise_authorized;
    enterpriseSubmit.hidden = status.enterprise_authorized;
    enterpriseForm.querySelector('label').hidden = status.enterprise_authorized;
    enterpriseSelect.hidden = !status.enterprise_authorized || enterpriseSelected;

    const lockedUntil = formatLockTime(status.enterprise_password_locked_until);
    if (lockedUntil) {
      enterpriseHint.textContent = `当前账号已锁定，请在 ${lockedUntil} 后再试。`;
      enterprisePassword.disabled = true;
      enterpriseSubmit.disabled = true;
    } else if (!status.enterprise_authorized) {
      enterpriseHint.textContent = `连续失败 5 次后，仅当前账号会锁定 12 小时；当前还可尝试 ${status.enterprise_password_attempts_remaining} 次。`;
      enterprisePassword.disabled = busy;
      enterpriseSubmit.disabled = busy;
    } else {
      enterpriseHint.textContent = '此账号已完成一次性授权，可随时手动切回企业额度。';
    }

    personalBadge.textContent = status.personal_key_configured
      ? (personalSelected ? '正在使用' : '已配置')
      : '未配置';
    personalBadge.classList.toggle('active', personalSelected);
    personalSelect.hidden = !status.personal_key_configured || personalSelected;
    personalClear.hidden = !status.personal_key_configured;
  }

  async function refreshStatus() {
    setBusy(true);
    try {
      render(await API.getApiQuotaStatus());
    } catch (error) {
      setMessage(briefError(error), true);
    } finally {
      setBusy(false);
      if (status) render(status);
    }
  }

  enterpriseForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy || status?.enterprise_authorized) return;
    const password = enterprisePassword.value.trim();
    if (!/^\d{8}$/.test(password)) {
      setMessage('请输入 8 位企业流动密码。', true);
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      render(await API.authorizeEnterpriseQuota(password));
      setMessage('企业额度已授权并启用。');
    } catch (error) {
      setMessage(briefError(error), true);
      await refreshStatus();
    } finally {
      enterprisePassword.value = '';
      setBusy(false);
      if (status) render(status);
    }
  });

  enterpriseSelect.addEventListener('click', async () => {
    if (busy) return;
    setBusy(true);
    try {
      render(await API.selectApiQuotaSource('enterprise'));
      setMessage('已切换到企业额度。');
    } catch (error) {
      setMessage(briefError(error), true);
    } finally {
      setBusy(false);
      if (status) render(status);
    }
  });

  personalForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (busy) return;
    const key = personalKey.value.trim();
    if (!key) {
      setMessage('请输入个人 DeepSeek Key。', true);
      return;
    }
    setBusy(true);
    setMessage('');
    try {
      render(await API.savePersonalQuotaKey(key));
      setMessage('个人 Key 已加密保存，并已切换到个人额度。');
    } catch (error) {
      setMessage(briefError(error), true);
    } finally {
      personalKey.value = '';
      setBusy(false);
      if (status) render(status);
    }
  });

  personalSelect.addEventListener('click', async () => {
    if (busy) return;
    setBusy(true);
    try {
      render(await API.selectApiQuotaSource('personal'));
      setMessage('已切换到个人额度。');
    } catch (error) {
      setMessage(briefError(error), true);
    } finally {
      setBusy(false);
      if (status) render(status);
    }
  });

  personalClear.addEventListener('click', async () => {
    if (busy || !window.confirm('确定清除已保存的个人 Key？清除后无法恢复。')) return;
    setBusy(true);
    try {
      render(await API.clearPersonalQuotaKey());
      setMessage('个人 Key 已清除。若它原本是当前来源，请重新选择额度来源。');
    } catch (error) {
      setMessage(briefError(error), true);
    } finally {
      personalKey.value = '';
      setBusy(false);
      if (status) render(status);
    }
  });

  refreshStatus();
})();
