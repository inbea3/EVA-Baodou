# EVA

EVA 是一个可自我进化的命令行 AI Agent。它通过 LLM 调用 `run_cli` 工具执行 Shell 命令完成任务，并在记忆接近上限时自动压缩对话、归档知识与技能。

可选的 `bot.py` 将 EVA 接入微信（基于 [wechatbot-sdk](https://github.com/corespeed-io/wechatbot)）。

## 功能特性

- 支持 **Windows（PowerShell）** 与 **Linux（Bash）**
- 流式输出，含 thinking 模型推理过程展示
- 命令安全审查：只读命令自动放行，写入/执行类命令需人工确认（可用 `-a` 跳过）
- 按工作目录自动保存/恢复会话
- 记忆压缩：接近 token 上限时触发归档与线索留存

## 环境要求

- Python 3.9+
- 兼容 OpenAI Chat Completions 的 API（默认 [DeepSeek](https://platform.deepseek.com/)）
- 建议使用 **thinking 模型**（如 `deepseek-reasoner`）

## 快速开始

### 1. 配置 API Key

```powershell
# Windows PowerShell
$env:EVA_API_KEY = "your-api-key"
$env:EVA_BASE_URL = "https://api.deepseek.com"
$env:EVA_MODEL_NAME = "deepseek-reasoner"
```

```bash
# Linux / macOS
export EVA_API_KEY="your-api-key"
export EVA_BASE_URL="https://api.deepseek.com"
export EVA_MODEL_NAME="deepseek-reasoner"
```

也可复制 `.env.example` 为 `.env` 后自行加载（本仓库不内置 dotenv，需配合 shell 或工具使用）。

### 2. 启动 EVA

```bash
python eva.py
```

进入交互模式后输入任务即可。Linux 下首次运行会自动在 `~/.local/bin/eva` 创建启动脚本。

### 3. 命令行参数

| 参数 | 说明 |
|------|------|
| `-a`, `--allow-all` | 所有 Shell 命令无需确认直接执行 |
| `-l`, `--list-session` | 列出已保存的会话 |
| `-c`, `--clear-session` | 清除当前目录对应的会话 |
| `-u`, `--user-ask TEXT` | 非交互模式，执行单条任务 |
| `-s`, `--with-session` | 配合 `-u` 使用，加载并保存会话 |
| `--until TEXT` | 配合 `-u` 使用，任务完成时需输出包含该子串的回复 |

示例：

```bash
python eva.py -u "列出当前目录文件" -s
python eva.py -a   # 开发调试：跳过命令确认
```

## 目录说明

```
EVA/
├── eva.py           # 主程序
├── bot.py           # 微信 Bot 入口（可选）
├── storage.py       # PostgreSQL / 文件双模式持久化
├── start.sh         # Railway / 生产环境启动脚本
├── railway.toml     # Railway 部署配置
├── runtime.txt      # Python 版本（Nixpacks）
├── EVA.md.example   # 固化知识/规则模板
├── .env.example     # 环境变量示例
└── requirements.txt # bot.py 依赖
```

运行时会在以下位置生成数据（已在 `.gitignore` 中排除，**请勿提交**）：

| 路径 | 用途 |
|------|------|
| `.eva/EVA.md` | 全局固化知识与规则 |
| `.eva/sessions/` | 各工作目录的会话快照 |
| `<项目>/.eva/hints.md` | 当前项目的记忆线索 |

首次使用前，可将 `EVA.md.example` 复制为 `.eva/EVA.md` 并按需编辑。

## Neon 数据库（推荐生产环境）

配置 `DATABASE_URL` 后，以下数据写入 **Neon PostgreSQL**，容器重启不丢失：

| 表名 | 内容 |
|------|------|
| `eva_sessions` | 对话会话（JSON） |
| `eva_hints` | 记忆压缩线索 |
| `eva_knowledge` | 固化知识 / 规则（EVA.md） |
| `wechat_credentials` | 微信登录凭证 |
| `eva_locks` | 并发锁（防重复启动） |

本地仍会镜像一份文件到 `.eva/`，供 EVA 执行 Shell 命令时读写；**数据库是权威来源**。

```bash
# Railway / Neon Variables 中设置（不要写进代码仓库）
DATABASE_URL=postgresql://user:pass@host/db?sslmode=require
```

未配置 `DATABASE_URL` 时自动回退到本地文件存储。

## 微信 Bot（可选）

```bash
pip install -r requirements.txt
python bot.py
```

首次运行需微信扫码登录。用户发送消息即触发 EVA 执行任务；发送 `/clear` 或 `clear` 清除当前目录会话。

## 部署到 Railway（长期运行）

微信 Bot 适合作为 **常驻 Worker** 部署在 [Railway](https://railway.app/) 上 24/7 运行。

### 1. 推送代码

将本仓库推送到 GitHub，然后在 Railway 创建项目并连接该仓库。

### 2. 配置环境变量

在 Railway 服务的 **Variables** 中至少设置：

| 变量 | 说明 |
|------|------|
| `EVA_API_KEY` | DeepSeek / 兼容 API 密钥（必填） |
| `DATABASE_URL` | Neon PostgreSQL 连接串（**强烈推荐**） |
| `EVA_BASE_URL` | 默认 `https://api.deepseek.com` |
| `EVA_MODEL_NAME` | 建议 `deepseek-reasoner` |

可选：

| 变量 | 说明 |
|------|------|
| `EVA_TASK_TIMEOUT` | 单次 EVA 任务超时秒数，默认 `600` |

### 3. Volume（可选）

配置了 `DATABASE_URL` 后，**可以不挂 Volume**，会话/凭证/知识库均存 Neon。

若未配置数据库，才需要挂载 Volume 到 `/app/data` 以保留数据。

### 4. 启动命令

仓库已包含 `railway.toml`，默认启动命令为：

```bash
bash start.sh
```

无需公网域名：在 **Settings → Networking** 中关闭 Public Networking（Bot 只需出站连接微信与 LLM API）。

### 5. 首次微信登录

部署完成后打开 **Deploy Logs**，搜索「请用微信扫描登录」，用手机微信扫码。凭证会同步到 Neon，后续重启无需重复扫码。

### 6. 本地模拟 Railway 启动

```bash
pip install -r requirements.txt
export EVA_API_KEY="your-api-key"
bash start.sh
```

数据会写入项目下的 `data/` 目录（已在 `.gitignore` 中排除）。

## 安全提示

- **切勿**将 API Key 写入代码或提交到 GitHub
- 生产环境请勿使用 `-a` / `--allow-all`
- `.eva/` 目录可能含会话历史与本地路径信息，上传前确认已忽略

## License

MIT（如与你计划不符，可自行添加 LICENSE 文件）
