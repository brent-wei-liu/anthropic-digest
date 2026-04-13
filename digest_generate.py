#!/usr/bin/env python3
"""
Anthropic Blog Digest Generator — outputs article data + prompt template.

Unlike other digests, Anthropic digest uses a single high-quality prompt
(not 3-step) because articles are fewer but longer. The prompt already
includes analyst-level instructions.

Usage:
  python3 digest_generate.py query [--days 14]
  python3 digest_generate.py save-summary [--days 14]  # stdin
  python3 digest_generate.py stats
"""

import json
import sys
from datetime import datetime, timezone, timedelta

from db import get_conn


DIGEST_PROMPT = """你是一位资深 AI 行业分析师，为 AI 从业者和工程领导者撰写 Anthropic 动态周报。你的读者不需要"总结"——他们需要你告诉他们**看不到的东西**。

数据（Anthropic 最近 {days} 天的文章）：
{articles_json}

输出格式：

# Anthropic 动态摘要 - {date}
（过去 {days} 天，共 {count} 篇文章）

## 🔮 本期洞察 (Analyst Take)
这是 digest 的核心，放在最前面。3-5 条独立洞察，每条要求：
- **跨文章连接**：把看似不相关的文章串联起来，揭示 Anthropic 的战略意图
- **对比竞品**：和 OpenAI/Google DeepMind/Meta 的同期动作对比，指出差异化策略
- **可验证的预测或推演**：每条洞察至少包含一个具体的推测或可验证的判断，而不是"这很重要"的变体
- **指出沉默**：Anthropic 没做什么、没说什么，有时比他们做了什么更有意义
- **挑战叙事**：如果 Anthropic 的公关叙事有矛盾或可疑之处，直接指出

❌ 不要写："这体现了 Anthropic 对安全的重视" — 这是废话
❌ 不要写："值得关注" — 要说清楚为什么值得关注，以及接下来会发生什么
✅ 要写：具体的因果推演、竞争格局分析、被忽视的信号、隐含的战略赌注

## 📰 文章速览
按分类列出文章（产品更新/研究/安全与政策/其他），每篇：
- 标题 + 1-2 句核心内容（不是摘要复述，是"这篇文章的一句话要点"）
- → 意味着什么：一句话说清楚对从业者的实际影响
- 附原文链接

## ⚡ 给从业者的行动建议
基于本期内容，给 AI 工程师/研究者 2-3 条具体的行动建议。不要泛泛的"关注 X 方向"，要具体到"如果你在做 Y，应该考虑 Z"。

规则：
- 全部中文，专有名词保留英文
- 没有相关文章的分类直接省略
- 简洁，适合手机阅读
- 有立场、有态度、有原创判断。分析师不是搬运工
- 只输出 digest 正文"""


def cmd_query(conn, args):
    days = 14
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = conn.execute(
        """SELECT url, title, date, category, summary, content
           FROM articles WHERE date >= ? ORDER BY date DESC""",
        (cutoff,),
    ).fetchall()

    articles = [dict(r) for r in rows]

    if not articles:
        print(json.dumps({"error": f"No articles in last {days} days. Run: python3 fetcher.py fetch --days {days}"}))
        return

    # Compact version for prompt (truncate content)
    compact = []
    for a in articles:
        entry = {
            "title": a["title"],
            "url": a["url"],
            "date": a["date"],
            "category": a["category"],
            "summary": a["summary"] or "",
        }
        if a["content"]:
            entry["content_preview"] = a["content"][:1500]
        compact.append(entry)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    articles_json = json.dumps(compact, ensure_ascii=False, indent=2)

    draft_prompt = DIGEST_PROMPT.format(
        days=days,
        articles_json=articles_json,
        date=today,
        count=len(articles),
    )

    critique_template = """你是一位资深科技编辑。请审阅以下 Anthropic 动态摘要初稿，给出改进建议。

## 初稿

{draft}

## 审稿要求

1. "本期洞察" 是否有真正的分析深度？还是只是换了个说法的摘要？
2. 跨文章连接是否有说服力？因果推演是否站得住脚？
3. 竞品对比是否准确、具体？
4. 行动建议是否足够具体、可执行？
5. 有没有遗漏重要文章？

请按 A/B/C 评级：
- A：洞察深刻，可以直接发布
- B：分析有价值但需改进
- C：流于表面，需要大幅重写

给出具体修改建议。"""

    refine_template = """你是 Anthropic 动态摘要的终稿编辑。请根据审稿意见修改初稿，生成终稿。

## 初稿

{draft}

## 审稿意见

{critique}

## 要求

1. 根据审稿意见逐条修改
2. 保持原有格式和链接
3. 如果审稿评级为 A，只做微调
4. 如果评级为 B/C，按建议大幅修改，特别加强洞察深度
5. 终稿直接输出，不要包含修改说明"""

    output = {
        "meta": {
            "date": today,
            "days": days,
            "article_count": len(articles),
            "categories": list(set(a["category"] for a in articles)),
        },
        "articles": compact,
        "prompts": {
            "draft": draft_prompt,
            "critique_template": critique_template,
            "refine_template": refine_template,
        },
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_save_summary(conn, args):
    content = sys.stdin.read().strip()
    if not content:
        print('{"error": "no content on stdin"}')
        return
    days = 14
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).isoformat()
    article_count = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE date >= ?",
        ((datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d"),)
    ).fetchone()[0]

    conn.execute(
        "INSERT INTO digests (date, days_back, article_count, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (today, days, article_count, content, now),
    )
    conn.commit()
    print(json.dumps({"saved": True, "date": today, "days": days, "article_count": article_count}))


def cmd_stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    digests = conn.execute("SELECT COUNT(*) FROM digests").fetchone()[0]
    latest = conn.execute("SELECT MAX(date) FROM articles").fetchone()[0]
    print(json.dumps({
        "total_articles": total,
        "total_digests": digests,
        "latest_article": latest,
    }, indent=2))


def main():
    conn = get_conn()

    if len(sys.argv) < 2 or sys.argv[1] == "query":
        cmd_query(conn, sys.argv[2:] if len(sys.argv) > 2 else [])
    elif sys.argv[1] == "save-summary":
        cmd_save_summary(conn, sys.argv[2:])
    elif sys.argv[1] == "stats":
        cmd_stats(conn)
    else:
        print(__doc__)
        sys.exit(1)

    conn.close()


if __name__ == "__main__":
    main()
