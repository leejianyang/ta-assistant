"""
The Athletic 文章爬虫
使用 Playwright 框架提取 headline 文章内容

使用方法：
1. 首次运行: python scraper.py --login
   (会打开浏览器让你手动登录，登录成功后按 Enter 保存 Cookie)
2. 后续运行: python scraper.py
   (自动使用已保存的 Cookie)

GitHub Actions 使用方法：
- 将 auth_state.json 的内容保存到 GitHub Secrets 的 AUTH_STATE_JSON
- 工作流会自动从 Secrets 恢复认证状态
"""

import os
import sys
import json
import re
import time
from datetime import datetime
from pathlib import Path
from playwright.sync_api import sync_playwright, Page, Browser

# 配置
BASE_URL = "https://www.nytimes.com/athletic"
NEWS_URL = f"{BASE_URL}/news/"
LOGIN_URL = "https://www.nytimes.com/athletic/login"

# Cookie 存储文件
COOKIE_FILE = Path("auth_state.json")

# 输出目录
ARTICLES_DIR = Path("articles")
ARTICLES_DIR.mkdir(exist_ok=True)

# 索引文件（用于去重，避免重复抓取）
INDEX_FILE = ARTICLES_DIR / "index.json"

# 重试配置
MAX_RETRIES = 10
RETRY_DELAY = 5  # 秒


def get_output_dir_by_date(date_str: str = None) -> Path:
    """
    根据日期获取输出目录，格式：articles/YYYYMMDD/
    
    Args:
        date_str: 日期字符串，如果为None或解析失败，使用今天的日期
    """
    folder_date = None
    
    if date_str:
        # 尝试解析各种日期格式
        # 例如: "Jan. 30, 2026", "2026-01-30", "January 30, 2026" 等
        import re
        
        # 尝试提取年月日
        # 格式1: "Jan. 30, 2026" 或 "January 30, 2026"
        month_map = {
            'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
            'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
            'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12'
        }
        
        # 匹配 "Mon. DD, YYYY" 或 "Month DD, YYYY"
        match = re.search(r'([a-zA-Z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})', date_str)
        if match:
            month_abbr = match.group(1)[:3].lower()
            day = match.group(2).zfill(2)
            year = match.group(3)
            if month_abbr in month_map:
                folder_date = f"{year}{month_map[month_abbr]}{day}"
        
        # 格式2: "YYYY-MM-DD" 或 ISO 格式
        if not folder_date:
            match = re.search(r'(\d{4})-(\d{2})-(\d{2})', date_str)
            if match:
                folder_date = f"{match.group(1)}{match.group(2)}{match.group(3)}"
    
    # 如果解析失败，使用今天的日期
    if not folder_date:
        folder_date = datetime.now().strftime("%Y%m%d")
    
    output_dir = ARTICLES_DIR / folder_date
    output_dir.mkdir(exist_ok=True)
    return output_dir


def load_index() -> dict:
    """加载文章索引 {url: 抓取时间戳}"""
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}


def save_index(index: dict):
    """保存文章索引"""
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def is_article_scraped(index: dict, url: str) -> bool:
    """检查文章是否已抓取"""
    return url in index


def goto_with_retry(page: Page, url: str, max_retries: int = MAX_RETRIES, **kwargs) -> bool:
    """
    带重试的页面导航，处理服务器错误等
    
    Returns:
        bool: 是否成功加载页面
    """
    for attempt in range(max_retries):
        try:
            response = page.goto(url, **kwargs)
            
            # 检查 HTTP 状态码
            if response and response.status >= 500:
                print(f"  ⚠ HTTP {response.status} 错误，第 {attempt + 1}/{max_retries} 次重试...")
                time.sleep(RETRY_DELAY)
                continue
            
            # 等待页面稳定
            time.sleep(1)
            
            # 检查页面是否显示服务器错误（只检查页面开头部分，避免误判）
            try:
                # 检查 title 或 h1 是否包含错误信息
                title = page.title().lower()
                error_in_title = 'error' in title or 'unavailable' in title
                
                # 检查页面是否几乎为空（错误页面通常内容很少）
                body_text = page.locator('body').inner_text(timeout=5000)
                is_error_page = False
                
                # 错误页面通常很短，且包含特定错误信息
                if len(body_text) < 500:
                    body_lower = body_text.lower()
                    error_phrases = [
                        'internal server error',
                        'something went wrong',
                        'service unavailable',
                        'bad gateway',
                        'gateway timeout',
                        'server error',
                    ]
                    is_error_page = any(phrase in body_lower for phrase in error_phrases)
                
                if error_in_title or is_error_page:
                    print(f"  ⚠ 页面显示服务器错误，第 {attempt + 1}/{max_retries} 次重试...")
                    time.sleep(RETRY_DELAY)
                    continue
            except:
                pass  # 如果无法获取页面文本，继续执行
            
            # 成功
            return True
            
        except Exception as e:
            error_msg = str(e).lower()
            # 如果是网络错误或超时，重试
            if 'timeout' in error_msg or 'net::' in error_msg or 'navigation' in error_msg:
                print(f"  ⚠ 加载失败: {e}，第 {attempt + 1}/{max_retries} 次重试...")
                time.sleep(RETRY_DELAY)
                continue
            else:
                # 其他错误直接抛出
                raise
    
    print(f"  ✗ 重试 {max_retries} 次后仍然失败")
    return False


def manual_login_and_save_cookie(p) -> bool:
    """
    打开浏览器让用户手动登录，然后保存 Cookie
    """
    print("=" * 60)
    print("手动登录模式")
    print("=" * 60)
    print("1. 浏览器将打开登录页面")
    print("2. 请在浏览器中手动登录你的付费账户")
    print("3. 登录成功后，回到这里按 Enter 键保存 Cookie")
    print("=" * 60)
    
    browser = None
    
    # 启动浏览器
    try:
        browser = p.firefox.launch(headless=False, slow_mo=50)
    except:
        try:
            browser = p.chromium.launch(headless=False, slow_mo=50)
        except:
            browser = p.webkit.launch(headless=False, slow_mo=50)
    
    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
    )
    page = context.new_page()
    
    try:
        # 打开登录页面（无超时限制）
        page.goto(LOGIN_URL, timeout=0)
        
        print("\n>>> 请在浏览器中完成登录...")
        print(">>> 登录成功后，按 Enter 键继续...")
        input()
        
        # 保存认证状态（包括 cookies、localStorage 等）
        context.storage_state(path=str(COOKIE_FILE))
        
        print(f"\n✓ Cookie 已保存到 {COOKIE_FILE}")
        print("下次运行时将自动使用此 Cookie，无需重新登录")
        
        return True
        
    except Exception as e:
        print(f"保存 Cookie 失败: {e}")
        return False
    finally:
        browser.close()


def has_saved_cookie() -> bool:
    """检查是否有已保存的 Cookie"""
    return COOKIE_FILE.exists()


def get_article_links(page: Page, debug: bool = True) -> list[dict]:
    """
    从新闻页面获取所有文章链接
    """
    print(f"正在访问: {NEWS_URL}")
    if not goto_with_retry(page, NEWS_URL, wait_until="networkidle", timeout=300000):
        print("无法加载新闻页面")
        return []
    time.sleep(3)
    
    # 滚动页面以加载更多内容
    print("滚动页面加载更多内容...")
    for _ in range(5):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(2)
    
    # 滚回顶部
    page.evaluate("window.scrollTo(0, 0)")
    time.sleep(1)
    
    # 保存页面HTML用于调试
    if debug:
        html_content = page.content()
        debug_file = ARTICLES_DIR / "debug_page.html"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        print(f"✓ 页面HTML已保存到 {debug_file}")
    
    articles = []
    seen_urls = set()
    
    # 文章URL格式：/athletic/数字ID/日期/标题/
    article_url_pattern = re.compile(r'nytimes\.com/athletic/\d+/\d{4}/\d{2}/\d{2}/')
    
    all_links = page.locator('a[href*="nytimes.com/athletic/"]').all()
    print(f"  找到 {len(all_links)} 个 Athletic 链接")
    
    for link in all_links:
        try:
            href = link.get_attribute("href")
            if not href or href in seen_urls:
                continue
            
            # 检查是否是文章链接（包含数字ID和日期）
            if not article_url_pattern.search(href):
                continue
            
            # 排除非文章页面
            exclude_patterns = ["/login", "/subscribe", "/account", "/author/", "/team/", "/league/", "/podcast/"]
            if any(pattern in href for pattern in exclude_patterns):
                continue
            
            # 获取标题 - 优先从 h5 标题元素获取
            title = ""
            try:
                # 尝试在链接内部找标题
                headline = link.locator('h5, h4, h3, h2, h1').first
                if headline.count() > 0:
                    title = headline.inner_text().strip()
            except:
                pass
            
            # 如果没找到标题，使用链接文本
            if not title:
                title = link.inner_text().strip()
                # 清理标题（取第一行有意义的文本）
                lines = [l.strip() for l in title.split('\n') if l.strip() and len(l.strip()) > 10]
                title = lines[0] if lines else ""
            
            if title and len(title) > 10:
                seen_urls.add(href)
                articles.append({
                    "title": title[:200],
                    "url": href
                })
                print(f"    ✓ {title[:60]}...")
        except Exception as e:
            continue
    
    print(f"✓ 找到 {len(articles)} 篇文章")
    return articles


def extract_article_content(page: Page, url: str, save_html: bool = False) -> dict:
    """
    提取单篇文章的内容
    
    Args:
        save_html: 如果为 True，保存文章HTML到文件用于调试
    """
    try:
        # 带重试的页面加载
        if not goto_with_retry(page, url, wait_until="domcontentloaded", timeout=300000):
            return {
                "url": url,
                "error": "页面加载失败（多次重试后）",
                "scraped_at": datetime.now().isoformat(),
            }
        time.sleep(3)
        
        # 保存HTML用于调试（放在最前面，确保能保存）
        if save_html:
            try:
                html_content = page.content()
                debug_file = ARTICLES_DIR / "debug_article.html"
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(html_content)
                print(f"  ✓ 文章HTML已保存到 {debug_file}")
            except Exception as e:
                print(f"  ✗ 保存HTML失败: {e}")
        
        article_data = {
            "url": url,
            "scraped_at": datetime.now().isoformat(),
        }
        
        # 提取标题
        title_selectors = [
            'h1',
            'article h1',
            '[data-testid="headline"]',
            '.headline',
            '.article-title',
        ]
        
        for selector in title_selectors:
            try:
                title_el = page.locator(selector).first
                if title_el.is_visible():
                    article_data["title"] = title_el.inner_text().strip()
                    break
            except:
                continue
        
        # 提取作者
        author_selectors = [
            '[data-testid="byline"]',
            '.byline',
            '.author',
            'a[href*="/author/"]',
        ]
        
        for selector in author_selectors:
            try:
                author_el = page.locator(selector).first
                if author_el.is_visible():
                    article_data["author"] = author_el.inner_text().strip()
                    break
            except:
                continue
        
        # 提取发布日期
        date_selectors = [
            'time',
            '[data-testid="timestamp"]',
            '.publish-date',
            '.date',
        ]
        
        for selector in date_selectors:
            try:
                date_el = page.locator(selector).first
                if date_el.is_visible():
                    article_data["published_date"] = date_el.inner_text().strip()
                    break
            except:
                continue
        
        # 提取正文内容
        # The Athletic 的正文在 .article-content-container 里面的 <p> 标签
        # 需要排除：图片版权、广告、推荐内容等
        paragraphs = []
        
        # 直接选择正文容器内的 p 标签，但排除特定类
        # 正文 p 标签没有特殊 class，而图片版权等有特定 class
        content_selector = 'div.article-content-container > p:not([class])'
        p_elements = page.locator(content_selector).all()
        
        print(f"  找到 {len(p_elements)} 个正文段落（使用选择器: {content_selector}）")
        
        if p_elements:
            for p in p_elements:
                try:
                    text = p.inner_text().strip()
                    if text and len(text) > 10:
                        paragraphs.append(text)
                except:
                    continue
        
        # 如果直接子元素没找到，尝试更宽松的选择器
        if not paragraphs:
            # 获取容器内所有 p，但排除 ignore 和 ad 内的
            content_container = page.locator('div.article-content-container').first
            if content_container.count() > 0:
                all_p = content_container.locator('p').all()
                print(f"  备用：找到 {len(all_p)} 个 p 标签")
                
                for p in all_p:
                    try:
                        # 获取 p 的 class 属性
                        p_class = p.get_attribute('class') or ''
                        
                        # 排除有特定 class 的 p（图片版权、广告等）
                        skip_classes = ['ImageCaption', 'ImageCredit', 'ad-slug', 'showcase']
                        if any(skip in p_class for skip in skip_classes):
                            continue
                        
                        text = p.inner_text().strip()
                        
                        # 过滤太短的段落和广告相关文本
                        if text and len(text) > 20:
                            # 排除常见的非正文内容
                            skip_texts = ['advertisement', 'follow', 'twitter', '@', 'getty images', 'photo:']
                            if not any(skip.lower() in text.lower()[:50] for skip in skip_texts):
                                paragraphs.append(text)
                    except:
                        continue
        
        article_data["content"] = "\n\n".join(paragraphs)
        article_data["paragraph_count"] = len(paragraphs)
        
        return article_data
        
    except Exception as e:
        return {
            "url": url,
            "error": str(e),
            "scraped_at": datetime.now().isoformat(),
        }


def save_article(article: dict, output_dir: Path) -> Path:
    """
    保存文章到文件
    
    Args:
        article: 文章数据
        output_dir: 输出目录
    """
    # 创建安全的文件名：保留字母数字和下划线，空格替换为下划线
    title = article.get("title", "untitled")
    safe_title = "".join(c if c.isalnum() or c == "_" else "_" if c == " " else "" for c in title)
    # 合并连续的下划线，并限制长度
    safe_title = "_".join(part for part in safe_title.split("_") if part)[:80]
    filename = f"{safe_title}.json"
    
    filepath = output_dir / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(article, f, ensure_ascii=False, indent=2)
    
    return filepath


def is_ci_environment() -> bool:
    """检测是否在 CI 环境中运行"""
    return os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"


def launch_browser(p, with_cookie: bool = False):
    """
    启动浏览器，可选择性地加载已保存的 Cookie
    在 CI 环境中自动使用 headless 模式
    """
    browser = None
    is_ci = is_ci_environment()
    headless = is_ci  # CI 环境使用 headless 模式
    
    if is_ci:
        print("🤖 检测到 CI 环境，使用 headless 模式")
    
    # 在 CI 环境中优先使用 Chromium（已安装）
    browser_order = [(p.chromium, "Chromium")] if is_ci else [
        (p.firefox, "Firefox"), 
        (p.webkit, "WebKit"), 
        (p.chromium, "Chromium")
    ]
    
    # 尝试启动浏览器
    for browser_type, name in browser_order:
        try:
            print(f"尝试启动 {name} 浏览器...")
            if name == "Chromium":
                browser = browser_type.launch(
                    headless=headless,
                    slow_mo=50 if is_ci else 100,
                    args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
                )
            else:
                browser = browser_type.launch(headless=headless, slow_mo=50 if is_ci else 100)
            print(f"✓ {name} 启动成功")
            break
        except Exception as e:
            print(f"{name} 启动失败: {e}")
    
    if browser is None:
        print("所有浏览器都无法启动，请运行: playwright install chromium")
        return None, None
    
    # 创建上下文，可选择性地加载 Cookie
    context_options = {
        "viewport": {"width": 1920, "height": 1080},
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    if with_cookie and COOKIE_FILE.exists():
        context_options["storage_state"] = str(COOKIE_FILE)
        print(f"✓ 已加载 Cookie: {COOKIE_FILE}")
    
    context = browser.new_context(**context_options)
    return browser, context


def main():
    """
    主函数
    
    使用方法:
      python scraper.py --login   # 手动登录并保存 Cookie
      python scraper.py           # 使用已保存的 Cookie 进行爬取
    """
    
    # 解析命令行参数
    login_mode = "--login" in sys.argv
    debug_mode = "--debug" in sys.argv  # 调试模式：只抓取第一篇文章
    save_html = "--save-html" in sys.argv  # 保存HTML文件用于调试
    
    print("=" * 60)
    print("The Athletic 文章爬虫")
    print("=" * 60)
    
    with sync_playwright() as p:
        
        # 登录模式：手动登录并保存 Cookie
        if login_mode:
            manual_login_and_save_cookie(p)
            return
        
        # 爬取模式：检查是否有 Cookie
        if not has_saved_cookie():
            print("❌ 未找到已保存的 Cookie")
            print("")
            print("请先运行以下命令进行登录：")
            print("  python scraper.py --login")
            print("")
            return
        
        # 启动浏览器并加载 Cookie
        browser, context = launch_browser(p, with_cookie=True)
        if browser is None:
            return
        
        page = context.new_page()
        
        try:
            # 加载文章索引（用于去重）
            index = load_index()
            print(f"✓ 已加载索引，历史抓取文章数: {len(index)}")
            
            # 获取文章链接
            articles = get_article_links(page, debug=save_html)
            
            if not articles:
                print("未找到任何文章链接")
                return
            
            # 过滤已抓取的文章
            new_articles = []
            skipped_count = 0
            for article in articles:
                if is_article_scraped(index, article["url"]):
                    skipped_count += 1
                else:
                    new_articles.append(article)
            
            print(f"✓ 找到 {len(articles)} 篇文章，其中 {skipped_count} 篇已抓取，{len(new_articles)} 篇待抓取")
            
            if not new_articles:
                print("没有新文章需要抓取")
                return
            
            # 提取每篇文章内容
            print("\n开始提取文章内容...")
            print("-" * 40)
            
            # 调试模式只抓取第一篇
            if debug_mode:
                print("🔧 调试模式：只抓取第一篇文章")
                new_articles = new_articles[:1]
            
            all_articles = []
            success_count = 0
            
            for i, article_info in enumerate(new_articles, 1):
                print(f"[{i}/{len(new_articles)}] 正在提取: {article_info['title'][:50]}...")
                
                # 只在指定 --save-html 参数且是第一篇文章时保存HTML
                should_save_html = save_html and (i == 1)
                article_data = extract_article_content(page, article_info["url"], save_html=should_save_html)
                all_articles.append(article_data)
                
                content_len = len(article_data.get("content", ""))
                if "error" in article_data:
                    print(f"  ✗ 提取失败: {article_data['error']}")
                else:
                    # 根据发布日期确定存储目录
                    published_date = article_data.get("published_date", "")
                    output_dir = get_output_dir_by_date(published_date)
                    
                    # 保存单篇文章
                    filepath = save_article(article_data, output_dir)
                    
                    print(f"  ✓ 已保存: {output_dir.name}/{filepath.name} ({content_len} 字符)")
                    success_count += 1
                    
                    # 更新索引
                    index[article_info["url"]] = datetime.now().isoformat()
                
                # 添加延迟，避免请求过快
                time.sleep(2)
            
            # 保存索引
            save_index(index)
            print(f"✓ 索引已更新，当前总文章数: {len(index)}")
            
            print("\n" + "=" * 60)
            print(f"✓ 爬取完成!")
            print(f"  - 本次抓取: {len(new_articles)} 篇")
            print(f"  - 成功提取: {success_count} 篇")
            print(f"  - 跳过已抓取: {skipped_count} 篇")
            print(f"  - 输出目录: {ARTICLES_DIR.absolute()} (按发布日期分目录)")
            print(f"  - 索引文件: {INDEX_FILE.absolute()}")
            print("=" * 60)
            
        finally:
            browser.close()


if __name__ == "__main__":
    main()

