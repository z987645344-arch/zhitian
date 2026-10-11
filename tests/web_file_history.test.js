const test = require('node:test');
const assert = require('node:assert/strict');
const files = require('../web_client/js/temporary-files.js');

test('刷新后的文件名支持新正文及旧下载格式，不显示内部路径', () => {
  const samples = [
    ['文件已生成：项目说明.md', '项目说明.md'],
    ['Office生成失败，已降级。\n文件已生成：项目说明.md', '项目说明.md'],
    ['文件已生成：项目说明.md\n下载地址：/files/old-id', '项目说明.md'],
    ['已生成 报告.pdf，可通过 /files/old-id 下载', '报告.pdf'],
    ['下载[原文件.docx](/files/old-id)', '原文件.docx'],
    ['已修改 故事-已修改.txt，请及时下载保存。', '故事-已修改.txt'],
  ];
  for (const [body, name] of samples) {
    assert.equal(files.historyFileLabel(body), `${name} · 文件已清理`);
    assert.ok(!files.historyFileLabel(body).includes('/files/'));
  }
});
