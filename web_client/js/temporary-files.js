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
  function readableSize(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return '大小未知';
    if (bytes < 1024) return `${Math.floor(bytes)} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  function renderDetails(document, file) {
    const copy = document.createElement('div');
    copy.className = 'generated-file-copy';
    const title = document.createElement('strong');
    title.textContent = String(file.download_filename || '文件');
    const size = document.createElement('span');
    size.className = 'generated-file-size';
    size.textContent = readableSize(file.size_bytes);
    const summary = document.createElement('p');
    summary.className = 'generated-file-summary';
    summary.textContent = typeof file.summary === 'string'
      ? file.summary.replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 160) || '已按要求处理文件。'
      : '已按要求处理文件。';
    copy.appendChild(title); copy.appendChild(size); copy.appendChild(summary);
    return { copy, size };
  }
  function withIntentOriginal(plan, pool) {
    // 只按本轮附件格式提供原件；是否编辑仍由同一次工具选择判断，不匹配用户措辞。
    if (plan.error || plan.fileTaskType === 'edit' || plan.originals.length || plan.ids.length !== 1) return plan;
    const file = pool.getOriginal(plan.ids[0]);
    if (!file || !/\.(txt|md)$/i.test(file.name || '')) return plan;
    return { ...plan, originals: [{ id: plan.ids[0], file }] };
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
  return { create, planResend, withIntentOriginal, renderDetails, readableSize, continuationIsEdit, historyFileLabel, ORIGINAL_CLEARED };
})();
if (typeof module !== 'undefined') module.exports = ZhitianTemporaryFiles;
