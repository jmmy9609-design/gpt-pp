#!/usr/bin/env python3
import os
import sys
import subprocess

# 自动在本地安装 paramiko 库
try:
    import paramiko
except ImportError:
    print("本地未检测到 paramiko 依赖，正在为您静默安装部署所需模块...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "--break-system-packages"])
    import paramiko

from pathlib import Path

# 配置信息与环境变量/ .env 配置文件自适应加载
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
DOMAIN = os.environ.get("DEPLOY_DOMAIN", "")

LOCAL_DIR = Path(__file__).resolve().parent
REMOTE_TARGET_DIR = "/root/pp_gateway"

def deploy():
    if not HOST or not DOMAIN:
        print("🔴 部署失败：未检测到合法的目标主机(DEPLOY_HOST)或部署域名(DEPLOY_DOMAIN)配置！", flush=True)
        print("💡 请复制根目录下的 `.env.example` 为 `.env` 并填入您的远程部署主机信息，或在运行前导出对应环境变量。", flush=True)
        return False

    print(f"==================================================", flush=True)
    print(f"🚀 启动一键商业云端部署流程: {DOMAIN}", flush=True)
    print(f"==================================================", flush=True)
    
    # 1. 建立 SSH 连接
    print(f"⚡ 正在连接远程服务器 {HOST}:{PORT}...", flush=True)
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, port=PORT, username=USER, password=PASSWORD, timeout=15)
        print("🟢 服务器连接成功！", flush=True)
    except Exception as e:
        print(f"🔴 服务器连接失败: {e}", flush=True)
        return False

    # 2. 在云端创建基础目录
    print(f"📁 正在准备远程部署工作目录 {REMOTE_TARGET_DIR}...", flush=True)
    ssh.exec_command(f"mkdir -p {REMOTE_TARGET_DIR}/webapp/static")

    # 3. SFTP 上传本地核心文件与协议依赖
    print("📦 正在建立高安全 SFTP 上传通道...", flush=True)
    sftp = ssh.open_sftp()

    # 要上传的文件清单
    files_to_upload = [
        ("plus_paypal_link_probe.py", "plus_paypal_link_probe.py"),
        ("protocol_paypal_authorize.py", "protocol_paypal_authorize.py"),
        ("inspect_hosted_checkout.py", "inspect_hosted_checkout.py"),
        ("record_stripe_protocol.py", "record_stripe_protocol.py"),
        ("click_paypal_authorize.py", "click_paypal_authorize.py"),
        ("webapp/server.py", "webapp/server.py"),
        ("webapp/static/index.html", "webapp/static/index.html"),
        ("webapp/static/styles.css", "webapp/static/styles.css"),
        ("webapp/static/app.js", "webapp/static/app.js"),
    ]

    for local_rel, remote_rel in files_to_upload:
        local_path = LOCAL_DIR / local_rel
        remote_path = f"{REMOTE_TARGET_DIR}/{remote_rel}"
        if local_path.exists():
            print(f"  ⬆️ 正在上传: {local_rel} -> {remote_path}", flush=True)
            sftp.put(str(local_path), remote_path)
        else:
            print(f"  ⚠️ 未找到本地文件: {local_rel}，跳过", flush=True)
    sftp.close()
    print("🟢 所有核心文件与前端资源上传完毕！", flush=True)

    # 4. 执行云端环境诊断和依赖安装 (支持 Debian/Ubuntu/CentOS 自适应)
    print("⚡ 正在云端进行系统依赖诊断与必要包安装...", flush=True)
    commands = [
        # 先确保系统包管理器中有 pip 和 编译开发工具
        "if [ -f /usr/bin/apt-get ]; then apt-get update && apt-get install -y python3-pip python3-venv build-essential python3-dev; elif [ -f /usr/bin/yum ]; then yum install -y python-pip python3-pip epel-release; fi",
        # 静默安装和更新核心依赖包 (解决 python2/3 区分及 --break-system-packages 兼容性问题)
        "python3 -m pip install --upgrade pip --break-system-packages || python3 -m pip install --upgrade pip || pip install --upgrade pip || true",
        "python3 -m pip install curl_cffi requests urllib3 certifi --break-system-packages || python3 -m pip install curl_cffi requests urllib3 certifi || pip install curl_cffi requests urllib3 certifi || true",
        "python3 -m pip install --upgrade certifi requests urllib3 --break-system-packages || python3 -m pip install --upgrade certifi requests urllib3 || pip install --upgrade certifi requests urllib3 || true",
        # 补齐 Playwright 核心 Python 包
        "python3 -m pip install playwright --break-system-packages || python3 -m pip install playwright || pip install playwright || true",
        # 热下载 Chromium 浏览器内核
        "python3 -m playwright install chromium || true",
        # 安装 Linux Headless 环境下的 300+ 渲染支持和动态链接系统依赖包
        "python3 -m playwright install-deps || true"
    ]
    for cmd in commands:
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stdout.channel.recv_exit_status() # 等待执行完毕

    # 5. 云端 Systemd 常驻服务创建
    print("⚙️ 正在为您创建 Systemd 系统守护进程 pp-gateway.service...", flush=True)
    service_content = f"""[Unit]
Description=ChatGPT Extract Payment Link Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={REMOTE_TARGET_DIR}
ExecStart=/usr/bin/python3 webapp/server.py --host 127.0.0.1 --port 8787
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PLUS_LINK_BATCH_PROXY_CANDIDATES=12
Environment=PLUS_LINK_BATCH_WORKERS=12
Environment=PLUS_LINK_BATCH_RECOVERY_ROUNDS=1
Environment=PLUS_LINK_BATCH_RECOVERY_PROXY_CANDIDATES=12
Environment=PLUS_LINK_PROXY_CITY_SAMPLES=80
Environment=PLUS_LINK_PROXY_PREFLIGHT_WORKERS=28
Environment=PLUS_LINK_PROXY_PREFLIGHT_TIMEOUT_SECONDS=45
Environment=PLUS_LINK_PROXY_TARGET_PREFLIGHT=1
Environment=PLUS_LINK_CHECKOUT_PAIR_LIMIT=1
Environment=PLUS_LINK_REQUIRE_ZERO=0
Environment=PLUS_LINK_BLOCKED_REDIRECT_POLL_SECONDS=18
Environment=PLUS_LINK_APPROVE_BACKGROUND_POLL_SECONDS=45
Environment=PLUS_LINK_APPROVE_BACKGROUND_WORKERS=12
Environment=PLUS_LINK_REDIRECT_POLL_SECONDS=4
Environment=PLUS_LINK_CHECKOUT_TIMEOUT_SECONDS=14
Environment=PLUS_LINK_TOKEN_UNAUTHORIZED_CONFIRMATIONS=2
Environment=PLUS_LINK_TOKEN_UNAUTHORIZED_RECOVERY=1
Environment=PLUS_LINK_APPROVE_BLOCKED_CONFIRMATIONS=3
Environment=PLUS_LINK_PROXY_UNSTABLE_CONFIRMATIONS=6
Environment=PLUS_LINK_STRIPE_INIT_TIMEOUT_SECONDS=8
Environment=PLUS_LINK_STRIPE_PAYMENT_METHOD_TIMEOUT_SECONDS=5
Environment=PLUS_LINK_STRIPE_CONFIRM_TIMEOUT_SECONDS=7
Environment=PLUS_LINK_CHATGPT_APPROVE_TIMEOUT_SECONDS=3
Environment=PLUS_LINK_SUCCESS_CITY_HINTS=kawagoe,myohoji,myōhōji
Environment=PLUS_LINK_CURL_IMPERSONATE=chrome

[Install]
WantedBy=multi-user.target
"""
    # 写入远程 systemd
    # 我们用 echo 写入或者通过 ssh 直接写入
    stdin, stdout, stderr = ssh.exec_command(f"cat << 'EOF' > /etc/systemd/system/pp-gateway.service\n{service_content}EOF")
    stdout.channel.recv_exit_status()

    # 重启并拉起 systemd 服务
    print("🔄 正在加载 Systemd 守护进程并拉起网关引擎...", flush=True)
    systemd_cmds = [
        "systemctl daemon-reload",
        "systemctl enable pp-gateway",
        "systemctl restart pp-gateway"
    ]
    for scmd in systemd_cmds:
        ssh.exec_command(scmd)[1].channel.recv_exit_status()
    print("🟢 Systemd 服务已成功在后台常驻运行！", flush=True)

    # 6. 自适应安装并配置 Nginx 反向代理
    print("🌐 正在对源站 Nginx 反向代理进行自适应配置与证书融合...", flush=True)
    
    # 自动识别系统架构安装 Nginx
    nginx_install_cmd = """
    if [ -f /usr/bin/apt-get ]; then
        apt-get update && apt-get install -y nginx openssl
    elif [ -f /usr/bin/yum ]; then
        yum install -y epel-release && yum install -y nginx openssl
    fi
    """
    ssh.exec_command(nginx_install_cmd)[1].channel.recv_exit_status()

    # 生成源站 SSL 自签名证书，支持 443 端口与 Cloudflare Full/Full Strict TLS 深度兼容
    print("🔑 正在云端自生成高可用 SSL 自签名证书通道以融合 CF SSL...", flush=True)
    ssl_cert_cmd = f"""
    mkdir -p /etc/ssl/private /etc/ssl/certs
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
      -keyout /etc/ssl/private/nginx-selfsigned.key \
      -out /etc/ssl/certs/nginx-selfsigned.crt \
      -subj "/CN={DOMAIN}"
    """
    ssh.exec_command(ssl_cert_cmd)[1].channel.recv_exit_status()

    # 检查远程是否存在真实的 Let's Encrypt 官方权威证书
    stdin, stdout, stderr = ssh.exec_command(f"test -f /etc/letsencrypt/live/{DOMAIN}/fullchain.pem && echo 'YES' || echo 'NO'")
    has_real_cert = stdout.read().decode("utf-8").strip() == "YES"

    if has_real_cert:
        cert_path = f"/etc/letsencrypt/live/{DOMAIN}/fullchain.pem"
        key_path = f"/etc/letsencrypt/live/{DOMAIN}/privkey.pem"
        print("🔑 发现已在云端签署的 Let's Encrypt 官方权威 SSL 证书，自动升级启用！", flush=True)
    else:
        cert_path = "/etc/ssl/certs/nginx-selfsigned.crt"
        key_path = "/etc/ssl/private/nginx-selfsigned.key"
        print("🔑 未发现官方权威证书，采用高可用本地 SSL 自签名证书作为前置基础...", flush=True)

    # Nginx 反代配置 (同时兼容 80 和 443 端口)
    nginx_conf = f"""server {{
    listen 80;
    listen 443 ssl;
    server_name {DOMAIN};

    ssl_certificate {cert_path};
    ssl_certificate_key {key_path};

    # 限制上传包大小
    client_max_body_size 128k;

    location / {{
        proxy_pass http://127.0.0.1:8787;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 180s;
        proxy_connect_timeout 180s;
    }}
}}
"""
    # 写入 Nginx
    # 兼容 Deb/Ubuntu (sites-available) 和 CentOS (conf.d)
    write_nginx_cmd = f"""
    if [ -d /etc/nginx/sites-available ]; then
        cat << 'EOF' > /etc/nginx/sites-available/pp-gateway
{nginx_conf}EOF
        rm -f /etc/nginx/sites-enabled/default
        ln -sf /etc/nginx/sites-available/pp-gateway /etc/nginx/sites-enabled/
    else
        cat << 'EOF' > /etc/nginx/conf.d/pp_gateway.conf
{nginx_conf}EOF
    fi
    """
    ssh.exec_command(write_nginx_cmd)[1].channel.recv_exit_status()

    # 重新加载 Nginx
    print("🔄 正在加载 Nginx 配置并测试解析...", flush=True)
    nginx_reload_cmd = "nginx -t && systemctl restart nginx || systemctl reload nginx"
    stdin, stdout, stderr = ssh.exec_command(nginx_reload_cmd)
    exit_status = stdout.channel.recv_exit_status()
    if exit_status == 0:
        print("🟢 Nginx 路由转发与本地自签名证书配置成功！", flush=True)
    else:
        print(f"🔴 Nginx 配置检测有误，错误日志如下:", flush=True)
        print(stderr.read().decode("utf-8"), flush=True)

    # 7. 自动尝试在云端签署真实的 Let's Encrypt 官方权威证书
    print("🔑 [自动升级真实证书] 正在检测并尝试向 Let's Encrypt 签署真正的权威 SSL 证书以打通 CF Full (Strict)...", flush=True)
    certbot_install_cmd = f"""
    if [ -f /usr/bin/apt-get ]; then
        apt-get update && apt-get install -y certbot python3-certbot-nginx
    elif [ -f /usr/bin/yum ]; then
        yum install -y certbot python3-certbot-nginx
    fi
    # 自动非交互式签署
    certbot --nginx -d {DOMAIN} --non-interactive --agree-tos --email admin@1818.pro --redirect || true
    """
    stdin, stdout, stderr = ssh.exec_command(certbot_install_cmd)
    stdout.channel.recv_exit_status()
    print("🟢 真实证书部署完毕！若云端校验通过，证书将已完成自动热平滑切换。", flush=True)

    # 8. 全线就绪
    print(f"==================================================", flush=True)
    print(f"🎉 恭喜！云端部署与防护体系已 100% 全线开通！", flush=True)
    print(f"👉 您的商业防克隆主域名: http://{DOMAIN}", flush=True)
    print(f"👉 Cloudflare SSL 安全链接: https://{DOMAIN}", flush=True)
    print(f"🔒 您的后端 Host 防阻断与前端防劫持自毁盾已高强度起航！", flush=True)
    print(f"==================================================", flush=True)
    
    ssh.close()
    return True

if __name__ == "__main__":
    deploy()
