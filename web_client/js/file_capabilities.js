// 附件提示与上传前校验：只使用后端的就绪状态和实际字节上限。
const ZhitianFileCapabilities = (() => {
  const OFFICE = ['doc', 'xls', 'xlsx', 'ppt', 'pptx'];
  const NATIVE = ['txt', 'md', 'pdf', 'docx'];
  const fallback = () => ({ officeReady: true, maxSizeMb: 5 });

  function parse(payload) {
    const engine = payload?.engines?.find(item => item.engine_name === 'libreoffice');
    if (!engine || !['pending', 'ready', 'failed'].includes(engine.status)) {
      throw new Error('文件能力响应不完整');
    }
    const size = Number(payload.max_upload_size_mb);
    return { officeReady: engine.status === 'ready',
      maxSizeMb: Number.isFinite(size) && size >= 0 ? size : fallback().maxSizeMb };
  }

  function hint(state) {
    const formats = state.officeReady
      ? '支持 txt / md / pdf / docx 及常见 Office 格式'
      : 'Office 格式（doc、xls、xlsx、ppt、pptx）暂时无法处理，txt、md、pdf、docx 不受影响';
    return `${formats}，单个文件不超过 ${state.maxSizeMb}MB。`;
  }

  function validate(file, state) {
    const ext = String(file.name || '').split('.').pop().toLowerCase();
    if (!state.officeReady && OFFICE.includes(ext)) {
      return 'Office 格式暂时无法处理，请稍后重试，或改用 txt、md、pdf、docx。';
    }
    if (file.size > state.maxSizeMb * 1024 * 1024) {
      return `这个文件 ${(file.size / 1024 / 1024).toFixed(1)}MB，超过了 ${state.maxSizeMb}MB 的上限，换个小一点的吧`;
    }
    return '';
  }

  function accept(state) {
    return (state.officeReady ? NATIVE.concat(OFFICE) : NATIVE).map(ext => '.' + ext).join(',');
  }

  return { fallback, parse, hint, validate, accept };
})();
