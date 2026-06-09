#!/usr/bin/env python3
import paramiko

from pathlib import Path
import os

def load_env_file():
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if val.startswith(('"', "'")) and val.endswith(val[0]):
                    val = val[1:-1]
                if key:
                    os.environ.setdefault(key, val)

load_env_file()

HOST = os.environ.get("DEPLOY_HOST", "")
PORT = int(os.environ.get("DEPLOY_PORT", "22"))
USER = os.environ.get("DEPLOY_USER", "root")
PASSWORD = os.environ.get("DEPLOY_PASSWORD", "")

def run_diagnostic():
    if not HOST:
        print("🔴 诊断失败：未检测到目标主机(DEPLOY_HOST)配置！", flush=True)
        print("💡 请复制根目录下的 `.env.example` 为 `.env` 并填入您的远程主机信息，或在运行前导出对应环境变量。", flush=True)
        return

    print(f"==================================================", flush=True)
    print(f"🔍 启动云端服务器生产环境现场诊断...", flush=True)
    print(f"==================================================", flush=True)
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=10)
        print("🟢 SSH 连接成功！开始抓取状态证据链...\n", flush=True)
    except Exception as e:
        print(f"🔴 SSH 连接失败: {e}", flush=True)
        return

    commands = [
        ("Nginx 运行状态", "systemctl status nginx"),
        ("Nginx 端口监听", "ss -tulnp | grep nginx || netstat -tulnp | grep nginx || lsof -i:80"),
        ("Python 网关运行状态", "systemctl status pp-gateway"),
        ("Python 网关端口监听", "ss -tulnp | grep 8787 || netstat -tulnp | grep 8787 || lsof -i:8787"),
        ("Python 网关最近 30 行报错日志", "journalctl -u pp-gateway -n 30 --no-pager"),
        ("Nginx 最近 20 行报错日志", "tail -n 20 /var/log/nginx/error.log || cat /var/log/nginx/error.log"),
        ("防火墙状态", "ufw status || iptables -L -n -v | head -n 10"),
    ]

    for title, cmd in commands:
        print(f"--------------------------------------------------", flush=True)
        print(f"📊 诊断项目: {title}", flush=True)
        print(f"💻 执行指令: {cmd}", flush=True)
        print(f"--------------------------------------------------", flush=True)
        try:
            stdin, stdout, stderr = ssh.exec_command(cmd)
            out_str = stdout.read().decode("utf-8").strip()
            err_str = stderr.read().decode("utf-8").strip()
            if out_str:
                print(out_str, flush=True)
            if err_str:
                print(f"⚠️ 标标流 stderr:\n{err_str}", flush=True)
        except Exception as e:
            print(f"❌ 执行失败: {e}", flush=True)
        print("\n", flush=True)

    ssh.close()

if __name__ == "__main__":
    run_diagnostic()
