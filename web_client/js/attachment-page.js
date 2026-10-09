/* 仅保存本标签页的附件标识，不保存正文。刷新及切换后主动清理。 */
(function (root) {
  'use strict';
  const KEY = 'zt_attachment_page_ledger';
  function create(storage, remove, options = {}) {
    // 页面标识不是鉴权凭证；兼容没有randomUUID的HTTP开发页面，不阻断普通聊天。
    const pageId = options.pageId || (typeof root.crypto?.randomUUID === 'function'
      ? root.crypto.randomUUID() : `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`);
    const navigationType = options.navigationType || root.performance?.getEntriesByType('navigation')[0]?.type || 'navigate';
    let ledger = [];
    try { ledger = JSON.parse(storage.getItem(KEY) || '[]'); } catch (_) { /* 无正文 */ }
    if (!Array.isArray(ledger)) ledger = [];
    const persist = () => storage.setItem(KEY, JSON.stringify(ledger));
    // 新标签页可能复制 opener 的 sessionStorage。新导航丢弃继承标识，绝不删除
    // 它们；刷新/历史恢复才清理本标签页上一页面的标识，不依赖标签页应答时限。
    if (navigationType === 'navigate') { ledger = []; persist(); }
    async function erase(entry) {
      const ids = [...entry.ids];
      try {
        for (let index = 0; index < ids.length; index += 100)
          await remove(entry.sessionId, entry.pageId, ids.slice(index, index + 100));
        ledger = ledger.map(item => item.sessionId === entry.sessionId && item.pageId === entry.pageId
          ? {...item, ids: item.ids.filter(id => !ids.includes(id))} : item).filter(item => item.ids.length);
        persist();
        return true;
      } catch (_) { return false; } // 保存待清理标识，下次页面载入再试；TTL 是最后兜底。
    }
    const ready = (async () => {
      const inherited = ledger.slice();
      for (const entry of inherited) {
        if (!entry || typeof entry.pageId !== 'string' || !Array.isArray(entry.ids)) continue;
        await erase(entry);
      }
    })();
    return {pageId, ready,
      register(sessionId, id) {
        let entry = ledger.find(item => item.pageId === pageId && item.sessionId === sessionId);
        if (!entry) { entry = {pageId, sessionId, ids: []}; ledger.push(entry); }
        if (!entry.ids.includes(id)) entry.ids.push(id);
        persist();
      },
      clear(sessionId) {
        return Promise.all(ledger.filter(item => item.pageId === pageId && item.sessionId === sessionId).map(erase));
      },
    };
  }
  const api = {create};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ZhitianAttachmentPage = api;
})(typeof window !== 'undefined' ? window : globalThis);
