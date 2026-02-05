"""
文章摘要生成器
读取昨天的文章，使用 DeepSeek LLM 生成摘要
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置
ARTICLES_DIR = Path("articles")
SUMMARY_DIR = Path("summary")
SUMMARY_DIR.mkdir(exist_ok=True)

PROMPT_FILE = Path("prompt.txt")

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"

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
    separator = "\n" + "=" * 80 + "\n"
    
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
    
    total_tokens = 0
    
    for i, article in enumerate(articles, 1):
        title = article.get("title", "无标题")[:50]
        print(f"[{i}/{len(articles)}] {title}...")
        
        try:
            summary, tokens = generate_summary(client, prompt, article)
            total_tokens += tokens
        except SummaryGenerationError as e:
            print(f"\n" + "!" * 60)
            print(f"❌ 错误: {e}")
            print(f"❌ 文章: {article.get('title', '无标题')}")
            print(f"❌ 流程中断，请检查网络连接或 API 状态后重试")
            print("!" * 60)
            sys.exit(1)
        
        articles_with_summary.append({
            "title": article.get("title", "无标题"),
            "url": article.get("url", ""),
            "summary": summary,
        })
        
        print(f"  ✓ 摘要生成完成")
    
    # 保存摘要文件
    output_file = SUMMARY_DIR / f"{date_str}_summary.txt"
    output_content = format_summary_output(articles_with_summary, date_str)
    
    # 在末尾添加 token 消耗统计
    output_content += f"\n{'─' * 60}\n"
    output_content += f"本次预计消耗 Token 数：{total_tokens:,}\n"
    
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(output_content)
    
    print("\n" + "=" * 60)
    print(f"✓ 摘要生成完成!")
    print(f"  - 处理文章: {len(articles)} 篇")
    print(f"  - 预计消耗 Token: {total_tokens:,}")
    print(f"  - 输出文件: {output_file.absolute()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
