# Plus PayPal 0-Amount Checkout Link Extractor (ChatGPT Extract Payment Link)

> **💡 Contributions via Pull Requests / Issues are highly welcome to co-create and improve this project!**
> 
> 💬 **Technical Discussion QQ Group**: **808987383** (Welcome to join and discuss protocol & payment security tech)

> **⚠️ Acknowledgements & Source Code Leak Statement**
> 
> Due to a rushed deployment in the early stage, the source code of this project was accidentally leaked. Here, we would like to **express our sincere gratitude to the expert (大佬) for giving us a vivid and valuable lesson**. The expert's "guidance" not only prompted us to conduct a thorough security audit, parameterize configurations for data desensitization, and reinforce the system architecture, but also made us deeply realize the value of open-source sharing.
> 
> Now, we have decided to fully open-source this project, offering a platform for developers in network protocols, payment anti-fraud systems, and residential proxies to communicate and learn together.

---

## 📖 Introduction

`ChatGPT Extract Payment Link` is a PayPal smart checkout conversion gateway based on the **Zero-Amount** security verification logic.
The system intercepts and extracts critical session context from Stripe Hosted Checkout, automatically completes merchant checks for the PayPal channel, and converts it into a high-security PayPal authorization long link.

### 🌟 Core Features
- **Zero-Amount Guard Checking**: Enforces sequential validation, only allowing sessions matching Zero-Amount rules to proceed, and blocks any non-zero abnormalities.
- **Intelligent Residential Proxy Rotation**: Silently dispatches residential proxies (preferring JP/EU zones) in the background. If a proxy connection fails or is blocked, it automatically rotates to another IP.
- **Dual-Engine Support**: Supports both a lightweight Python HTTP gateway and a high-performance Go concurrent gateway (located in [cmd/ppgateway/main.go](cmd/ppgateway/main.go)).
- **Clickjacking Protection**: Built-in frame escape validation inside [webapp/static/index.html](webapp/static/index.html) to prevent malicious iframe embedding.

---

## 🛠 Architecture & Directory Structure

The project is structured into three main parts: Gateway Service, Test Suite, and Automated Deployment:

1. **Core Gateway (Python)**: [webapp/server.py](webapp/server.py)
   - Handles dashboard UI rendering, JWT Access Token (e.g., OpenAI Session) parsing, transaction state management, and proxy preflight health checks.
2. **Go Gateway (High-Performance)**: [cmd/ppgateway/main.go](cmd/ppgateway/main.go)
   - Works with the [internal/gateway/server.go](internal/gateway/server.go) engine to provide concurrent link extraction and load balancing.
3. **Automated Deployment**:
   - Provides [deploy_server.py](deploy_server.py) (Python gateway deployment) and [deploy_go_gateway.py](deploy_go_gateway.py) (Go gateway deployment) with SSH environment diagnosis tool [diagnose.py](diagnose.py).
4. **Unit Tests**: [tests/test_zero_gate.py](tests/test_zero_gate.py)
   - Full offline suite validating the zero-guard checking logic, proxy geo-priority algorithms, and queue serialization.
5. **Batch Probe Tool**: [tools/pp_batch_probe.py](tools/pp_batch_probe.py)
   - Benchmarks gateway throughput, success rates, and latency.

---

## 📦 One-Click Local Startup Package

Startup scripts are provided in the repository to automatically set up the workspace and start the service with one click.

### 🚀 How to Run
- **macOS / Linux**:
  Execute the following command in your terminal (which runs [start.sh](start.sh)):
  ```bash
  chmod +x start.sh
  ./start.sh
  ```
- **Windows**:
  Double-click the [start.bat](start.bat) batch file in the file explorer.

### ⚙️ What the Script Does Automatically
1. **Config Auto-generation**: Copies `.env.example` to `.env` if not present.
2. **Virtual Environment Setup**: Initializes a Python 3 virtual environment (`.venv`) locally to avoid dependency contamination.
3. **Dependency Check**: Silently upgrades `pip` and installs `curl_cffi` (for Akamai/JA3/JA4 TLS fingerprint impersonation) and `playwright`.
4. **Chromium Installation**: Automatically fetches the required headless Chromium browser binary.
5. **Browser Open**: Launches your system's default browser and navigates to the gateway panel at `http://127.0.0.1:8888`.

---

## 🚀 Manual Startup & Debugging

If you prefer to start the server step-by-step or run it in a container:

### 1. Copy Configuration
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Fill in your remote server IP, SSH credentials, allowed domains, and residential proxies.

### 2. Manual Python Gateway
Run the service (Python 3.10+ recommended):
```bash
python3 webapp/server.py --host 127.0.0.1 --port 8888
```
Navigate to: `http://127.0.0.1:8888`

### 3. High-Performance Go Gateway
Build and launch:
```bash
go build -o ppgateway ./cmd/ppgateway/main.go
./ppgateway -addr 127.0.0.1:8787 -static webapp/static
```

---

## 🔧 Environment Variables Reference

| Variable | Description | Example / Default |
| :--- | :--- | :--- |
| `DEPLOY_HOST` | Remote deployment server IP | `your_server_ip` |
| `DEPLOY_USER` | Deployment server SSH username | `root` |
| `DEPLOY_PASSWORD` | Deployment server SSH password | `your_ssh_password` |
| `DEPLOY_DOMAIN` | Target domain name for deployment | `yourdomain.com` |
| `ALLOWED_DOMAINS` | Allowed domains for the console (comma-separated)| `yourdomain.com,example.com` |
| `SERVER_PUBLIC_IP` | Public IP of this server (for proxy whitelist guide) | `your_server_ip` |

---

## 🧪 Running Unit Tests

Run the unit tests locally to ensure gateway logic is correct:
```bash
python3 -m unittest discover -s tests -v
```

---

## ⚖️ Disclaimer
This project is open-sourced purely for security auditing, network protocol analysis, and anti-fraud academic research. No organization or individual shall use this project for fraudulent actions, payment bypasses, or any commercial activities violating third-party terms of service. The author is not liable for any legal consequences or security incidents arising from the use of this project.

---

## 📈 Trends & Stats

<p align="left">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=jmmy9609-design.gpt-pp" alt="Visitors" />
</p>

[![Star History Chart](https://api.star-history.com/svg?repos=jmmy9609-design/gpt-pp&type=Date)](https://star-history.com/#jmmy9609-design/gpt-pp&Date)

