"""
文章摘要生成器
读取昨天的文章，使用 DeepSeek LLM 生成摘要
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
import ssl
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

# 配置
ARTICLES_DIR = Path("articles")
SUMMARY_DIR = Path("summary")
SUMMARY_DIR.mkdir(exist_ok=True)

PROMPT_FILE = Path("prompt.txt")

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

# 飞书机器人 Webhook 环境变量名
FEISHU_WEBHOOK_ENV = "FEISHU_WEBHOOK_URL"

# API 调用配置
API_TIMEOUT = 120  # 超时时间（秒）
MAX_RETRIES = 3    # 最大重试次数
RETRY_DELAY = 5    # 重试间隔（秒）


def get_yesterday_date() -> str:
    """获取昨天的日期，格式 YYYYMMDD"""
    yesterday = datetime.now() - timedelta(days=1)
    return yesterday.strftime("%Y%m%d")


def load_prompt() -> str:
    """加载提示词"""
    if PROMPT_FILE.exists():
        with open(PROMPT_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    else:
        # 默认提示词
        return "请为以下文章生成一个简洁的中文摘要，不超过200字："


def load_articles(date_str: str) -> list[dict]:
    """加载指定日期的所有文章"""
    articles_path = ARTICLES_DIR / date_str
    
    if not articles_path.exists():
        print(f"目录不存在: {articles_path}")
        return []
    
    articles = []
    for file_path in articles_path.glob("*.json"):
        # 跳过 article_links.json 等非文章文件
        if file_path.name.startswith("article_links") or file_path.name.startswith("all_articles"):
            continue
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                article = json.load(f)
                article["_file_path"] = str(file_path)
                articles.append(article)
        except Exception as e:
            print(f"读取文件失败 {file_path}: {e}")
    
    return articles


class SummaryGenerationError(Exception):
    """摘要生成失败异常"""
    pass


def estimate_tokens(text: str) -> int:
    """
    粗略估算文本的 token 数
    中文约 1.5 字符 = 1 token，英文约 4 字符 = 1 token
    """
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


def generate_summary(client: OpenAI, prompt: str, article: dict) -> tuple[str, int]:
    """
    调用 DeepSeek API 生成摘要，带重试机制
    
    Returns:
        tuple: (摘要文本, 预估token数)
    
    Raises:
        SummaryGenerationError: 重试多次后仍然失败
    """
    title = article.get("title", "无标题")
    content = article.get("content", "")
    
    if not content:
        return "（文章内容为空，无法生成摘要）", 0
    
    # 构建消息
    user_message = f"{prompt}\n\n标题：{title}\n\n正文：\n{content}"
    
    last_error = None
    
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "user", "content": user_message}
                ],
                max_tokens=4096,
                temperature=0.2,
                timeout=API_TIMEOUT,
            )
            
            summary = response.choices[0].message.content.strip()
            
            # 尝试从响应获取实际 token 数，否则估算
            if hasattr(response, 'usage') and response.usage:
                total_tokens = response.usage.total_tokens
            else:
                # 粗略估算：输入 + 输出
                total_tokens = estimate_tokens(user_message) + estimate_tokens(summary)
            
            return summary, total_tokens
        
        except Exception as e:
            last_error = e
            error_msg = str(e).lower()
            
            # 判断是否是超时或网络相关错误，进行重试
            if 'timeout' in error_msg or 'timed out' in error_msg or 'connection' in error_msg:
                print(f"  ⚠ API 请求超时，第 {attempt + 1}/{MAX_RETRIES} 次重试...")
                time.sleep(RETRY_DELAY)
                continue
            else:
                # 其他错误直接抛出
                raise SummaryGenerationError(f"生成摘要失败: {e}")
    
    # 重试次数用完仍然失败
    raise SummaryGenerationError(f"生成摘要失败（重试 {MAX_RETRIES} 次后）: {last_error}")


def format_summary_output(articles_with_summary: list[dict], date_str: str) -> str:
    """格式化摘要输出"""
    lines = []
    separator = "\n" + "=" * 20 + "\n"
    
    # 解析日期字符串 (YYYYMMDD -> xxxx年xx月xx日)
    year = date_str[:4]
    month = date_str[4:6].lstrip('0')  # 去掉前导零
    day = date_str[6:8].lstrip('0')    # 去掉前导零
    formatted_date = f"{year}年{month}月{day}日"
    
    for i, item in enumerate(articles_with_summary, 1):
        title = item.get("title", "无标题")
        summary = item.get("summary", "")
        url = item.get("url", "")
        
        article_block = f"""【{i}】{title}

📝 摘要：
{summary}

🔗 原文链接：{url}"""
        
        lines.append(article_block)
    
    # 添加头部信息
    header = f"以下为{formatted_date} The Athletic 要闻综述，共{len(articles_with_summary)}篇文章，内容综述如下：\n"
    
    return header + separator + separator.join(lines) + separator


def is_ci_environment() -> bool:
    """检测是否在 CI 环境中运行"""
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def send_feishu_alert(error_msg: str) -> bool:
    """
    发送飞书告警消息
    
    Args:
        error_msg: 告警消息内容，包含尽可能多的调试信息
    
    Returns:
        bool: 是否发送成功
    """
    webhook_url = os.getenv(FEISHU_WEBHOOK_ENV)
    if not webhook_url:
        print(f"⚠️  警告: 环境变量 {FEISHU_WEBHOOK_ENV} 未设置，无法发送飞书告警", flush=True)
        return False
    
    body = {
        "msg_type": "alert",
        "content": {
            "msg": error_msg,
        },
    }
    
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        
        # SSL 配置：默认校验证书
        ssl_context = ssl.create_default_context()
        
        # 显式关闭系统环境中的 HTTP/HTTPS 代理，避免公司代理导致 SSL 异常
        proxy_handler = urllib.request.ProxyHandler({})
        https_handler = urllib.request.HTTPSHandler(context=ssl_context)
        opener = urllib.request.build_opener(proxy_handler, https_handler)
        
        # 直接请求飞书，不经过任何代理
        with opener.open(req, timeout=10) as resp:
            resp_text = resp.read().decode("utf-8", errors="replace")
            print(f"✓ 飞书告警发送成功: {resp_text[:100]}", flush=True)
            return True
    except Exception as e:
        print(f"✗ 发送飞书告警失败: {e}", flush=True)
        return False


def format_api_key_missing_alert() -> str:
    """生成 API 密钥未配置的告警消息"""
    return f"""摘要生成失败：API 密钥未配置

调试信息：
- 时间: {datetime.now().isoformat()}
- CI环境: {is_ci_environment()}
- 环境变量 DEEPSEEK_API_KEY: {'未设置' if not DEEPSEEK_API_KEY else '已设置（但可能为空）'}

可能的原因：
1. .env 文件中未配置 DEEPSEEK_API_KEY
2. 环境变量未正确加载
3. API 密钥配置错误

建议：
1. 检查 .env 文件是否存在并包含 DEEPSEEK_API_KEY=sk-xxxxx
2. 确认环境变量加载路径正确
3. 验证 API 密钥是否有效"""


def format_no_articles_found_alert(date_str: str) -> str:
    """生成未找到文章的告警消息"""
    articles_path = ARTICLES_DIR / date_str
    
    return f"""摘要生成失败：未找到文章

调试信息：
- 时间: {datetime.now().isoformat()}
- 目标日期: {date_str}
- 文章目录: {articles_path.absolute()}
- 目录存在: {articles_path.exists()}
- CI环境: {is_ci_environment()}

可能的原因：
1. 指定日期的文章尚未抓取
2. 文章目录路径配置错误
3. 文章文件格式不正确

建议：
1. 检查 articles/{date_str}/ 目录是否存在
2. 确认该目录下是否有 .json 格式的文章文件
3. 运行 python scraper.py 先抓取文章"""


def format_summary_generation_failed_alert(article_title: str, error: Exception, 
                                           date_str: str, processed_count: int, 
                                           total_count: int) -> str:
    """生成摘要生成失败的告警消息"""
    return f"""摘要生成失败：单篇文章处理失败

调试信息：
- 时间: {datetime.now().isoformat()}
- 目标日期: {date_str}
- 失败文章: {article_title[:100]}
- 错误类型: {type(error).__name__}
- 错误信息: {str(error)}
- 处理进度: {processed_count}/{total_count}
- CI环境: {is_ci_environment()}
- API密钥配置: {'已配置' if DEEPSEEK_API_KEY else '未配置'}

可能的原因：
1. DeepSeek API 服务异常
2. 网络连接问题
3. API 密钥无效或过期
4. 文章内容过长或格式异常
5. API 调用超时

建议：
1. 检查网络连接和 DeepSeek API 服务状态
2. 验证 DEEPSEEK_API_KEY 是否有效
3. 检查文章内容是否正常
4. 稍后重试或使用 --force 参数重新生成"""


def format_all_summaries_failed_alert(date_str: str, total_count: int, 
                                      failed_articles: list) -> str:
    """生成所有摘要生成失败的告警消息"""
    failed_details = "\n".join([
        f"- {art.get('title', '无标题')[:50]}...\n  错误: {art.get('error', '未知错误')}"
        for art in failed_articles
    ])
    
    return f"""摘要生成失败：所有文章处理失败

调试信息：
- 时间: {datetime.now().isoformat()}
- 目标日期: {date_str}
- 文章总数: {total_count}
- 失败数量: {len(failed_articles)}
- CI环境: {is_ci_environment()}
- API密钥配置: {'已配置' if DEEPSEEK_API_KEY else '未配置'}

失败详情：
{failed_details}

可能的原因：
1. DeepSeek API 服务完全不可用
2. API 密钥无效或已过期
3. 网络连接完全中断
4. API 调用配额已用完
5. 所有文章内容格式异常

建议：
1. 检查 DeepSeek API 服务状态
2. 验证 DEEPSEEK_API_KEY 是否有效
3. 检查网络连接
4. 查看 API 使用配额
5. 检查文章文件格式"""


def format_unexpected_exception_alert(e: Exception, date_str: str) -> str:
    """生成未预期异常的告警消息"""
    import traceback
    error_traceback = traceback.format_exc()
    
    return f"""摘要生成失败：发生未预期的异常

调试信息：
- 时间: {datetime.now().isoformat()}
- 目标日期: {date_str}
- 异常类型: {type(e).__name__}
- 异常信息: {str(e)}
- CI环境: {is_ci_environment()}
- Python版本: {sys.version}

完整堆栈跟踪：
{error_traceback}

可能的原因：
1. 代码逻辑错误
2. 环境配置问题
3. 依赖库版本不兼容
4. 文件系统权限问题
5. 系统资源不足

建议：
1. 检查完整错误堆栈跟踪
2. 确认依赖库版本是否正确
3. 检查文件系统权限
4. 检查系统资源使用情况"""


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="文章摘要生成器")
    parser.add_argument("--force", action="store_true", help="强制重新生成摘要（即使已存在）")
    args = parser.parse_args()
    
    # 检查 API 密钥
    if not DEEPSEEK_API_KEY:
        print("错误: 请在 .env 文件中设置 DEEPSEEK_API_KEY")
        print("示例: DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx")
        send_feishu_alert(format_api_key_missing_alert())
        return
    
    # 获取昨天的日期
    date_str = get_yesterday_date()
    
    # 检查目标摘要文件是否已存在
    output_file = SUMMARY_DIR / f"{date_str}_summary.txt"
    if output_file.exists() and not args.force:
        print(f"摘要文件已存在: {output_file}")
        print(f"如需重新生成，请使用 --force 参数")
        return
    
    print(f"=" * 60)
    print(f"文章摘要生成器")
    print(f"=" * 60)
    print(f"处理日期: {date_str}")
    
    # 加载提示词
    prompt = load_prompt()
    print(f"✓ 已加载提示词: {PROMPT_FILE}")
    
    # 加载文章
    articles = load_articles(date_str)
    if not articles:
        print(f"未找到 {date_str} 的文章")
        send_feishu_alert(format_no_articles_found_alert(date_str))
        return
    
    print(f"✓ 找到 {len(articles)} 篇文章")
    
    # 初始化 DeepSeek 客户端
    client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url=DEEPSEEK_BASE_URL,
    )
    
    # 逐篇生成摘要
    print("\n开始生成摘要...")
    print("-" * 40)
    
    articles_with_summary = []
    failed_articles = []
    
    total_tokens = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "无标题")[:50]
        print(f"[{i}/{len(articles)}] {title}...")
        
        try:
            summary, tokens = generate_summary(client, prompt, article)
            total_tokens += tokens
            articles_with_summary.append({
                "title": article.get("title", "无标题"),
                "url": article.get("url", ""),
                "summary": summary,
            })
            print(f"  ✓ 摘要生成完成")
        except SummaryGenerationError as e:
            print(f"  ✗ 摘要生成失败: {e}")
            failed_articles.append({
                "title": article.get("title", "无标题"),
                "url": article.get("url", ""),
                "error": str(e)
            })
    
    # 处理失败情况
    if failed_articles:
        # 如果所有文章都失败了，发送告警并退出
        if len(articles_with_summary) == 0:
            send_feishu_alert(format_all_summaries_failed_alert(
                date_str, len(articles), failed_articles
            ))
            print(f"\n" + "!" * 60)
            print(f"❌ 所有文章摘要生成失败")
            print(f"❌ 流程中断，请检查网络连接或 API 状态后重试")
            print("!" * 60)
            sys.exit(1)
        # 如果失败的文章超过一半，发送告警并退出
        elif len(failed_articles) > len(articles) / 2:
            send_feishu_alert(format_all_summaries_failed_alert(
                date_str, len(articles), failed_articles
            ))
            print(f"\n" + "!" * 60)
            print(f"❌ 超过一半的文章摘要生成失败 ({len(failed_articles)}/{len(articles)})")
            print(f"❌ 流程中断，请检查网络连接或 API 状态后重试")
            print("!" * 60)
            sys.exit(1)
        # 如果只有少数文章失败，发送单篇文章失败的告警（只发送第一条失败的）
        else:
            # 只对第一条失败的文章发送告警，避免告警过多
            first_failed = failed_articles[0]
            send_feishu_alert(format_summary_generation_failed_alert(
                first_failed.get("title", "无标题"), 
                Exception(first_failed.get("error", "未知错误")), 
                date_str, 
                len(articles_with_summary), 
                len(articles)
            ))
    
    # 保存摘要文件
    try:
        output_file = SUMMARY_DIR / f"{date_str}_summary.txt"
        output_content = format_summary_output(articles_with_summary, date_str)
        
        # 在末尾添加 token 消耗统计
        output_content += f"本次预计消耗 Token 数：{total_tokens:,}\n"
        
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(output_content)
        
        print("\n" + "=" * 60)
        print(f"✓ 摘要生成完成!")
        print(f"  - 处理文章: {len(articles)} 篇")
        print(f"  - 成功生成: {len(articles_with_summary)} 篇")
        if failed_articles:
            print(f"  - 生成失败: {len(failed_articles)} 篇")
        print(f"  - 预计消耗 Token: {total_tokens:,}")
        print(f"  - 输出文件: {output_file.absolute()}")
        print("=" * 60)
    except Exception as e:
        # 文件保存失败，发送告警
        send_feishu_alert(format_unexpected_exception_alert(e, date_str))
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 捕获所有未预期的异常
        date_str = get_yesterday_date()
        send_feishu_alert(format_unexpected_exception_alert(e, date_str))
        raise
