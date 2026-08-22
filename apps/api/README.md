# Flyweave API

最小 FastAPI 后端应用。

## 前置要求

- Python 3.11+
- pip

## 本地开发设置

### 1. 创建虚拟环境

从仓库根目录：

```bash
python -m venv .venv
```

### 2. 激活虚拟环境

Windows (Git Bash):
```bash
source .venv/Scripts/activate
```

Linux/macOS:
```bash
source .venv/bin/activate
```

### 3. 安装依赖

```bash
cd apps/api
pip install -r requirements.txt
```

## 启动应用

### 方式 1: 直接运行

```bash
cd apps/api
python main.py
```

### 方式 2: 使用 uvicorn

```bash
cd apps/api
uvicorn main:app --reload
```

应用将在 http://localhost:8000 启动。

## 验证健康检查

访问 http://localhost:8000/health 应该返回：

```json
{
  "status": "healthy",
  "app_name": "Flyweave API",
  "environment": "development"
}
```

## 运行测试

```bash
cd apps/api
pytest
```

## 环境配置

应用配置通过环境变量驱动。可选配置项：

- `APP_NAME`: 应用名称（默认: "Flyweave API"）
- `APP_ENV`: 环境名称（默认: "development"）
- `API_HOST`: 监听地址（默认: "0.0.0.0"）
- `API_PORT`: 监听端口（默认: 8000）

创建 `.env` 文件或设置环境变量来覆盖默认值。
