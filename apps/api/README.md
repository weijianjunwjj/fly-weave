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
../../.venv/Scripts/python.exe -m pytest
```

## 环境配置

应用配置通过环境变量驱动。可选配置项：

- `APP_NAME`: 应用名称（默认: "Flyweave API"）
- `APP_ENV`: 环境名称（默认: "development"）
- `API_HOST`: 监听地址（默认: "0.0.0.0"）
- `API_PORT`: 监听端口（默认: 8000）

### 必需的数据库配置

- `DATABASE_URL`: PostgreSQL 连接字符串（**必需**）

**格式**：
```
postgresql://username:password@host:port/database_name
```

**示例**：
```
DATABASE_URL=postgresql://flyweave:devpassword@localhost:5432/flyweave_dev
```

在 `apps/api/.env` 创建本机配置（可从 `.env.example` 复制）或设置环境变量。
应用始终从 `apps/api/.env` 读取本机默认配置，而不依赖启动目录；系统环境变量
`DATABASE_URL` 仍会优先覆盖该文件。DATABASE_URL 是唯一的数据库连接源，不支持硬编码凭据。

## 数据库设置

### 本地 PostgreSQL 开发环境

应用需要一个可访问的 PostgreSQL 实例。确保：

1. PostgreSQL 服务正在运行
2. 已创建目标数据库
3. DATABASE_URL 环境变量指向该实例

### 数据库迁移

本项目使用 Alembic 管理数据库 schema 迁移。

**生成新迁移**：
```bash
cd apps/api
../../.venv/Scripts/python.exe -m alembic revision --autogenerate -m "描述变更"
```

**应用迁移**：
```bash
cd apps/api
../../.venv/Scripts/python.exe -m alembic upgrade head
```

**查看迁移历史**：
```bash
cd apps/api
../../.venv/Scripts/python.exe -m alembic history
```

**回滚迁移**：
```bash
cd apps/api
../../.venv/Scripts/python.exe -m alembic downgrade -1
```

迁移脚本存储在 `migrations/versions/` 目录，由 Alembic 基于 `database.py` 中的 SQLAlchemy 模型自动生成。

## 数据库连接验证

运行数据库连接烟雾测试：

```bash
cd apps/api
../../.venv/Scripts/python.exe -m pytest tests/test_database.py -v
```

此测试会验证 DATABASE_URL 配置并执行真实的 PostgreSQL 连接检查。测试失败表明数据库配置或连接存在问题。
