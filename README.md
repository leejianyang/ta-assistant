# The Athletic 文章爬虫

使用 Python + Playwright 框架提取 The Athletic 网站上的 headline 文章内容，并使用 DeepSeek LLM 生成中文摘要。

## 功能特性

- Cookie 认证，支持付费内容访问
- 自动提取文章标题、作者、发布日期、正文
- 按发布日期分目录存储
- 文章去重，避免重复抓取
- 集成 DeepSeek LLM 自动生成中文摘要
- 支持本地调试和 GitHub Actions 自动化运行

---

## 方式一：本地运行（调试开发）

### 1. 安装依赖

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器（推荐 Firefox，登录体验更好）
playwright install firefox
# 或者安装 Chromium
playwright install chromium
```

### 2. 配置环境变量

创建 `.env` 文件：

```bash
echo "DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx" > .env
```

### 3. 登录并保存 Cookie（首次运行）

```bash
python scraper.py --login
```

这会打开**有头浏览器**，请在浏览器中手动登录你的 The Athletic 付费账户。登录成功后，回到终端按 Enter 键保存 Cookie 到 `auth_state.json`。

> 注意：Cookie 可能会过期，如果爬取失败提示未登录，请重新执行此命令。

### 4. 运行爬虫

```bash
# 正常运行（有头浏览器，可观察过程）
python scraper.py

# 调试模式：只抓取第一篇文章
python scraper.py --debug

# 保存 HTML 文件用于调试页面结构
python scraper.py --save-html

# 组合使用
python scraper.py --debug --save-html
```

### 5. 生成摘要

```bash
# 生成昨天文章的摘要
python summary.py

# 强制重新生成（即使摘要文件已存在）
python summary.py --force
```

### 命令行参数说明

**scraper.py:**

| 参数 | 说明 |
|------|------|
| `--login` | 手动登录模式，打开浏览器保存 Cookie |
| `--debug` | 调试模式，只抓取第一篇文章 |
| `--save-html` | 保存 HTML 文件到 `articles/` 目录用于调试 |

**summary.py:**

| 参数 | 说明 |
|------|------|
| `--force` | 强制重新生成摘要（即使已存在） |

---

## 方式二：GitHub Actions 自动化运行

### 1. 准备工作

将项目 Push 到 GitHub 仓库。

### 2. 配置 GitHub Secrets

在仓库的 **Settings → Secrets and variables → Actions** 中添加以下 Secrets：

| Secret 名称 | 说明 | 获取方式 |
|------------|------|---------|
| `AUTH_STATE_JSON` | 认证状态 JSON | 本地登录后执行 `cat auth_state.json` 复制内容 |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 从 DeepSeek 控制台获取 |

### 3. 工作流配置

项目包含两个独立的工作流：

| 工作流 | 文件 | 运行频率 | 说明 |
|-------|------|---------|------|
| Scrape Articles | `.github/workflows/scrape.yml` | 每 2 小时 | 抓取新文章 |
| Generate Summary | `.github/workflows/summary.yml` | 北京时间每天 8:00 | 生成昨日摘要 |

两个工作流都支持在 Actions 页面手动触发。

### 4. 工作流执行流程

**Scrape Articles（每 2 小时）：**
1. 检出代码
2. 安装 Python 和 Playwright Chromium
3. 从 Secrets 恢复 `auth_state.json`
4. 运行爬虫（headless 模式）
5. 将新文章提交回仓库

**Generate Summary（每天早上 8 点）：**
1. 检出代码并拉取最新更改
2. 安装 Python 依赖
3. 从 Secrets 创建 `.env` 文件
4. 运行摘要生成器
5. 将摘要文件提交回仓库

### 5. 手动触发运行

1. 打开 GitHub 仓库页面
2. 点击 **Actions** 标签
3. 选择 **Daily Athletic Scraper** 工作流
4. 点击 **Run workflow** 按钮

### 6. 更新 Cookie

当 Cookie 过期导致爬取失败时：

1. 本地运行 `python scraper.py --login` 重新登录
2. 执行 `cat auth_state.json` 复制内容
3. 更新 GitHub Secret `AUTH_STATE_JSON`

---

## 项目结构

```
athletic/
├── .github/workflows/
│   ├── scrape.yml          # 爬虫工作流（每2小时）
│   └── summary.yml         # 摘要工作流（每天早8点）
├── articles/               # 文章存储目录
│   ├── 20260205/          # 按发布日期分目录
│   │   └── *.json         # 文章 JSON 文件
│   └── index.json         # 文章索引（用于去重）
├── summary/               # 摘要存储目录
│   └── *_summary.txt      # 每日摘要文件
├── .env                   # 环境变量（本地使用，不提交）
├── .gitignore            # Git 忽略配置
├── auth_state.json       # 认证状态（敏感，不提交）
├── prompt.txt            # LLM 提示词模板
├── requirements.txt      # Python 依赖
├── scraper.py           # 爬虫主程序
├── summary.py           # 摘要生成程序
└── README.md            # 说明文档
```

## 输出文件说明

### 文章 JSON 格式

```json
{
  "title": "文章标题",
  "url": "原文链接",
  "author": "作者",
  "published_date": "发布日期",
  "content": "正文内容",
  "paragraph_count": 段落数
}
```



## 注意事项

- 请确保你有 The Athletic 的**付费订阅账户**
- `auth_state.json` 和 `.env` 包含敏感信息，已在 `.gitignore` 中忽略，**请勿手动提交**
- Cookie 可能会过期（通常几周到几个月），需要定期更新
- 请合理使用，避免频繁请求对服务器造成压力

## License

MIT
