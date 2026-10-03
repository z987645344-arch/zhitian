// 零依赖 Markdown：先转义全部输入，只生成白名单标签；未闭合标记按文本显示。
(function (root) {
  'use strict';
  function escape(value) {
    return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function inline(text, depth = 0) {
    if (depth > 12) return text;
    let result = '', i = 0;
    while (i < text.length) {
      if (text[i] === '`') {
        const end = text.indexOf('`', i + 1);
        if (end > i + 1) { result += '<code>' + text.slice(i + 1, end) + '</code>'; i = end + 1; continue; }
      }
      if (text[i] === '[') {
        const match = text.slice(i).match(/^\[([^\]\n]+)\]\(([^\s)]+)\)/);
        if (match) {
          const href = match[2];
          if (/^https?:\/\/[^/\s]+/i.test(href)) {
            result += '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' + inline(match[1], depth + 1) + '</a>';
          } else result += inline(match[1], depth + 1);
          i += match[0].length; continue;
        }
      }
      const delimiter = text.startsWith('***', i) ? '***' : text.startsWith('**', i) ? '**' : text[i] === '*' ? '*' : '';
      if (delimiter) {
        const end = text.indexOf(delimiter, i + delimiter.length);
        if (end > i + delimiter.length) {
          const content = inline(text.slice(i + delimiter.length, end), depth + 1);
          result += delimiter === '***' ? '<strong><em>' + content + '</em></strong>'
            : delimiter === '**' ? '<strong>' + content + '</strong>' : '<em>' + content + '</em>';
          i = end + delimiter.length; continue;
        }
      }
      result += text[i++];
    }
    return result;
  }
  function renderMarkdown(value) {
    const lines = escape(value || '').replace(/\r\n?/g, '\n').split('\n');
    let output = '', list = '', paragraph = [], code = null;
    const flush = () => { if (paragraph.length) { output += '<p>' + paragraph.map(line => inline(line)).join('<br>') + '</p>'; paragraph = []; } };
    const closeList = () => { if (list) { output += '</' + list + '>'; list = ''; } };
    for (const line of lines) {
      if (/^\s*```/.test(line)) {
        flush(); closeList();
        if (code !== null) { output += '<pre><code>' + code.join('\n') + '</code></pre>'; code = null; }
        else code = [];
        continue;
      }
      if (code !== null) { code.push(line); continue; }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      const item = line.match(/^\s*(?:([-*])\s+|(\d+)\.\s+)(.+)$/);
      if (heading) { flush(); closeList(); const h = heading[1].length; output += '<h' + h + '>' + inline(heading[2]) + '</h' + h + '>'; }
      else if (item) { flush(); const type = item[2] ? 'ol' : 'ul'; if (list !== type) { closeList(); list = type; output += '<' + type + '>'; } output += '<li>' + inline(item[3]) + '</li>'; }
      else if (!line.trim()) { flush(); closeList(); }
      else { closeList(); paragraph.push(line); }
    }
    flush(); closeList();
    if (code !== null) output += '<pre><code>' + code.join('\n') + '</code></pre>';
    return output;
  }
  root.ZhitianMarkdown = { renderMarkdown };
  if (typeof module !== 'undefined') module.exports = root.ZhitianMarkdown;
})(typeof window !== 'undefined' ? window : globalThis);
