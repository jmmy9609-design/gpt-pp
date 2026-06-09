# Plus PayPal 0 元提链控制台 (ChatGPT 提取支付链接)

> **💡 欢迎大家提交 Pull Request / Issue，共同共建与完善本项目！**
> 
> 💬 **技术交流 QQ 群**：**808987383**（入群欢迎共同探讨协议与支付安全技术）

> **⚠️ 特别致谢与外泄说明**
> 
> 本项目因前期仓促部署上线，导致源码意外泄露。在此，**特别感谢某位热心的大佬为我们上了生动的一课**。大神的“指点”不仅促使我们对项目进行了彻底的安全审计、配置参数化脱敏和系统架构加固，也让我们更加认识到开源共享的意义。
> 
> 现我们决定将本项目完全开源，供广大网络协议、支付系统防风控及住宅网络代理领域的开发者共同交流与学习。

---

## 📖 项目简介

`ChatGPT 提取支付链接` 是一个基于 **Zero-Amount**（零额隔离）安全风控核对逻辑的 PayPal 智能收单转化网关。
系统能够实时拦截并提取 Stripe Hosted Checkout 会话中的关键上下文，针对 PayPal 渠道自动完成商户核对，高自动、零款安全地转化提取 PayPal 授权长链接。

### 🌟 核心特性
- **零额核对防御**：强制顺序校验，只允许符合 Zero-Amount 规则 of 会话提交，拦截非零额异常变动。
- **智能住宅代理轮询**：后台静默调度多区域（首选日区/欧区）动态家宽住宅代理，当代理连接失效或遭对端拦截时，自动换 IP 重试。
- **双引擎支持**：支持轻量级 Python HTTP 网关与高性能 Go 多并发代理网关（位于 [cmd/ppgateway/main.go](cmd/ppgateway/main.go)）。
- **防克隆盾牌**：内嵌 Clickjacking 盾牌，在 [webapp/static/index.html](webapp/static/index.html) 中实现了 frame 逃逸校验，防止网页恶意 iframe 嵌套。

---

## 🛠 架构设计与核心模块

项目主要分为网关服务、测试集、自动化部署三大部分：

1. **网关核心 (Python 版)**：[webapp/server.py](webapp/server.py)
   - 负责前端面板渲染、JWT Access Token 提取解析、网关事务状态管理及代理 preflight 检测。
2. **Go 并发网关 (高性能版)**：[cmd/ppgateway/main.go](cmd/ppgateway/main.go)
   - 配合 [internal/gateway/server.go](internal/gateway/server.go) 服务，提供秒级高并发提链分流处理能力。
3. **自动化一键部署**：
   - 提供 [deploy_server.py](deploy_server.py)（Python 网关部署）和 [deploy_go_gateway.py](deploy_go_gateway.py)（Go 网关部署）。可以通过配套的 [diagnose.py](diagnose.py) 进行生产环境诊断。
4. **单元测试与门禁**：[tests/test_zero_gate.py](tests/test_zero_gate.py)
   - 包含零额过滤逻辑、代理地理胜率算法、多账号串行排队机制的完整自动化测试。
5. **自动化探测工具**：[tools/pp_batch_probe.py](tools/pp_batch_probe.py)
   - 提供商户通道和代理连接胜率的批量压力和吞吐探测支持。

---

## 🚀 快速开始

## 📦 一键本地启动包说明

项目根目录下附带了自动化启动脚本，专为零基础或希望快速启动服务的开发者设计。

### 🚀 使用方式
- **macOS / Linux 系统**：
  在终端中执行以下命令（将自动运行 [start.sh](start.sh)）：
  ```bash
  chmod +x start.sh
  ./start.sh
  ```
- **Windows 系统**：
  直接双击根目录下的 [start.bat](start.bat) 批处理文件即可。

### ⚙️ 脚本自动化工作流程
1. **智能环境备份**：脚本将首先检查项目根目录下是否存在 `.env` 配置文件。若不存在，会自动复制 `.env.example` 并重命名为 `.env`。
2. **隔离虚拟环境**：在项目目录内自动创建并初始化 Python 虚拟环境（`.venv` 目录），确保所有运行时依赖库与您电脑的主系统环境物理隔离，避免依赖冲突。
3. **依赖自动补齐**：自动在此虚拟环境下静默增量更新 `pip` 包管理器，并安装核心通信引擎 `curl_cffi`（提供高强度 JA3/JA4/Akamai 指纹浏览器仿冒）以及 `playwright` 自动化测试库。
4. **浏览器套件配置**：自动下载和配置 Playwright 专用的 Headless Chromium 浏览器组件。
5. **自动打开浏览器**：网关服务监听启动成功后，脚本会静默发送指令，自动调用您的默认浏览器并打开本地网关管理后台：`http://127.0.0.1:8888`。

---

## 🚀 手动调试与运行

如果您倾向于纯手动或在容器内进行按部就班的调试：

### 1. 复制环境配置
将根目录下的 `.env.example` 复制为 `.env`：
```bash
cp .env.example .env
```
根据提示配置您的远程服务器 IP、SSH 登录凭证、允许 of 域名列表以及代理设置。

### 2. 手动运行 Python 控制台
推荐在已安装 `curl_cffi` 和 `playwright` 的 Python 3.10+ 环境下运行：
```bash
python3 webapp/server.py --host 127.0.0.1 --port 8888
```
打开浏览器访问：`http://127.0.0.1:8888`

### 3. 使用 Go 高并发网关
若需要承载批量并发交易，可编译并运行 Go 网关：
```bash
go build -o ppgateway ./cmd/ppgateway/main.go
./ppgateway -addr 127.0.0.1:8787 -static webapp/static
```

---

## 🔧 环境变量说明

| 变量名 | 说明 | 默认/示例值 |
| :--- | :--- | :--- |
| `DEPLOY_HOST` | 部署的目标主机服务器 IP | `your_server_ip` |
| `DEPLOY_USER` | 部署服务器 SSH 用户名 | `root` |
| `DEPLOY_PASSWORD` | 部署服务器 SSH 密码/密钥口令 | `your_ssh_password` |
| `DEPLOY_DOMAIN` | 部署绑定的服务主域名 | `yourdomain.com` |
| `ALLOWED_DOMAINS` | 控制台可访问域名限制（以逗号分隔） | `yourdomain.com,example.com` |
| `SERVER_PUBLIC_IP` | 本网关公网 IP（辅助代理白名单配置提示）| `your_server_ip` |

---

## 🧪 单元测试

运行以下指令执行网关逻辑的离线单元测试，确保逻辑正确性：
```bash
python3 -m unittest discover -s tests -v
```

---

## ⚖️ 免责声明
本项目开源仅作为安全审计、网络协议分析和接口防欺诈学术研究之用。任何组织和个人不得将本项目用于非法欺诈、绕过风控收费等违反法律或第三方服务条款的商业场景。作者对因此引发的任何安全事故或法律纠纷不承担任何责任。

---

## 📈 趋势与统计 (Stats)

<p align="left">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=jmmy9609-design.gpt-pp" alt="Visitors" />
</p>

[![Star History Chart](https://api.star-history.com/svg?repos=jmmy9609-design/gpt-pp&type=Date)](https://star-history.com/#jmmy9609-design/gpt-pp&Date)

