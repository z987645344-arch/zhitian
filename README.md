# 知天（zhitian）

## 项目简介

知天（zhitian）是本地私有化部署 Agent，支持对话、联网搜索、文档知识库、三角色权限管理。

## 环境要求

- Python 3.10.11
- Flutter（Windows桌面端）
- 浏览器（管理后台）

## 首次启动前必须配置

打开 `.env` 文件，填写：

```env
GLM_API_KEY=你的GLM API Key
TAVILY_API_KEY=你的Tavily API Key
JWT_SECRET_KEY=随机强密钥（建议32位以上随机字符串）
```

注意：`.env` 必须保存为无 BOM 的 UTF-8 格式。

## 三个项目的启动方式

后端：

```bat
cd D:\zhiliao\zhitian
.venv\Scripts\activate
python main.py
```

客户端：

```bat
cd D:\zhiliao\zhitian_app
flutter run -d windows
```

管理后台：

直接用浏览器打开：

```text
D:\zhiliao\zhitian_admin\index.html
```

## 三个角色说明

- customer（客户）：使用 Flutter 桌面端聊天
- employee（员工）：使用管理后台上传文档和录入知识
- reviewer（审核员）：使用管理后台审核文档、管理知识库

## 文档上传说明

管理后台使用浏览器文件上传，不需要填写服务器文件路径。

上传后的原始文件只会临时保存用于解析，解析完成后立即删除；长期保存的是 Chroma 文档 chunk、向量索引和 SQLite 审核记录。

## 首次使用：注册账号

调用 `POST /auth/register` 注册第一个 reviewer 账号。

后续由 reviewer 在管理后台管理其他账号（或继续调用接口注册）。

## 多机共用说明

同一局域网内，其他电脑把前端后端地址改为本机 `IP:8000` 即可共用数据。

跨网络需要将后端部署到公网服务器。

## 数据备份说明

代码备份：正常备份项目目录，`data/` 目录可单独处理。

数据备份：单独备份 `data/` 目录（包含 SQLite 和 Chroma 向量库）。

迁移到其他电脑：拷贝整个项目目录（含 `data/`）即可。
