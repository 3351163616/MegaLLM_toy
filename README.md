# MegaLLM 自动注册工具

自动化注册 MegaLLM 账号的 Python 脚本，支持代理池管理、并发注册、邀请码池等功能。

## 功能特性

- ✅ **临时邮箱**: 自动获取临时邮箱地址
- ✅ **自动注册**: 自动完成账号注册流程
- ✅ **邮箱验证**: 自动轮询邮箱获取验证码并完成验证
- ✅ **代理池管理**: 基于 Clash API 的智能代理池
  - 自动从 Clash API 获取代理节点列表
  - 并发健康检查，快速过滤不可用节点
  - 自动故障转移，失败节点自动剔除
  - 代理状态持久化存储
- ✅ **并发注册**: 支持多任务并发执行，提高效率
- ✅ **邀请码池**: 可选的邀请码池功能
  - 自动收集新注册账号的邀请码
  - 随机选择邀请码进行注册
  - 邀请码持久化存储
- ✅ **重试机制**: 注册失败自动重试
- ✅ **数据导出**: 自动保存账号信息到 CSV 文件

## 环境要求

- Python 3.7+
- Clash 代理客户端 (如 Clash Verge)
- 依赖库:
  - requests

## 安装

1. 克隆仓库
```bash
git clone <repository-url>
cd small-toy
```

2. 安装依赖
```bash
pip install requests
```

或使用 uv:
```bash
uv sync
```

3. 配置 Clash
   - 确保 Clash 已启动
   - 开启 Clash API (External Controller)
   - 记录 API 地址和密钥

## 配置说明

编辑 `config.json` 文件:

```json
{
  "email_base": "https://mail.chatgpt.org.uk",
  "referral_code": "",
  "api_base": "https://megallm.io",

  "account": {
    "password": "your-password"
  },

  "clash": {
    "api_url": "http://127.0.0.1:9097",
    "secret": "your-clash-secret",
    "local_proxy": "http://127.0.0.1:7897"
  },

  "proxy_pool": {
    "health_check_interval": 300,
    "max_failures": 3,
    "concurrent_tasks": 5,
    "test_url": "http://www.gstatic.com/generate_204"
  },

  "referral_pool": {
    "enabled": true,
    "initial_codes": []
  },

  "retry": {
    "max_retries": 5,
    "retry_delay": 30
  },

  "email_polling": {
    "timeout": 600,
    "interval": 5
  }
}
```

### 配置项详解

#### 基础配置
- `email_base`: 临时邮箱 API 地址
- `referral_code`: 固定邀请码 (邀请码池禁用时使用)
- `api_base`: MegaLLM API 地址

#### 账号配置
- `password`: 注册账号使用的固定密码

#### Clash 配置
- `api_url`: Clash External Controller 地址
- `secret`: Clash API 密钥 (在 Clash 配置中查看)
- `local_proxy`: Clash 本地代理端口

#### 代理池配置
- `health_check_interval`: 定时健康检查的间隔时间 (秒)
- `max_failures`: 节点失败多少次后被剔除
- `concurrent_tasks`: 同时运行的注册任务数量
- `test_url`: 用于测试代理连通性的 URL

#### 邀请码池配置
- `enabled`: `true` 启用邀请码池 / `false` 使用固定邀请码
- `initial_codes`: 初始邀请码列表

#### 重试配置
- `max_retries`: 注册失败最大重试次数
- `retry_delay`: 重试间隔时间 (秒)

#### 邮件轮询配置
- `timeout`: 邮件轮询超时时间 (秒)
- `interval`: 轮询间隔 (秒)

## 使用方法

```bash
python main.py
```

或使用 uv:
```bash
uv run main.py
```

程序启动后会:
1. 加载配置
2. 初始化代理池
3. 执行启动前健康检查
4. 开始并发注册任务

## 输出文件

- `accounts.csv`: 注册成功的账号信息 (邮箱、密码、API Key)
- `referral_pool.json`: 邀请码池数据 (启用邀请码池时)
- `proxy_pool_state.json`: 代理池状态数据

## 注意事项

1. **代理要求**
   - 必须安装并运行 Clash 客户端
   - 确保 Clash API 已启用
   - 代理节点需要能访问目标网站

2. **并发配置**
   - 根据代理数量和网络情况调整 `concurrent_tasks`
   - 建议从较小的值开始测试 (如 3-5)

3. **敏感信息**
   - 不要将 `config.json` 提交到公开仓库
   - 不要分享包含真实数据的 `accounts.csv`
   - Clash API 密钥应妥善保管

## 常见问题

### 1. 提示"没有可用的代理节点"
- 检查 Clash 是否正常运行
- 检查 Clash API 配置是否正确
- 检查代理节点是否可用

### 2. 注册失败提示 "Too many signup attempts"
- 降低 `concurrent_tasks` 数值
- 增加 `retry_delay` 延迟时间
- 更换代理节点

### 3. 未收到验证邮件
- 增加 `email_polling.timeout` 超时时间
- 检查临时邮箱服务是否正常

## 许可证

MIT License

## 免责声明

本工具仅供学习和研究使用，请遵守相关网站的服务条款。使用本工具产生的任何后果由使用者自行承担。
