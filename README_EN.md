# Plus PayPal Zero-Amount Link Extractor (ChatGPT PayPal Link Extraction Console)

<div align="center">

🇺🇸 English &nbsp;|&nbsp; 🇨🇳 [中文](./README.md)

</div>

---

This project is a smart PayPal authorization link extraction gateway built on a **Zero-Amount isolation** risk-control model.
It intercepts and extracts key context from Stripe Hosted Checkout sessions in real time, performs automated merchant verification on the PayPal channel, and extracts long PayPal authorization links with **zero funds collected**.

> Use cases: Network protocol analysis · Payment risk-control research · Residential proxy scheduling · API reverse engineering

> 💡 **Pull Requests and Issues are welcome — let's build this together!**
> 
> 💬 **QQ Group (Tech Discussion)**: **808987383**

---

## 📖 Project Overview

`ChatGPT PayPal Link Extractor` is a PayPal smart acquisition conversion gateway based on **Zero-Amount isolation** security risk-control logic.

The system intercepts and extracts critical context from Stripe Hosted Checkout sessions in real time, performs automated merchant verification on the PayPal channel, and safely extracts PayPal authorization long-links with high automation and zero fund entry.

### 🌟 Core Features

- **Zero-Amount Defense**: Enforces sequential validation — only allows session submissions that comply with Zero-Amount rules; intercepts non-zero anomalies.
- **Smart Residential Proxy Rotation**: Silently schedules multi-region (Japan/EU preferred) dynamic residential proxies in the background; auto-retries with a new IP when a proxy fails or is blocked.
- **Dual-Engine Support**: Supports both a lightweight Python HTTP gateway and a high-performance Go concurrent proxy gateway ([cmd/ppgateway/main.go](cmd/ppgateway/main.go)).
- **Anti-Clone Shield**: Embedded Clickjacking shield implemented in [webapp/static/index.html](webapp/static/index.html) — detects and escapes malicious iframe nesting.

---

## 🛠 Architecture & Core Modules

The project is divided into three main components: gateway service, test suite, and automated deployment.

1. **Gateway Core (Python)**: [webapp/server.py](webapp/server.py)
   - Handles frontend panel rendering, JWT Access Token extraction/parsing, gateway transaction state management, and proxy preflight checks.
2. **Go Concurrent Gateway (High Performance)**: [cmd/ppgateway/main.go](cmd/ppgateway/main.go)
   - Works with [internal/gateway/server.go](internal/gateway/server.go) to provide second-level high-concurrency link extraction and traffic splitting.
3. **One-Click Automated Deployment**:
   - [deploy_server.py](deploy_server.py) (Python gateway deployment) and [deploy_go_gateway.py](deploy_go_gateway.py) (Go gateway deployment). Use [diagnose.py](diagnose.py) for production environment diagnostics.
4. **Unit Tests & Gate**: [tests/test_zero_gate.py](tests/test_zero_gate.py)
   - Full automated tests covering zero-amount filtering logic, proxy geo-win-rate algorithm, and multi-account serial queuing.
5. **Automated Probing Tool**: [tools/pp_batch_probe.py](tools/pp_batch_probe.py)
   - Batch stress and throughput probing for merchant channels and proxy connection win rates.

---

## 🚀 Quick Start

## 📦 One-Click Local Launch

An automated startup script is included in the project root, designed for beginners or developers who want to get the service running quickly.

### 🚀 Usage

- **macOS / Linux**:
  Run the following command in your terminal (automatically executes [start.sh](start.sh)):
  ```bash
  chmod +x start.sh
  ./start.sh
  ```
- **Windows**:
  Double-click [start.bat](start.bat) in the project root directory.

### ⚙️ Script Automation Workflow

1. **Smart Environment Backup**: Checks for `.env` in the project root. If not found, automatically copies `.env.example` and renames it to `.env`.
2. **Isolated Virtual Environment**: Automatically creates and initializes a Python virtual environment (`.venv`) in the project directory to prevent dependency conflicts.
3. **Dependency Auto-Install**: Silently upgrades `pip` and installs the core communication engine `curl_cffi` (providing high-intensity JA3/JA4/Akamai browser fingerprint spoofing) and `playwright`.
4. **Browser Kit Setup**: Automatically downloads and configures the Playwright-specific Headless Chromium browser components.
5. **Auto-Open Browser**: Once the gateway service starts successfully, the script silently opens your default browser at: `http://127.0.0.1:8888`.

---

## 🚀 Manual Debug & Run

If you prefer a fully manual or containerized step-by-step setup:

### 1. Copy Environment Config

Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Configure your remote server IP, SSH credentials, allowed domain list, and proxy settings.

### 2. Run Python Console Manually

Recommended with Python 3.10+ (with `curl_cffi` and `playwright` installed):
```bash
python3 webapp/server.py --host 127.0.0.1 --port 8888
```
Open your browser at: `http://127.0.0.1:8888`

### 3. Use the Go High-Concurrency Gateway

To handle batch concurrent transactions, compile and run the Go gateway:
```bash
go build -o ppgateway ./cmd/ppgateway/main.go
./ppgateway -addr 127.0.0.1:8787 -static webapp/static
```

---

## 🔧 Environment Variables

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `DEPLOY_HOST` | Target deployment server IP | `your_server_ip` |
| `DEPLOY_USER` | Deployment server SSH username | `root` |
| `DEPLOY_PASSWORD` | SSH password / key passphrase | `your_ssh_password` |
| `DEPLOY_DOMAIN` | Primary service domain to bind | `yourdomain.com` |
| `ALLOWED_DOMAINS` | Console accessible domain whitelist (comma-separated) | `yourdomain.com,example.com` |
| `SERVER_PUBLIC_IP` | Public IP of this gateway (for proxy whitelist hints) | `your_server_ip` |

---

## 🧪 Unit Tests

Run offline unit tests to verify gateway logic:
```bash
python3 -m unittest discover -s tests -v
```

---

## ⚖️ Disclaimer

This project is open-sourced solely for security auditing, network protocol analysis, and academic research on anti-fraud interfaces. No organization or individual may use this project for illegal fraud, bypassing risk controls for commercial gain, or any activity that violates applicable laws or third-party service terms. The author assumes no responsibility for any security incidents or legal disputes arising therefrom.

---

## 📈 Stats

<p align="left">
  <img src="https://visitor-badge.laobi.icu/badge?page_id=jmmy9609-design.gpt-pp" alt="Visitors" />
</p>

[![Star History Chart](https://star-history.dera.page/svg?repos=jmmy9609-design/gpt-pp&type=Date)](https://star-history.dera.page/#jmmy9609-design/gpt-pp&Date)
