# Anthropic Digest

追踪 Anthropic 官网 news/research 文章，通过 sitemap.xml 抓取 → BeautifulSoup 提取正文 → SQLite 存储，由 Hermes cron job 编排三步隔离反思流水线（Draft → Critique → Refine）生成高质量中文周报。

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  Hermes Cron Jobs                                           │
│                                                             │
│  ┌─────────────────────┐    ┌─────────────────────────────┐ │
│  │ Blog Fetch (2x/wk)  │    │ Weekly Digest (Fri 8pm)    │ │
│  │ Mon 10:00            │    │                             │ │
│  │ Thu 10:00            │    │  script: digest query       │ │
│  │                     │    │       ↓ JSON 注入           │ │
│  │ sitemap.xml 解析     │    │  Agent 编排 delegate_task   │ │
│  │ + BS4 正文提取       │    │       ↓                     │ │
│  │       ↓             │    │  ┌──────────────────────┐   │ │
│  │    SQLite DB        │    │  │ Subagent 1: Draft    │   │ │
│  └─────────────────────┘    │  │ (看得到原始文章)       │   │ │
│                             │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │  ┌──────────────────────┐   │ │
│                             │  │ Subagent 2: Critique │   │ │
│                             │  │ (只看得到初稿，隔离) │   │ │
│                             │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │  ┌──────────────────────┐   │ │
│                             │  │ Subagent 3: Refine   │   │ │
│                             │  │ (初稿 + 审稿意见)    │   │ │
│                             │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │  ┌──────────────────────┐   │ │
│                             │  │ Step 4: Save Summary │   │ │
│                             │  │ (终稿写入 SQLite DB) │   │ │
│                             │  └──────────┬───────────┘   │ │
│                             │             ↓               │ │
│                             │     最终周报 → Telegram     │ │
│                             └─────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## 文件结构

```
~/.hermes/hermes-agent/anthropic-digest/
├── fetcher.py              # 抓取层：sitemap.xml → BS4 提取正文 → SQLite
├── digest_generate.py      # 摘要层：数据加载 + 三步 Prompt 模板输出
├── db.py                   # 数据层：共享 DB schema 和连接管理
├── data/
│   └── anthropic.db        # SQLite 数据库（15 篇文章，5 期周报）
└── README.md

~/.hermes/scripts/
├── anthropic_fetch.py      # Cron 包装：调用 fetcher.py fetch
└── anthropic_digest.py     # Cron 包装：调用 digest_generate.py query
```

## 追踪的内容

| 来源 | URL | 说明 |
|------|-----|------|
| News | anthropic.com/news/* | 产品更新、公司公告 |
| Research | anthropic.com/research/* | 研究论文、技术报告 |

通过 `sitemap.xml` 发现新文章，用 `lastmod` 日期过滤。依赖 `beautifulsoup4`（`pip install beautifulsoup4`）提取正文。

## 核心文件说明

### db.py

共享数据库初始化模块。自动建表，提供 `get_conn()` 连接工厂。

| 功能 | 说明 |
|------|------|
| `get_conn()` | 获取 SQLite 连接，自动建表，启用 WAL 模式 |

### fetcher.py

通过 Anthropic 官网 sitemap.xml 发现文章，用 BeautifulSoup 提取正文。依赖 `beautifulsoup4`。

**命令：**

| 命令 | 说明 |
|------|------|
| `fetch [--days 14]` | 抓取最近 N 天的新文章，存入 SQLite |
| `list [--days 14]` | 列出数据库中最近的文章 |

**抓取流程：**
1. 请求 `https://www.anthropic.com/sitemap.xml`
2. 筛选 `/news/xxx` 和 `/research/xxx` URL（排除列表页和 team pages）
3. 按 `lastmod` 日期过滤
4. 逐篇 GET 文章页面，用 BS4 提取 title / summary / content
5. 正文截断到 8000 字符，存入 SQLite

### digest_generate.py

数据加载 + 三步 Prompt 模板输出。不调用 LLM，LLM 调用由 Hermes cron agent 通过 delegate_task 完成。

**命令：**

| 命令 | 说明 |
|------|------|
| `query [--days 14]` | 输出文章数据 + 三步 Prompt 模板 JSON |
| `save-summary [--days 14]` | 从 stdin 保存摘要到 DB |
| `stats` | 简要统计 |

**Prompt 风格：** 分析师视角，强调跨文章洞察、竞品对比（vs OpenAI/Google DeepMind/Meta）、行动建议。不是简单摘要，而是告诉读者"看不到的东西"。

**query 输出 JSON 结构：**
```json
{
  "meta": { "date", "days", "article_count", "categories" },
  "articles": [ "文章列表（flat list，content 截断到 1500 字符为 content_preview）" ],
  "prompts": {
    "draft": "完整的初稿 Prompt（文章数据已嵌入）",
    "critique_template": "审稿模板（{draft} 占位符）",
    "refine_template": "精修模板（{draft} + {critique} 占位符）"
  }
}
```

## 三步隔离反思设计

核心思想：审稿人看不到原始数据，只能评估摘要质量。

| 步骤 | Subagent | 输入 | 输出 | 隔离 |
|------|----------|------|------|------|
| Draft | #1 | 原始文章数据 + 分析师 Prompt | 初稿 | 看得到原始文章 |
| Critique | #2 | 只有初稿 | 审稿意见 + A/B/C 评分 | 看不到原始文章 |
| Refine | #3 | 初稿 + 审稿意见 | 终稿 | 看不到原始文章 |

每个 subagent 通过 Hermes `delegate_task` 创建，天然上下文隔离。

## 数据库结构

SQLite（`data/anthropic.db`），2 张表：

| 表 | 说明 |
|----|------|
| articles | url (PK), title, date, category, summary, content, fetched_at |
| digests | id (PK), date, days_back, article_count, content, created_at |

## Cron Jobs

| Job | 时间 (PST) | 说明 |
|-----|-----------|------|
| Anthropic Blog Fetch | 周一、四 10:00 | 抓取 sitemap → 新文章入库 |
| Anthropic Weekly Digest | 周五 20:00 | 三步反思生成周报，保存到 DB，发送到 Telegram |

采用周报而非日报，因为 Anthropic 发文频率较低。

## 手动使用

```bash
cd ~/.hermes/hermes-agent/anthropic-digest

# 抓取最近 14 天文章
python3 fetcher.py fetch --days 14

# 列出已抓取的文章
python3 fetcher.py list --days 30

# 查看统计
python3 digest_generate.py stats

# 生成 digest JSON（不调 LLM）
python3 digest_generate.py query --days 14
```

## 迁移说明

从 OpenClaw workspace 迁移而来。主要改动：

- `digest.py`（OpenClaw Gateway LLM 调用）→ `digest_generate.py`（输出 JSON + Prompt 模板）
- LLM 调用改由 Hermes delegate_task 完成
- 抓取频率从每天改为每周两次（Anthropic 发文频率低）
- 新增三步反思架构（原来是单次 LLM 调用）
- Cron 脚本必须是 .py（Hermes scheduler 固定用 Python 解释器执行）
- Cron 脚本必须放在 `~/.hermes/scripts/`（路径校验限制）

## 已知限制

- Anthropic 可能更改 sitemap.xml 结构或页面 HTML，导致抓取失败
- BeautifulSoup 依赖需要额外安装（`pip install beautifulsoup4`）
- 文章正文截断到 8000 字符，极长的研究论文可能丢失细节
- 三步 delegate_task 串行执行，生成周报需要几分钟
