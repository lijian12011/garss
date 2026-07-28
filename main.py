"""
garss main.py — 完全基于 garssInfo.json 驱动
不再依赖 EditREADME.md 作为源配置，直接读取 garssInfo.json 拉取 RSS 并生成 README.md
"""

import time
import os
import json
import email.utils
import urllib.request
from datetime import datetime
from urllib.parse import urlparse, urljoin, quote
from zoneinfo import ZoneInfo
from multiprocessing import Pool, Manager

try:
    import feedparser
except ImportError:
    feedparser = None

try:
    import requests
except ImportError:
    requests = None

try:
    import yagmail
except ImportError:
    yagmail = None

import xml.etree.ElementTree as ET


# ===== GARSS Studio RSSHub 代理相关 =====

GARSS_STUDIO_ACCESS_TOKEN = ""


def get_garss_studio_base_url():
    return os.environ.get("GARSS_STUDIO_BASE_URL", "http://127.0.0.1:25173").rstrip("/")


def get_garss_studio_access_code():
    return os.environ.get("GARSS_STUDIO_ACCESS_CODE") or os.environ.get("ACCESS_CODE") or "banana"


def is_garss_studio_rsshub_url(feed_url):
    parse_result = urlparse(feed_url)
    return parse_result.scheme in ["http", "https"] and parse_result.netloc in ["rsshub:1200", "rsshub.v2fy.com"]


def get_rsshub_route_path(feed_url):
    parse_result = urlparse(feed_url)
    route_path = parse_result.path or "/"
    if parse_result.query:
        route_path = route_path + "?" + parse_result.query
    return route_path


def get_garss_studio_access_token():
    global GARSS_STUDIO_ACCESS_TOKEN
    if GARSS_STUDIO_ACCESS_TOKEN:
        return GARSS_STUDIO_ACCESS_TOKEN
    login_url = urljoin(get_garss_studio_base_url() + "/", "api/auth/login")
    response_data = http_post_json(login_url, {"accessCode": get_garss_studio_access_code()}, 8)
    GARSS_STUDIO_ACCESS_TOKEN = response_data["token"]
    return GARSS_STUDIO_ACCESS_TOKEN


# ===== HTTP 工具 =====

def http_get_content(url, timeout, headers):
    if requests:
        response = requests.get(url, timeout=timeout, headers=headers)
        response.raise_for_status()
        return response.content
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def http_post_json(url, body, timeout):
    encoded_body = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=encoded_body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_feed_url_content(feed_url, timeout, headers):
    if not is_garss_studio_rsshub_url(feed_url):
        return http_get_content(feed_url, timeout, headers)
    route_path = get_rsshub_route_path(feed_url)
    fetch_url = urljoin(get_garss_studio_base_url() + "/", "api/rsshub/fetch") + "?routePath=" + quote(route_path, safe="")
    return http_get_content(fetch_url, timeout, {
        **headers,
        "Authorization": "Bearer " + get_garss_studio_access_token(),
    })


# ===== RSS 解析 =====

def parse_entry_date(value):
    try:
        parsed_date = email.utils.parsedate_to_datetime(value or "")
        if parsed_date:
            return parsed_date.strftime("%Y-%m-%d")
    except:
        pass
    return datetime.today().strftime("%Y-%m-%d")


def parse_feed_entries_with_stdlib(feed_url_content):
    root = ET.fromstring(feed_url_content)
    entries = []
    if root.tag.endswith("rss") or root.find("./channel") is not None:
        for item in root.findall("./channel/item"):
            title = item.findtext("title", default="")
            link = item.findtext("link", default="")
            published = item.findtext("pubDate", default="") or item.findtext("date", default="")
            entries.append({"title": title, "link": link, "date": parse_entry_date(published)})
        return entries
    namespaces = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall("./atom:entry", namespaces):
        title = entry.findtext("atom:title", default="", namespaces=namespaces)
        link_element = entry.find("atom:link", namespaces)
        link = link_element.get("href", "") if link_element is not None else ""
        published = entry.findtext("atom:published", default="", namespaces=namespaces) or entry.findtext("atom:updated", default="", namespaces=namespaces)
        entries.append({"title": title, "link": link, "date": parse_entry_date(published)})
    return entries


def parse_feed_entries(feed_url_content):
    if not feedparser:
        return parse_feed_entries_with_stdlib(feed_url_content)
    feed = feedparser.parse(feed_url_content)
    result = []
    for entry in feed["entries"]:
        try:
            date = time.strftime("%Y-%m-%d", entry["published_parsed"])
        except:
            date = datetime.today().strftime("%Y-%m-%d")
        result.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "date": date
        })
    return result


# ===== 拉取单个源 =====

def get_rss_info(feed_url, index, rss_info_list):
    result = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Content-Encoding": "gzip"
    }
    for i in range(3):
        try:
            feed_url_content = get_feed_url_content(feed_url, (i + 1) * 8, headers)
            feed_entries = parse_feed_entries(feed_url_content)
            print(f"  ✓ {feed_url} => {len(feed_entries)} 条")
            for entry in feed_entries:
                title = entry["title"].replace("\n", "").replace("\r", "")
                result.append({"title": title, "link": entry["link"], "date": entry["date"]})
            break
        except Exception as e:
            print(f"  ⚠️ {feed_url} 第{i+1}次请求出错: {e}")
    rss_info_list[index] = result
    return result


# ===== 生成 README.md =====

def generate_readme(sources, rss_info_list):
    today = datetime.today().strftime("%Y-%m-%d")
    ga_rss_datetime = datetime.fromtimestamp(int(time.time()), ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')

    lines = []
    lines.append(f"# 📰 每日技术简报 (garss, 已收集{len(sources)}个RSS源, 生成时间: {ga_rss_datetime})")
    lines.append("")
    lines.append("基于 [garss](https://github.com/zhaoolee/garss) 改造，每日自动拉取 RSS 源并生成简报。")
    lines.append("")

    # 生成今日新闻汇总
    new_num = 0
    news_lines = []

    # 按 category 分组
    categories = {}
    for i, source in enumerate(sources):
        cat = source.get("category", "未分类")
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((source, rss_info_list[i]))

    for cat, items in categories.items():
        lines.append(f"## {cat}")
        lines.append("")
        lines.append("| 源 | 最新内容 |")
        lines.append("| --- | --- |")

        for source, rss_info in items:
            title = source["title"]
            if isinstance(rss_info, list) and len(rss_info) > 0:
                first = rss_info[0]
                is_today = "🌈" if first["date"] == today else ""
                content = f"[{first['title']}]({first['link']}) {is_today} {first['date']}"
            else:
                content = "暂无更新"
            lines.append(f"| **{title}** | {content} |")

        lines.append("")

    # 今日值得看
    lines.append("---")
    lines.append("")
    lines.append(f"## 🕶️ 今日值得看 ({today})")
    lines.append("")

    for i, source in enumerate(sources):
        rss_info = rss_info_list[i]
        if not isinstance(rss_info, list):
            continue
        for entry in rss_info:
            if entry["date"] == today:
                new_num += 1
                lines.append(f"{new_num}. [{entry['title']}]({entry['link']}) — {source['title']}")

    if new_num == 0:
        lines.append("今日暂无新内容更新")

    lines.append("")
    lines.append(f"---")
    lines.append(f"")
    lines.append(f"共 **{new_num}** 篇今日新文章，来自 **{len(sources)}** 个 RSS 源。")
    lines.append("")

    return "\n".join(lines), new_num


# ===== 邮件发送 =====

def send_mail(email_list, title, content):
    if os.environ.get("GARSS_SKIP_MAIL", "") == "1":
        print("已设置 GARSS_SKIP_MAIL=1，跳过邮件发送")
        return

    if not yagmail:
        print("当前环境没有 yagmail，跳过邮件发送")
        return

    user = os.environ.get("USER", "")
    password = os.environ.get("PASSWORD", "")
    host = os.environ.get("HOST", "")

    if not user or not password:
        # 尝试读取本地 secret.json
        secret_path = os.path.join(os.getcwd(), "secret.json")
        if os.path.exists(secret_path):
            with open(secret_path, 'r') as f:
                load_dict = json.load(f)
                user = load_dict.get("user", "")
                password = load_dict.get("password", "")
                host = load_dict.get("host", "")
        else:
            print("无法获取发件人信息，跳过邮件发送")
            return

    try:
        yag = yagmail.SMTP(user=user, password=password, host=host)
        yag.send(email_list, title, content)
        print(f"✅ 邮件发送成功 -> {email_list}")
    except Exception as e:
        print(f"⚠️ 邮件发送失败: {e}")


# ===== 主函数 =====

def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)

    # 1. 读取 garssInfo.json
    with open("garssInfo.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    sources = config.get("garssInfo", [])
    print(f"📡 开始拉取 {len(sources)} 个 RSS 源...")

    # 2. 并行拉取所有源
    rss_info_list = Manager().list(range(len(sources)))
    po = Pool(min(8, len(sources) or 1))

    for index, source in enumerate(sources):
        feed_url = source["xmlUrl"]
        po.apply_async(get_rss_info, (feed_url, index, rss_info_list))

    po.close()
    po.join()

    print(f"📊 拉取完成")

    # 3. 生成 README.md
    readme_content, new_num = generate_readme(sources, rss_info_list)

    with open("README.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"✅ README.md 生成完成，今日新文章: {new_num} 篇")

    # 4. 发送邮件（可选）
    tasks_path = os.path.join(project_dir, "tasks.json")
    if os.path.exists(tasks_path):
        with open(tasks_path, "r") as f:
            tasks = json.load(f)
        email_list = [t["email"] for t in tasks.get("tasks", [])]
        if email_list and new_num > 0:
            send_mail(email_list, f"📰 每日技术简报 {datetime.today().strftime('%Y-%m-%d')}", readme_content)


if __name__ == "__main__":
    main()
