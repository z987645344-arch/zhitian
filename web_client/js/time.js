// API时间统一按本地时区显示；旧的无偏移时间一律按UTC解释。
(function (root) {
  function parseTimestamp(value) {
    if (!value) return null;
    const raw = String(value).trim().replace(' ', 'T');
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(raw)) return null;
    const tagged = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
    const date = new Date(tagged ? raw : raw + 'Z');
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function formatLocalTime(value, options = {}, empty = '-') {
    const date = parseTimestamp(value);
    if (!date) return empty;
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23', ...options,
    }).format(date);
  }

  const time = { parseTimestamp, formatLocalTime };
  if (typeof module !== 'undefined' && module.exports) module.exports = time;
  else root.ZhitianTime = time;
})(typeof globalThis !== 'undefined' ? globalThis : this);
