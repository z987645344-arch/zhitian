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
  function planResend(text, pendingIds, pool, hasFileHistory = false) {
    const ids = [...pendingIds];
    const processing = /转换|转为|转成|编辑|修改文件|修改文档|文件格式|导出为|另存为|转(?:pdf|docx|xlsx|pptx)/i.test(text);
    if (!processing) return { ids, originals: [] };
    if (!ids.length) {
      const available = pool.originalIds();
      if (available.length === 1) ids.push(available[0]);
      else if (available.length > 1) return { error: '请先选择要处理的文件，或重新上传该文件' };
      // 没有文件的普通聊天不能仅因含“转换”等词被拦住。
      else if (!hasFileHistory) return { ids, originals: [] };
    }
    const originals = ids.map(id => ({ id, file: pool.getOriginal(id) }));
    if (!originals.length || originals.some(item => !item.file)) return { error: ORIGINAL_CLEARED };
    return { ids, originals };
  }
  return { create, planResend, ORIGINAL_CLEARED };
})();
if (typeof module !== 'undefined') module.exports = ZhitianTemporaryFiles;
