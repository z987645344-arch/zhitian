// customer网页客户端的后端调用封装。
// 仅覆盖customer角色能力范围：自助注册、登录、对话、聊天附件与个人文件下载。
// 不包含文档上传、知识库录入、审批等企业角色接口。
const API = (() => {
  const configuredUrl = window.ZHITIAN_CONFIG?.apiBaseUrl;
  const backendUrl = (
    typeof configuredUrl === 'string' && configuredUrl.trim()
      ? configuredUrl.trim()
      : '/api'
  ).replace(/\/+$/, '');

  // 与管理后台一致使用localStorage保存token。安全取舍：localStorage对XSS无
  // 抵抗力，理论上HttpOnly Cookie更稳妥，但后端当前是无状态JWT、未签发Cookie
  // 也未做CSRF防护，单方面改用Cookie需要后端配套改动，超出本批"不改后端"的
  // 约束。因此沿用现有方式，并在此标注为已知取舍，待后续统一评估。
  const TOKEN_KEY = 'zt_web_token';
  const ROLE_KEY = 'zt_web_role';
  const NAME_KEY = 'zt_web_username';
  const CHAT_SESSION_KEY = 'zt_web_session_id';

  function token() {
    return localStorage.getItem(TOKEN_KEY) || '';
  }

  function currentUsername() {
    return localStorage.getItem(NAME_KEY) || '';
  }

  function saveSession(authToken, username) {
    localStorage.setItem(TOKEN_KEY, authToken);
    localStorage.setItem(ROLE_KEY, 'customer');
    localStorage.setItem(NAME_KEY, username);
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(ROLE_KEY);
    localStorage.removeItem(NAME_KEY);
    localStorage.removeItem(CHAT_SESSION_KEY);
  }

  function headers(json = true) {
    const result = {};
    if (json) result['Content-Type'] = 'application/json';
    const authToken = token();
    if (authToken) result.Authorization = `Bearer ${authToken}`;
    return result;
  }

  function responseFilename(contentDisposition, fallbackFilename) {
    const value = String(contentDisposition || '');
    const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) {
      try {
        return decodeURIComponent(utf8Match[1]);
      } catch (error) {
        // 编码异常时继续尝试普通filename，避免让下载本身失败。
      }
    }
    const plainMatch = value.match(/filename="?([^";]+)"?/i);
    return plainMatch?.[1] || fallbackFilename || '知天生成文件';
  }

  async function request(path, options = {}) {
    const { skipAuthRedirect = false, json = options.body !== undefined, ...rest } = options;
    const response = await fetch(`${backendUrl}${path}`, {
      ...rest,
      headers: { ...headers(json), ...(rest.headers || {}) },
    });
    if (response.status === 401 && !skipAuthRedirect) {
      logout();
      sessionStorage.setItem('zt_web_notice', '登录已过期，请重新登录');
      location.replace('./login.html');
      throw new Error('登录已过期，请重新登录');
    }
    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    if (!response.ok) {
      const error = new Error(data.detail || `请求失败：HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  return {
    backendUrl,
    token,
    currentUsername,
    saveSession,
    logout,
    request,

    login: (username, password) => request('/auth/login', {
      method: 'POST',
      skipAuthRedirect: true,
      body: JSON.stringify({ username, password, role: 'customer' }),
    }),

    // customer自助注册用途，后端不要求企业密码
    sendCode: (email) => request('/auth/send-verification-code', {
      method: 'POST',
      skipAuthRedirect: true,
      body: JSON.stringify({ email, purpose: 'customer_register' }),
    }),

    register: (username, password, verificationCode) => request('/auth/register', {
      method: 'POST',
      skipAuthRedirect: true,
      body: JSON.stringify({
        username,
        password,
        role: 'customer',
        verification_code: verificationCode,
      }),
    }),

    // 与Flutter端一致显式传入fast/expert；后端仍负责校验合法模式。
    chat: (sessionId, message, mode, attachmentIds) => request('/chat', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sessionId,
        message,
        mode,
        attachment_ids: attachmentIds || [],
      }),
    }),

    getSessions: async () => {
      const data = await request('/memory/sessions');
      return Array.isArray(data.sessions) ? data.sessions : [];
    },

    getHistory: async (sessionId) => {
      const data = await request(`/memory/${encodeURIComponent(sessionId)}`);
      return Array.isArray(data.history) ? data.history : [];
    },

    deleteSession: async (sessionId) => {
      const data = await request(`/memory/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'DELETE',
        json: false,
      });
      return data.deleted === true;
    },

    getApiQuotaStatus: () => request('/account/api-quota'),

    authorizeEnterpriseQuota: (enterprisePassword) => request(
      '/account/api-quota/enterprise/authorize',
      {
        method: 'POST',
        body: JSON.stringify({ enterprise_password: enterprisePassword }),
      },
    ),

    savePersonalQuotaKey: (deepseekApiKey) => request('/account/api-quota/personal', {
      method: 'PUT',
      body: JSON.stringify({ deepseek_api_key: deepseekApiKey }),
    }),

    clearPersonalQuotaKey: () => request('/account/api-quota/personal', {
      method: 'DELETE',
      json: false,
    }),

    selectApiQuotaSource: (source) => request('/account/api-quota/source', {
      method: 'PUT',
      body: JSON.stringify({ source }),
    }),

    async uploadAttachment(sessionId, file) {
      const form = new FormData();
      form.append('session_id', sessionId);
      form.append('file', file);
      const response = await fetch(`${backendUrl}/chat/attachments`, {
        method: 'POST',
        headers: headers(false),
        body: form,
      });
      const text = await response.text();
      const data = text ? JSON.parse(text) : {};
      if (!response.ok) {
        throw new Error(data.detail || data.error_type || `上传失败：HTTP ${response.status}`);
      }
      return data;
    },

    async downloadFile(fileId, fallbackFilename) {
      const response = await fetch(`${backendUrl}/files/${encodeURIComponent(fileId)}`, {
        method: 'GET',
        headers: headers(false),
      });
      if (response.status === 401) {
        logout();
        sessionStorage.setItem('zt_web_notice', '登录已过期，请重新登录');
        location.replace('./login.html');
        throw new Error('登录已过期，请重新登录');
      }
      if (!response.ok) {
        const text = await response.text();
        let detail = `下载失败：HTTP ${response.status}`;
        try {
          const parsed = text ? JSON.parse(text) : {};
          detail = parsed.detail || detail;
        } catch (error) {
          // 非JSON错误体只显示脱敏后的状态码，不回显服务端原文。
        }
        throw new Error(detail);
      }
      return {
        blob: await response.blob(),
        filename: responseFilename(
          response.headers.get('Content-Disposition'),
          fallbackFilename,
        ),
      };
    },

    // 流式对话：后端SSE载荷有四类业务形状——
    //   {"chunk": "片段"} 逐段正文，以 {"chunk": "[DONE]"} 结束
    //   {"type": "citations", "citations": [...]} 引用来源
    //   {"type": "file", "file_id": "...", ...} 生成文件交付信息
    //   {"error": "..."} 服务端异常
    async chatStream(sessionId, message, mode, attachmentIds, handlers) {
      const response = await fetch(`${backendUrl}/chat/stream`, {
        method: 'POST',
        headers: headers(true),
        body: JSON.stringify({
          session_id: sessionId,
          message,
          mode,
          attachment_ids: attachmentIds || [],
        }),
      });
      if (response.status === 401) {
        logout();
        location.replace('./login.html');
        throw new Error('登录已过期，请重新登录');
      }
      if (!response.ok) {
        const text = await response.text();
        let detail = `请求失败：HTTP ${response.status}`;
        try {
          const parsed = text ? JSON.parse(text) : {};
          detail = parsed.detail || detail;
        } catch (error) {
          // 非JSON错误体保留原始状态码提示
        }
        throw new Error(detail);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop() || '';
        for (const block of blocks) {
          const line = block.split('\n').find((item) => item.startsWith('data: '));
          if (!line) continue;
          let payload;
          try {
            payload = JSON.parse(line.slice(6));
          } catch (error) {
            continue;
          }
          if (payload.error) {
            handlers.onError?.(payload.error);
            return;
          }
          if (payload.type === 'file') {
            const fileId = typeof payload.file_id === 'string' ? payload.file_id.trim() : '';
            const downloadFilename = typeof payload.download_filename === 'string'
              ? payload.download_filename.trim()
              : '';
            if (fileId && downloadFilename) {
              handlers.onFile?.({
                file_id: fileId,
                download_filename: downloadFilename,
                file_type: typeof payload.file_type === 'string' ? payload.file_type.trim() : '',
              });
            }
            continue;
          }
          if (payload.type === 'citations') {
            handlers.onCitations?.(payload.citations || []);
            continue;
          }
          if (payload.chunk === '[DONE]') {
            handlers.onDone?.();
            return;
          }
          if (payload.reasoning) {
            handlers.onReasoning?.(payload.reasoning);
            continue;
          }
          if (typeof payload.chunk === 'string' && payload.chunk) {
            handlers.onChunk?.(payload.chunk);
          }
        }
      }
      handlers.onDone?.();
    },
  };
})();

function briefError(error) {
  const text = String(error?.message || error || '操作失败');
  return text.length > 120 ? `${text.slice(0, 120)}...` : text;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}
