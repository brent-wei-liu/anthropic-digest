#!/usr/bin/env python3
"""
Anthropic Blog Fetcher — 抓取 anthropic.com/news 和 /research 文章。

策略：解析 sitemap.xml 获取文章列表和日期，再逐篇抓取正文。

Usage:
  python3 fetcher.py fetch [--days 14]    # 抓取最近 N 天的文章
  python3 fetcher.py list [--days 14]     # 列出数据库中最近的文章
"""

import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: 需要 beautifulsoup4。运行: pip install beautifulsoup4", file=sys.stderr)
    sys.exit(1)

from db import get_conn

SITEMAP_URL = "https://www.anthropic.com/sitemap.xml"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
FETCH_DELAY = 1.0  # 每篇文章之间的延迟（秒）


# ── Sitemap 解析 ─────────────────────────────────────────────────────

def fetch_url(url, timeout=30):
    """GET 请求，返回 bytes。"""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_sitemap(days=14):
    """从 sitemap.xml 解析 /news/ 和 /research/ 文章链接。"""
    print(f"正在获取 sitemap.xml ...", file=sys.stderr)
    xml_bytes = fetch_url(SITEMAP_URL)
    root = ET.fromstring(xml_bytes)

    # sitemap 的 XML namespace
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    articles = []
    for url_elem in root.findall("sm:url", ns):
        loc = url_elem.findtext("sm:loc", "", ns)
        lastmod = url_elem.findtext("sm:lastmod", "", ns)

        # 只要 /news/xxx 和 /research/xxx（排除列表页本身）
        if not re.search(r"/(news|research)/[^/]+$", loc):
            continue

        # 跳过 team pages
        if "/research/team/" in loc:
            continue

        if lastmod and lastmod < cutoff:
            continue

        category = "research" if "/research/" in loc else "news"
        articles.append({"url": loc, "date": lastmod, "category": category})

    print(f"Sitemap 中找到 {len(articles)} 篇近 {days} 天文章", file=sys.stderr)
    return articles


# ── 文章正文抓取 ─────────────────────────────────────────────────────

def extract_article(html_bytes, url):
    """用 BeautifulSoup 从文章页面提取标题、摘要和正文。"""
    soup = BeautifulSoup(html_bytes, "html.parser")

    # 标题：优先 og:title，其次 h1
    title = ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", "")
    if not title:
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else ""

    # 摘要：og:description
    summary = ""
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        summary = og_desc.get("content", "")

    # 发布日期：优先 article:published_time，其次 time 标签
    pub_date = ""
    og_date = soup.find("meta", property="article:published_time")
    if og_date:
        pub_date = og_date.get("content", "")[:10]
    if not pub_date:
        time_tag = soup.find("time")
        if time_tag:
            dt = time_tag.get("datetime", "")
            pub_date = dt[:10] if dt else time_tag.get_text(strip=True)

    # 正文：找 article 或 main 标签内的文本
    content = ""
    article_tag = soup.find("article") or soup.find("main")
    if article_tag:
        # 移除 script、style、nav、header、footer
        for tag in article_tag.find_all(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        paragraphs = article_tag.find_all(["p", "h2", "h3", "li"])
        content = "\n".join(p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True))

    # 截断过长的正文
    if len(content) > 8000:
        content = content[:8000] + "..."

    return {
        "title": title,
        "summary": summary,
        "pub_date": pub_date,
        "content": content,
    }


# ── Fetch 命令 ───────────────────────────────────────────────────────

def cmd_fetch(days=14):
    """抓取最近文章并存入数据库。"""
    conn = get_conn()
    articles = parse_sitemap(days=days)

    if not articles:
        print("没有找到需要抓取的文章。", file=sys.stderr)
        return

    # 过滤已存在的
    existing = set(
        r[0] for r in conn.execute("SELECT url FROM articles").fetchall()
    )
    new_articles = [a for a in articles if a["url"] not in existing]
    print(f"其中 {len(new_articles)} 篇是新文章，开始抓取...", file=sys.stderr)

    now = datetime.now(timezone.utc).isoformat()
    fetched = 0

    for i, art in enumerate(new_articles):
        url = art["url"]
        print(f"  [{i+1}/{len(new_articles)}] {url}", file=sys.stderr)

        try:
            html = fetch_url(url)
            info = extract_article(html, url)

            date = (info["pub_date"] or art["date"] or "")[:10]  # 只保留 YYYY-MM-DD

            conn.execute(
                """INSERT OR REPLACE INTO articles (url, title, date, category, summary, content, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (url, info["title"], date, art["category"], info["summary"], info["content"], now),
            )
            conn.commit()
            fetched += 1

        except (URLError, HTTPError) as e:
            print(f"    跳过（网络错误）: {e}", file=sys.stderr)
        except Exception as e:
            print(f"    跳过（解析错误）: {e}", file=sys.stderr)

        if i < len(new_articles) - 1:
            time.sleep(FETCH_DELAY)

    print(f"\n完成！共抓取 {fetched} 篇新文章。数据库共 {conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]} 篇。",
          file=sys.stderr)
    conn.close()


# ── List 命令 ────────────────────────────────────────────────────────

def cmd_list(days=14):
    """列出数据库中最近的文章。"""
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    rows = conn.execute(
        "SELECT date, category, title, url FROM articles WHERE date >= ? ORDER BY date DESC",
        (cutoff,),
    ).fetchall()

    if not rows:
        print(f"数据库中没有最近 {days} 天的文章。先运行: python3 fetcher.py fetch", file=sys.stderr)
        return

    print(f"\n最近 {days} 天的文章（共 {len(rows)} 篇）：\n")
    for r in rows:
        print(f"  [{r['date']}] [{r['category']:8s}] {r['title']}")
        print(f"    {r['url']}")

    conn.close()


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    # 解析 --days
    days = 14
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            days = int(args[i + 1])

    if cmd == "fetch":
        cmd_fetch(days=days)
    elif cmd == "list":
        cmd_list(days=days)
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
