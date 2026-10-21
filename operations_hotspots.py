"""Read public Douyin ranking clues without accounts, models or schedulers.

The ranking adapter follows the public SSR source already used by 星尚.
Publication time, aggregator snapshot time and retrieval time remain distinct.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
import urllib.robotparser
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Asia/Shanghai")
UA = "VideoWorkbenchOperations/1.0"
MAX_BYTES = 2_000_000
# Titles are discovery evidence, not proof of a video's mechanics or audience.
FOOD_WORDS = ("美食", "卤肉", "滷肉", "卤味", "米饭", "工作餐", "午餐", "晚餐", "早餐", "午饭", "吃饭", "一人食", "外卖", "小吃", "探店", "餐厅", "下饭", "饭搭子", "食堂", "拌饭", "热饭", "面要多吃", "夜宵", "火锅", "咖啡", "奶茶", "糖葫芦", "甜品", "烤肉", "炸鸡", "面条", "螺蛳粉")
FORMAT_WORDS = ("转场", "滑镜", "运镜", "卡点", "定格", "一镜到底", "慢动作", "微缩", "延时摄影", "第一视角", "ASMR", "asmr", "沉浸式", "无声vlog", "原声", "变装")
LIFE_WORDS = ("午休", "下班", "上班", "打工人", "打工人的", "打工日常", "开学", "返校", "返工", "毕业季", "通勤", "搭子", "周末去哪", "城市漫步", "citywalk")
EXCLUDED = ("事故", "遇难", "死亡", "身亡", "中毒", "食品安全", "塌方", "战争", "军演", "抗议", "判刑", "宣判", "被捕", "爆发冲突", "骚乱", "股市", "黄金", "股价", "基金", "经济新", "GDP", "国家数据", "造假", "黑心", "被罚", "饭圈", "饭撒", "娃圈", "楼市", "营收", "财报", "假冒", "爆雷")
PLATFORMS = {"douyin": ("抖音热榜", "douyin.com")}
SELECTION_POLICY = "douyin_restaurant_clues_v2"


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _text(value: str) -> str:
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", value or "", flags=re.I | re.S)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value))).strip()


def _id(source: str, value: str) -> str:
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).strip("#＃")
    return hashlib.sha256(f"{source}:{normalized}".encode()).hexdigest()[:24]


def _safe_url(url: str, domain: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and (parsed.hostname == domain or (parsed.hostname or "").endswith("." + domain))


def _relevance(item: dict) -> dict:
    text = item["title"]
    food = [word for word in FOOD_WORDS if word in text]
    form = [word for word in FORMAT_WORDS if word in text]
    life = [word for word in LIFE_WORDS if word in text]
    excluded = [word for word in EXCLUDED if word in text]
    category = None if excluded else "food" if food else "format" if form else "daily_life" if life else None
    item.update(matched_keywords=(food + form + life)[:8], topic_fit=category or "general",
                content_category={"food": "吃什么", "format": "怎么拍", "daily_life": "生活场景"}.get(category),
                selection_evidence="title_keywords", excluded_keywords=excluded)
    return item


def select_trends(items: list[dict]) -> list[dict]:
    # Never fill a restaurant feed with unrelated national news when clues are few.
    selected = [_relevance(dict(item)) for item in items if item.get("source_id") == "soso_douyin"
                and item.get("kind") == "platform_hotspot"]
    selected = [item for item in selected if item["content_category"]]
    return sorted(selected, key=lambda item: (item.get("freshness") != "recent", item.get("rank", 999), item["id"]))[:20]


def _error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.RequestError):
        return "网络连接失败"
    return str(exc)[:120] if isinstance(exc, ValueError) else type(exc).__name__


class PublicReader:
    def __init__(self):
        self.client = httpx.Client(timeout=httpx.Timeout(12, connect=7), follow_redirects=True,
                                   trust_env=False, headers={"User-Agent": UA})
        self.rules: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def close(self):
        self.client.close()

    def _get(self, url: str) -> httpx.Response:
        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("公开来源响应超过大小上限")
            headers = {key: value for key, value in response.headers.items() if key not in {"content-encoding", "content-length"}}
            return httpx.Response(response.status_code, headers=headers, content=bytes(body),
                                  request=response.request)

    def fetch(self, url: str) -> str:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.rules:
            try:
                robots = self._get(origin + "/robots.txt")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 410}:
                    raise
                self.rules[origin] = None
            else:
                if "<html" in robots.text.lower():
                    raise ValueError("robots.txt 返回网页，无法确认采集规则")
                rule = urllib.robotparser.RobotFileParser()
                rule.parse(robots.text.splitlines())
                self.rules[origin] = rule
        rule = self.rules[origin]
        if rule and not rule.can_fetch(UA, url):
            raise ValueError("来源 robots.txt 不允许采集此页面")
        return self._get(url).text


def _collect_rankings() -> tuple[list[dict], list[dict]]:
    reader = PublicReader()
    items, sources = [], []
    try:
        page = reader.fetch("https://soso.re/")
        fetched_at = _now()
        chunks = []
        for match in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)</script>", page, re.S):
            try:
                entry = json.loads(match[1])
            except json.JSONDecodeError:
                continue
            if len(entry) > 1 and isinstance(entry[1], str):
                chunks.append(entry[1])
        flight = "".join(chunks)
        marker = re.search(r'"initialSnapshots"\s*:\s*', flight)
        if not marker:
            raise ValueError("公开榜单页面结构变化")
        snapshots, _ = json.JSONDecoder().raw_decode(flight[marker.end():])
        if not isinstance(snapshots, list):
            raise ValueError("公开榜单快照不是数组")
        for platform, (label, domain) in PLATFORMS.items():
            sid = "soso_" + platform
            source = {"id": sid, "name": "搜搜热 · " + label, "status": "failed", "fetched_at": fetched_at}
            sources.append(source)
            snapshot = next((x for x in snapshots if isinstance(x, dict) and x.get("source") == platform), None)
            if not snapshot or snapshot.get("ok") is not True:
                source["error"] = "聚合来源未返回有效榜单"
                continue
            timestamp = snapshot.get("timestamp")
            try:
                if not isinstance(timestamp, (float, int)) or isinstance(timestamp, bool):
                    raise ValueError()
                observed = datetime.fromtimestamp(timestamp / 1000, TZ)
            except (ValueError, TypeError, OverflowError, OSError):
                source["error"] = "榜单快照时间缺失或无效"
                continue
            age = (datetime.now(TZ) - observed).total_seconds()
            source["source_updated_at"] = observed.isoformat(timespec="seconds")
            if age < -300 or age > 86400:
                source["status"] = "stale"
                source["error"] = "榜单快照超过24小时或时间异常，未作为当前热点导入"
                continue
            source["status"] = "ok" if age <= 7200 else "stale"
            source["count"] = 0
            for position, row in enumerate(snapshot.get("items", []), 1):
                if not isinstance(row, dict):
                    continue
                title, url = row.get("title"), row.get("url")
                if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not _safe_url(url, domain):
                    continue
                desc = _text(row["desc"]) if isinstance(row.get("desc"), str) else ""
                if re.sub(r"[#＃\s]", "", desc) == re.sub(r"[#＃\s]", "", title):
                    desc = ""
                items.append(_relevance({
                    "id": _id(sid, title), "title": title.strip(), "summary": desc[:240], "url": url,
                    "source": source["name"], "source_id": sid, "published_at": None,
                    "fetched_at": fetched_at, "observed_at": source["source_updated_at"],
                    "time_basis": "aggregator_snapshot", "kind": "platform_hotspot", "rank": position,
                    "rank_basis": "aggregator_list_position", "freshness": "recent" if age <= 7200 else "stale",
                }))
                source["count"] += 1
            if not source["count"]:
                source.update(status="failed", error="榜单中没有可读取的条目")
    except Exception as exc:
        sources = [{"id": "soso_" + key, "name": "搜搜热 · " + value[0], "status": "failed",
                    "error": _error(exc), "fetched_at": _now()} for key, value in PLATFORMS.items()]
    finally:
        reader.close()
    return items, sources



def collect_hotspots() -> dict:
    items, sources = _collect_rankings()
    selected = select_trends(items)
    for source in sources:
        source["selected_count"] = sum(row["source_id"] == source["id"] for row in selected)
    return {"items": selected, "sources": sources, "fetched_at": _now(),
            "candidate_count": len(items), "selection_basis": SELECTION_POLICY,
            "coverage": {"douyin": "public_general_board_filtered", "xiaohongshu": "not_connected"}}
