// 运行时后端地址配置，与管理后台config.js同一模式。
// 生产环境默认使用同源/api；本地开发可改为http://localhost:8000。
// 不要把地址硬编码进js/api.js，容器交付时只需替换本文件。
window.ZHITIAN_CONFIG = {
  apiBaseUrl: '/api',
};
