#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

try:
    import paramiko
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko


ROOT = pathlib.Path(__file__).resolve().parent
OLD_DEPLOY = ROOT / "deploy_server.py"
BIN = ROOT / ".tmp" / "ppgateway-linux-amd64"
HELPERS = [
    "python_executor.py",
    "plus_paypal_link_probe.py",
    "protocol_paypal_authorize.py",
    "inspect_hosted_checkout.py",
    "webapp/static/index.html",
    "webapp/static/styles.css",
    "webapp/static/app.js",
]


def load_target():
    spec = importlib.util.spec_from_file_location("deploy_server", OLD_DEPLOY)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod.HOST, int(mod.PORT), mod.USER, mod.PASSWORD, mod.DOMAIN, mod.REMOTE_TARGET_DIR


def run(ssh, command: str) -> str:
    stdin, stdout, stderr = ssh.exec_command(command)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if code != 0:
        raise RuntimeError(f"remote command failed ({code}): {command}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    return out.strip()


def main() -> int:
    host, port, user, password, domain, remote_dir = load_target()
    if not host or not domain:
        print("🔴 部署失败：未检测到合法的部署主机(DEPLOY_HOST)或域名(DEPLOY_DOMAIN)配置！", flush=True)
        print("💡 请复制根目录下的 `.env.example` 为 `.env` 并填入您的远程部署主机信息，或在运行前导出对应环境变量。", flush=True)
        return 1

    BIN.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({"GOOS": "linux", "GOARCH": "amd64"})
    subprocess.check_call([
        "go", "build", "-o", str(BIN), "./cmd/ppgateway"
    ], cwd=ROOT, env=env)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password, timeout=20)
    try:
        run(ssh, f"mkdir -p {remote_dir}/webapp/static {remote_dir}/.venv")
        sftp = ssh.open_sftp()
        try:
            # Avoid truncating the executable while systemd is running it.
            # Some SFTP servers return only a generic "Failure" in that case.
            sftp.put(str(BIN), f"{remote_dir}/ppgateway.new")
            for rel in HELPERS:
                local = ROOT / rel
                if local.exists():
                    sftp.put(str(local), f"{remote_dir}/{rel}")
        finally:
            sftp.close()

        run(ssh, f"chmod +x {remote_dir}/ppgateway.new")
        run(ssh, f"cd {remote_dir} && python3 -m venv .venv || true")
        run(ssh, f"cd {remote_dir} && .venv/bin/python -m pip install -q --upgrade pip curl_cffi certifi")
        service = f"""[Unit]
Description=PayPal Long Link Go Gateway
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={remote_dir}
ExecStart={remote_dir}/ppgateway -addr 127.0.0.1:8787 -static webapp/static
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
        run(ssh, f"cat > /etc/systemd/system/pp-gateway.service <<'EOF'\n{service}\nEOF")
        run(ssh, f"systemctl stop pp-gateway || true; mv -f {remote_dir}/ppgateway.new {remote_dir}/ppgateway")
        run(ssh, "systemctl daemon-reload && systemctl enable pp-gateway && systemctl restart pp-gateway")
        health = run(ssh, "sleep 1; curl -fsS http://127.0.0.1:8787/api/health")
        active = run(ssh, "systemctl is-active pp-gateway")
        print(f"remote={host} domain={domain} service={active} health={health}")
    finally:
        ssh.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
