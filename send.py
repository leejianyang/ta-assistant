"""
发送昨天的 summary 到飞书群机器人

功能：
1. 读取 summary 目录下昨天的 summary 文件，例如 20260205_summary.txt
2. 将文件完整内容通过 webhook 发送给飞书机器人
3. webhook 地址通过环境变量配置
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
import json
import urllib.request
import urllib.error
import ssl
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
SUMMARY_DIR = BASE_DIR / "summary"

# 加载 .env 环境变量（固定从项目根目录加载）
load_dotenv(dotenv_path=BASE_DIR / ".env")

# 飞书机器人 Webhook 环境变量名
FEISHU_WEBHOOK_ENV = "FEISHU_WEBHOOK_URL"


def get_yesterday_date_str() -> str:
    """获取昨天的日期字符串，格式 YYYYMMDD"""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y%m%d")


def load_yesterday_summary() -> tuple[Path, str]:
    """
    读取昨天的 summary 文件内容

    Returns:
        (file_path, content)
    Raises:
        FileNotFoundError: 找不到对应的 summary 文件
        OSError: 读取文件失败
    """
    date_str = get_yesterday_date_str()
    file_name = f"{date_str}_summary.txt"
    file_path = SUMMARY_DIR / file_name

    if not file_path.exists():
        raise FileNotFoundError(f"未找到昨天的 summary 文件: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    return file_path, content


def send_to_feishu(webhook_url: str, content: str, verify_ssl: bool = True) -> str:
    """
    将内容发送到飞书机器人

    按用户要求，HTTP body 结构为：
    {
        "msg_type": "text",
        "content": {
            "msg": "xxxx"
        }
    }

    Returns:
        飞书返回的响应文本
    Raises:
        urllib.error.URLError: 网络或 HTTP 错误
    """
    body = {
        "msg_type": "text",
        "content": {
            "msg": content,
        },
    }

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    # SSL 配置：默认校验证书，如需调试可关闭验证
    if verify_ssl:
        ssl_context = ssl.create_default_context()
    else:
        ssl_context = ssl._create_unverified_context()

    # 显式关闭系统环境中的 HTTP/HTTPS 代理，避免公司代理导致 SSL 异常
    proxy_handler = urllib.request.ProxyHandler({})
    https_handler = urllib.request.HTTPSHandler(context=ssl_context)
    opener = urllib.request.build_opener(proxy_handler, https_handler)

    # 直接请求飞书，不经过任何代理
    with opener.open(req) as resp:
        resp_text = resp.read().decode("utf-8", errors="replace")
        return resp_text


def main() -> None:
    # 读取 webhook 地址
    webhook_url = os.getenv(FEISHU_WEBHOOK_ENV)
    if not webhook_url:
        raise RuntimeError(
            f"环境变量 {FEISHU_WEBHOOK_ENV} 未设置，请在环境中配置飞书机器人 Webhook URL"
        )

    # 是否关闭 SSL 校验（仅用于本地调试）
    insecure_ssl = os.getenv("FEISHU_INSECURE_SSL", "").lower() in {
        "1",
        "true",
        "yes",
    }

    # 读取昨天的 summary 文件
    summary_path, summary_content = load_yesterday_summary()

    print(f"准备发送 summary 文件: {summary_path}")

    # 发送到飞书，失败重试
    max_retries = 3
    delay_seconds = 10
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(f"第 {attempt}/{max_retries} 次尝试发送到飞书...")
            resp_text = send_to_feishu(
                webhook_url,
                summary_content,
                verify_ssl=not insecure_ssl,
            )
        except urllib.error.URLError as e:
            last_error = e
            print(f"发送失败: {e}")
            if attempt < max_retries:
                print(f"{delay_seconds} 秒后重试...")
                time.sleep(delay_seconds)
            continue
        else:
            print("发送成功，飞书返回：")
            print(resp_text)
            break
    else:
        # 所有重试都失败
        raise RuntimeError(f"发送到飞书失败（重试 {max_retries} 次后）: {last_error}")


if __name__ == "__main__":
    main()

