# Codex 初始化指令
> 将此文档完整复制给Codex，作为项目启动的第一条指令

---

你是知天（zhitian）项目的编程执行者。开发前必须阅读 docs/zhitian_structure.md。每次完成改动后必须更新 CHANGELOG.md 和 docs/claude_memory.md。

## 基本编码规范

- 语言：Python 3.10
- 编码：UTF-8（所有文件头部加 `# -*- coding: utf-8 -*-`）
- 缩进：4个空格
- 换行：LF
- 注释：中文
- 引号：双引号优先
- 禁止硬编码API Key，统一从config.py读取

---

## 第一步：创建目录结构

在 D:\zhiliao\zhitian\ 下创建以下所有文件和文件夹：

```
zhitian/
├── docs/
│   ├── claude_memory.md
│   ├── zhitian_structure.md
│   └── codex_init_prompt.md
├── CHANGELOG.md
├── Dockerfile
├── main.py
├── requirements.txt
├── config.py
├── .env
├── layers/
│   ├── __init__.py
│   ├── perception.py
│   ├── memory.py
│   ├── planning.py
│   ├── execution.py
│   └── output.py
└── data/
    └── vectordb/
```

---

## 第二步：写入文件内容

**CHANGELOG.md**
```markdown
# 知天（zhitian）改动记录
> Codex每次完成改动后必须追加到此文件

## 2026-06-28
- 初始化项目完整目录结构
- 写入五层代码骨架
- FastAPI基础服务完成
- 写入三份docs文档
```

**requirements.txt**
```
fastapi==0.115.0
uvicorn==0.30.0
pydantic==2.7.0
zhipuai==2.1.5
langchain==0.2.0
langgraph==0.1.0
chromadb==0.5.0
tavily-python==0.3.3
python-dotenv==1.0.0
```

**.env**
```
GLM_API_KEY=你的GLM_API_KEY
TAVILY_API_KEY=你的TAVILY_API_KEY
PORT=8000
```

**config.py**
```python
# -*- coding: utf-8 -*-
# 配置中心：所有参数从环境变量读取，禁止硬编码

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
GLM_API_KEY = os.getenv("GLM_API_KEY", "")
LLM_MODEL = "glm-4-flash"

# 服务
PORT = int(os.getenv("PORT", 8000))
HOST = "0.0.0.0"

# 路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORDB_PATH = os.path.join(BASE_DIR, "data", "vectordb")
HISTORY_DB_PATH = os.path.join(BASE_DIR, "data", "history.db")

# 工具
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 记忆
MAX_HISTORY_LENGTH = 20
```

**main.py**
```python
# -*- coding: utf-8 -*-
# 知天（zhitian）FastAPI主入口

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import config

app = FastAPI(title="知天 Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    session_id: str
    message: str
    mode: str = "chat"

class ChatResponse(BaseModel):
    status: str
    data: str
    layer_trace: list[str] = []
    session_id: str

@app.get("/")
async def root():
    return {"message": "知天 Agent 运行中", "version": "0.1.0"}

@app.get("/health")
async def health():
    return {"status": "ok", "layers": ["perception", "memory", "planning", "execution", "output"]}

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # 第一阶段占位，后续逐层接入
    return ChatResponse(
        status="success",
        data=f"收到消息：{request.message}",
        layer_trace=["perception", "output"],
        session_id=request.session_id
    )

if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=True)
```

**layers/__init__.py**
```python
# -*- coding: utf-8 -*-
```

**layers/perception.py**
```python
# -*- coding: utf-8 -*-
# 感知层：接收并预处理用户输入

from pydantic import BaseModel
from datetime import datetime

class PerceptionInput(BaseModel):
    session_id: str
    raw_message: str
    mode: str = "chat"

class PerceptionOutput(BaseModel):
    session_id: str
    message: str
    input_type: str       # text | file | image
    mode: str
    timestamp: str

def process(input_data: PerceptionInput) -> PerceptionOutput:
    """将用户原始输入格式化为内部数据结构"""
    return PerceptionOutput(
        session_id=input_data.session_id,
        message=input_data.raw_message.strip(),
        input_type="text",
        mode=input_data.mode,
        timestamp=datetime.now().isoformat()
    )
```

**layers/memory.py**
```python
# -*- coding: utf-8 -*-
# 记忆层：短期SQLite对话历史 + 长期Chroma向量记忆
# 第二阶段实现，当前为占位结构

def save_message(session_id: str, role: str, content: str) -> None:
    """保存一条对话记录到SQLite"""
    pass

def get_history(session_id: str, limit: int = 10) -> list:
    """读取最近N轮对话历史"""
    return []

def save_to_vector(session_id: str, content: str, importance: str = "normal") -> None:
    """写入Chroma长期向量记忆"""
    pass

def search_memory(query: str, top_k: int = 3) -> list:
    """语义检索长期记忆"""
    return []

def clear_session(session_id: str) -> None:
    """清空会话记忆"""
    pass
```

**layers/planning.py**
```python
# -*- coding: utf-8 -*-
# 规划层：意图识别与任务编排
# LangGraph第三阶段接入，当前为简单关键词分类

def classify_intent(message: str) -> str:
    """意图分类：search / chat / file"""
    search_keywords = ["搜索", "查找", "查一下", "搜一下", "最新", "现在"]
    for kw in search_keywords:
        if kw in message:
            return "search"
    return "chat"

def plan_tasks(intent: str, message: str) -> list:
    """根据意图拆解任务列表"""
    if intent == "search":
        return [{"tool": "search_web", "params": {"query": message}, "order": 1}]
    return [{"tool": "llm_chat", "params": {"message": message}, "order": 1}]
```

**layers/execution.py**
```python
# -*- coding: utf-8 -*-
# 执行层：工具调用统一入口
# 第一阶段接入Tavily，当前search_web为占位

import config

# 工具注册表：新增工具在此注册
TOOL_REGISTRY = {
    "search_web": "_search_web",
    "llm_chat": "_llm_chat",
}

def run(tool: str, params: dict) -> dict:
    """统一工具调用入口"""
    if tool not in TOOL_REGISTRY:
        return {"status": "error", "data": "", "error_msg": f"未知工具：{tool}"}
    try:
        func = globals()[TOOL_REGISTRY[tool]]
        result = func(**params)
        return {"status": "success", "data": result, "error_msg": ""}
    except Exception as e:
        return {"status": "error", "data": "", "error_msg": str(e)}

def _search_web(query: str) -> str:
    """联网搜索（占位，第一阶段接入Tavily）"""
    return f"搜索结果占位：{query}"

def _llm_chat(message: str) -> str:
    """LLM对话（占位，第一阶段接入GLM）"""
    return f"LLM回复占位：{message}"
```

**layers/output.py**
```python
# -*- coding: utf-8 -*-
# 输出层：格式化最终响应

def format_response(session_id: str, data: str, layer_trace: list, status: str = "success") -> dict:
    """格式化最终响应"""
    return {
        "status": status,
        "data": data,
        "layer_trace": layer_trace,
        "session_id": session_id
    }

def format_error(session_id: str, error_msg: str, layer_trace: list) -> dict:
    """格式化错误响应"""
    return {
        "status": "error",
        "data": "服务暂时异常，请重试",
        "layer_trace": layer_trace,
        "session_id": session_id
    }
```

**Dockerfile**
```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY . .
CMD ["python", "main.py"]
```

---

## 第三步：安装依赖

```bash
cd D:\zhiliao\zhitian
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 第四步：启动验证

```bash
python main.py
```

验证以下两个接口：

```
GET  http://localhost:8000        → {"message": "知天 Agent 运行中", "version": "0.1.0"}
GET  http://localhost:8000/health → {"status": "ok", ...}
```

---

## 第五步：完成后更新文档

将 docs/claude_memory.md 中以下项目标记为完成：
```
- [x] Codex初始化执行（创建文件结构）
```

---

## 完成标准

- [ ] 所有文件和文件夹创建完毕
- [ ] pip install 无报错
- [ ] python main.py 启动无报错
- [ ] GET / 返回正确JSON
- [ ] GET /health 返回正确JSON
- [ ] CHANGELOG.md 已更新
- [ ] docs/claude_memory.md 进度已更新

完成后告知结果，等待下一步指令。

