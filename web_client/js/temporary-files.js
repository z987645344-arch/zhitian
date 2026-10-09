// 页面内存专用：不写localStorage、IndexedDB或缓存API。
const ZhitianTemporaryFiles = (() => {
  const TTL = 60 * 60 * 1000;
  function create(now = () => Date.now()) {
    const originals = new Map(), products = new Map();
    function purge() {
      for (const pool of [originals, products]) {
        for (const [id, entry] of pool) if (entry.expires <= now()) pool.delete(id);
      }
    }
    return {
      original(id, file) { originals.set(id, { file, expires: now() + TTL }); },
      getOriginal(id) { purge(); return originals.get(id)?.file; },
      originalIds() { purge(); return Array.from(originals.keys()); },
      product(id, blob, filename) { products.set(id, { blob, filename, saved: false, expires: now() + TTL }); },
      getProduct(id) { purge(); return products.get(id); },
      saved(id) { const item = products.get(id); if (item) item.saved = true; },
      hasUnsaved() { purge(); return Array.from(products.values()).some(item => !item.saved); },
      clear() { originals.clear(); products.clear(); },
      purge,
    };
  }
  const ORIGINAL_CLEARED = '原件已清理，请重新上传后再转换';
  function planResend(text, pendingIds, pool, hasFileHistory = false, taskType = '') {
    const ids = [...pendingIds];
    const processing = taskType === 'edit' || /转换|转为|转成|编辑|修改文件|修改文档|文件格式|导出为|另存为|转(?:pdf|docx|xlsx|pptx)/i.test(text);
    if (!processing) return { ids, originals: [] };
    if (!ids.length) {
      const available = pool.originalIds();
      if (available.length === 1) ids.push(available[0]);
      else if (available.length > 1) return { error: '请先选择要处理的文件，或重新上传该文件' };
      // 没有文件的普通聊天不能仅因含“转换”等词被拦住。
      else if (!hasFileHistory) return { ids, originals: [] };
    }
    const originals = ids.map(id => ({ id, file: pool.getOriginal(id) }));
    if (!originals.length || originals.some(item => !item.file)) return { error: taskType === 'edit' ? '原件已清理，请重新上传后再编辑' : ORIGINAL_CLEARED };
    if (taskType === 'edit') {
      if (originals.length !== 1) return { error: '请只选择一个 txt 或 md 文件进行编辑' };
      if (!/\.(txt|md)$/i.test(originals[0].file.name || '')) return { error: '目前只支持编辑 txt / md 文件' };
      return { ids, originals, fileTaskType: 'edit' };
    }
    return { ids, originals };
  }
  function renderComparison(document, file) {
    const panel = document.createElement('details');
    const title = document.createElement('summary');
    title.textContent = `修改对照（${(file.edit_changes || []).length}处，点击展开改前 / 改后）`;
    panel.className = 'edit-comparison';
    panel.appendChild(title);
    for (const change of file.edit_changes || []) {
      const block = document.createElement('pre');
      block.textContent = (change.action === 'insert_after' ? `插入位置：${change.anchor}\n` : '') +
        `改前：${change.before || '（空）'}\n改后：${change.after || '（删除）'}`;
      panel.appendChild(block);
    }
    if ((file.edit_issues || []).length) {
      const notice = document.createElement('p');
      notice.textContent = '部分操作未完成，请结合回答中的原因核对后再修改。';
      panel.appendChild(notice);
    }
    return panel;
  }
  function continuationIsEdit(file) {
    return /\.(txt|md)$/i.test(file.download_filename || '');
  }
  function historyFileLabel(content) {
    const text = String(content || '');
    const linked = text.match(/\[([^\]]+)\]\(\/files\/[^)]+\)/);
    const edited = text.match(/^已修改 (.+\.(?:txt|md))，/);
    const name = linked?.[1] || edited?.[1] || text.replace(/\/files\/[\w/-]+/g, '').trim() || '文件';
    return `${name} · 文件已清理`;
  }
  return { create, planResend, renderComparison, continuationIsEdit, historyFileLabel, ORIGINAL_CLEARED };
})();
if (typeof module !== 'undefined') module.exports = ZhitianTemporaryFiles;
