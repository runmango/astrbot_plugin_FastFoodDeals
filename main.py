import asyncio
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import httpx
from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import MessageChain, filter, AstrMessageEvent
from astrbot.api.message_components import Image as CompImage, Plain
from astrbot.api.star import Context, Star, register


# 全局 Scheduler，避免多个插件实例重复创建
_scheduler: Optional[AsyncIOScheduler] = None


def _get_scheduler() -> AsyncIOScheduler:
    """
    获取（或创建）全局 AsyncIOScheduler。
    放在这里而不是 AstrBot 内部，是为了插件能够独立运行。
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        _scheduler.start()
        logger.info("[FastFoodDeals] AsyncIOScheduler started.")
    return _scheduler


def _parse_schedule_time(schedule_time: str) -> Tuple[int, int]:
    """
    解析 "HH:MM" 格式的时间字符串，返回 (hour, minute)。
    解析失败时回退到 08:00。
    """
    try:
        parts = schedule_time.strip().split(":")
        if len(parts) != 2:
            raise ValueError("schedule_time format invalid")
        hour = int(parts[0])
        minute = int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("schedule_time value out of range")
        return hour, minute
    except Exception as e:  # noqa: BLE001
        logger.error(f"[FastFoodDeals] Invalid schedule_time '{schedule_time}', fallback to 08:00. Error: {e}")
        return 8, 0


def get_theme_for_today() -> Optional[str]:
    """
    根据当前日期返回今日适用的「特殊活动」主题，用于海报配色与背景。
    - 周四 -> 疯狂星期四（肯德基）
    - 可在此扩展：麦当劳麦乐送日、周末狂欢等。
    """
    weekday = datetime.now().weekday()  # 0=周一, 3=周四
    if weekday == 3:
        return "crazy_thursday"
    return None


def _build_group_origin(group_id: str) -> str:
    """
    将 QQ 群号转换为 AstrBot 的 unified_msg_origin。

    这里假设使用 OneBot v11 / aiocqhttp 适配器：
      aiocqhttp:group:<group_id>

    如果你的适配器不同，可以在这里进行改写。
    """
    group_id = str(group_id).strip()
    return f"aiocqhttp:group:{group_id}"


def _extract_price_from_text(text: str) -> Optional[float]:
    """从文案中尝试提取价格（元），例如 ¥32.9、32.9元、￥19.9。"""
    if not text:
        return None
    # 优先匹配 ¥/￥ 后数字
    m = re.search(r"[¥￥]\s*(\d+\.?\d*)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"(\d+\.?\d*)\s*元", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _infer_brand_from_text(text: str, target_brands: List[str]) -> Optional[str]:
    """从标题/描述中根据关键词推断品牌，用于 RSS 条目。"""
    if not text or not target_brands:
        return None
    for brand in target_brands:
        if brand and brand in text:
            return brand
    return None


async def _fetch_from_rss(
    rss_urls: List[str],
    target_brands: List[str],
) -> List[Dict[str, Any]]:
    """
    从 RSS 订阅拉取条目，按 target_brands 关键词过滤并映射为统一 deal 结构。
    支持 RSS 2.0 与 Atom 常见标签（title, link, description, pubDate 或 updated）。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    deals: List[Dict[str, Any]] = []
    seen_keys: set = set()  # 去重 (brand, title)

    async with httpx.AsyncClient(timeout=15.0) as client:
        for url in (u.strip() for u in rss_urls if u and str(u).strip().startswith("http")):
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[FastFoodDeals] RSS fetch failed {url}: {e}")
                continue

            # RSS 2.0: channel/item；Atom: feed/entry
            items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for item in items:
                def _text(tag: str, ns: Optional[str] = None) -> str:
                    if ns:
                        el = item.find(f"{{{ns}}}{tag}")
                    else:
                        el = item.find(tag) or item.find(f".//*[local-name()='{tag}']")
                    if el is None:
                        return ""
                    if tag == "link" and el.get("href"):
                        return (el.get("href") or "").strip()
                    return (el.text or "").strip()

                title = _text("title") or _text("title", "http://www.w3.org/2005/Atom")
                if not title:
                    continue
                link = _text("link") or _text("link", "http://www.w3.org/2005/Atom")
                desc = _text("description") or _text("summary", "http://www.w3.org/2005/Atom") or title
                combined = f"{title} {desc}"

                brand = _infer_brand_from_text(combined, target_brands)
                if not brand:
                    continue  # 与监控品牌无关则跳过

                price = _extract_price_from_text(combined)
                if price is None:
                    price = 0.0

                key = (brand, title[:80])
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                deals.append({
                    "date": today_str,
                    "brand": brand,
                    "title": title[:80],
                    "category": "RSS 优惠",
                    "price": price,
                    "origin_price": None,
                    "tag": "限时" if "限时" in combined or "促销" in combined else "优惠",
                    "activity": desc[:120] if desc else "",
                    "desc": desc[:200] if desc else title,
                    "main_image_url": "",
                })

    return deals


async def _fetch_from_api(
    api_url: str,
    api_method: str,
    target_brands: List[str],
) -> List[Dict[str, Any]]:
    """
    从自定义 HTTP API 拉取 JSON，期望返回数组，每项可含 brand/title/price/origin_price 等。
    若 API 返回的字段名不同，可在此做映射。
    """
    if not api_url or not api_url.strip().startswith("http"):
        logger.warning("[FastFoodDeals] api_url 未配置或无效，跳过 API 拉取")
        return []

    today_str = datetime.now().strftime("%Y-%m-%d")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            if (api_method or "get").lower() == "post":
                resp = await client.post(api_url)
            else:
                resp = await client.get(api_url)
            resp.raise_for_status()
            raw = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] API fetch failed {api_url}: {e}")
            return []

    if not isinstance(raw, list):
        raw = raw.get("data", raw.get("deals", [])) if isinstance(raw, dict) else []
    deals: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        # 兼容多种字段名
        brand = (item.get("brand") or item.get("品牌") or "").strip()
        if target_brands and brand not in target_brands:
            continue
        if not brand:
            brand = str(target_brands[0]) if target_brands else "其他"
        title = str(item.get("title") or item.get("商品") or item.get("name") or "未知商品")
        price = item.get("price")
        if price is None:
            price = item.get("到手价") or item.get("final_price")
        try:
            price = float(price) if price is not None else 0.0
        except (TypeError, ValueError):
            price = 0.0
        origin = item.get("origin_price") or item.get("原价") or item.get("original_price")
        try:
            origin_price = float(origin) if origin is not None else None
        except (TypeError, ValueError):
            origin_price = None
        deals.append({
            "date": today_str,
            "brand": brand,
            "title": title[:80],
            "category": str(item.get("category") or item.get("分类") or "优惠"),
            "price": price,
            "origin_price": origin_price,
            "tag": str(item.get("tag") or item.get("标签") or "优惠"),
            "activity": str(item.get("activity") or item.get("活动") or ""),
            "desc": str(item.get("desc") or item.get("推荐") or item.get("recommendation") or title),
            "main_image_url": str(item.get("main_image_url") or item.get("image") or ""),
        })
    return deals


async def _fetch_mock_deals(target_brands: List[str]) -> List[Dict[str, Any]]:
    """内置 Mock 数据，用于演示与测试。"""
    if not target_brands:
        target_brands = ["肯德基", "麦当劳", "德克士"]
    today_str = datetime.now().strftime("%Y-%m-%d")
    deals: List[Dict[str, Any]] = []
    base_presets = [
        {
            "title": "熔岩蛋包汁汁和牛堡套餐",
            "category": "新品推荐",
            "price": 32.9,
            "origin_price": 36.0,
            "tag": "新品上市",
            "activity": "2月24日-3月22日：新品尝鲜限时优惠",
            "desc": "软嫩滑蛋，轻薄蛋皮，120g 厚制和牛，口感多汁饱满。",
        },
        {
            "title": "人气经典鸡腿堡+薯条+饮料",
            "category": "人气热卖",
            "price": 29.9,
            "origin_price": 35.0,
            "tag": "人气热卖",
            "activity": "午市时段任选第二份半价，限堂食或外带。",
            "desc": "经典脆皮鸡腿堡搭配金黄薯条与冰爽气泡饮，工作日午餐优选。",
        },
        {
            "title": "三人分享桶（鸡翅+鸡块+薯条）",
            "category": "多人分享",
            "price": 79.0,
            "origin_price": 96.0,
            "tag": "聚会必点",
            "activity": "周末及节假日限时加赠中份薯条一份。",
            "desc": "适合三五好友小聚，丰富搭配，一桶搞定多种口味。",
        },
    ]
    for brand in target_brands:
        for idx, preset in enumerate(base_presets):
            deals.append({
                "date": today_str,
                "brand": brand,
                "title": preset["title"],
                "category": preset["category"],
                "price": preset["price"],
                "origin_price": preset["origin_price"],
                "tag": preset["tag"],
                "activity": preset["activity"],
                "desc": preset["desc"],
                "main_image_url": f"https://example.com/{brand}/menu_{idx}.jpg",
            })
    return deals


async def fetch_today_deals(
    target_brands: List[str],
    data_source: str = "mock",
    rss_urls: Optional[List[str]] = None,
    api_url: str = "",
    api_method: str = "get",
) -> List[Dict[str, Any]]:
    """
    获取“今日快餐菜单与活动”数据，数据源由配置决定。

    - data_source=mock：内置示例数据；
    - data_source=rss：从 rss_urls 拉取（如什么值得买优惠精选），按 target_brands 关键词过滤；
    - data_source=api：从 api_url 拉取 JSON 数组，字段可映射为 brand/title/price 等。

    返回结构每条：brand, title, category, price, origin_price, tag, activity, desc, main_image_url, date。
    """
    if not target_brands:
        target_brands = ["肯德基", "麦当劳", "德克士"]
    source = (data_source or "mock").strip().lower()

    if source == "rss":
        urls = rss_urls or []
        if urls:
            return await _fetch_from_rss(urls, target_brands)
        logger.warning("[FastFoodDeals] data_source=rss 但 rss_urls 为空，回退到 mock")
    elif source == "api":
        if api_url and str(api_url).strip().startswith("http"):
            return await _fetch_from_api(api_url.strip(), api_method or "get", target_brands)
        logger.warning("[FastFoodDeals] data_source=api 但 api_url 未配置或无效，回退到 mock")
    return await _fetch_mock_deals(target_brands)


def _ensure_directory(path: str) -> None:
    """确保目录存在（支持直接传入目录路径）。"""
    # 这里假定传入的就是目录路径，而不是文件路径
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def _text_size(draw, text: str, font) -> Tuple[int, int]:
    """获取文本绘制尺寸，兼容 Pillow 10+（textsize 已弃用）。"""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _draw_centered_text(draw, text: str, xy: Tuple[int, int], font, fill: str = "#333333") -> None:
    """在指定坐标为中心点绘制文本。"""
    from PIL import ImageFont  # 延迟导入，避免未使用 Pillow 时影响加载

    if isinstance(font, str):
        font = ImageFont.truetype(font, 32)
    w, h = _text_size(draw, text, font)
    x, y = xy
    draw.text((x - w // 2, y - h // 2), text, font=font, fill=fill)


# 特殊活动主题配色与文案（如疯狂星期四）
THEME_CONFIG: Dict[str, Dict[str, Any]] = {
    "crazy_thursday": {
        "header_color": "#e4002b",
        "header_subtitle_color": "#ffd700",
        "title_text": "疯狂星期四 · 今日快餐菜单与活动早报",
        "card_accent": "#e4002b",
        "card_placeholder_fill": "#ffe6e6",
        "card_placeholder_outline": "#e4002b",
        "badge_fill": "#ffd700",
        "badge_text_color": "#5c3317",
        "background_image_name": "crazy_thursday.png",
    },
}
DEFAULT_THEME = {
    "header_color": "#ff6b3b",
    "header_subtitle_color": "#ffe7d9",
    "title_text": "今日快餐菜单与活动早报",
    "card_accent": "#ff6b3b",
    "card_placeholder_fill": "#ffe9dd",
    "card_placeholder_outline": "#ffb89b",
    "badge_fill": "#ffdd55",
    "badge_text_color": "#7a4b00",
    "background_image_name": None,
}


def _load_font(size: int = 40):
    """
    尝试加载系统中常见的中文字体；如果失败，则退回到 Pillow 默认字体。
    """
    from PIL import ImageFont

    candidate_paths = [
        # Windows 常见中文字体路径
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simfang.ttf",
        r"C:\Windows\Fonts\simkai.ttf",
        # 常见 Linux 中文字体路径（如容器 / 服务器环境）
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]

    for path in candidate_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:  # noqa: BLE001
                continue

    # 回退到默认字体（可能不支持中文加粗，但至少不会报错）
    return ImageFont.load_default()


def _sanitize_brand_for_filename(brand: str) -> str:
    """将品牌名转换为适合文件名的安全字符串。"""
    if not brand:
        return "brand"
    safe = "".join(ch for ch in brand if ch.isalnum())
    return safe or "brand"


def _generate_poster_sync(
    deals: List[Dict[str, Any]],
    theme: Optional[str] = None,
    brand_name: Optional[str] = None,
) -> str:
    """
    使用 Pillow 同步生成“海报级”优惠对比图片。
    theme: 特殊活动主题，如 "crazy_thursday"（疯狂星期四）使用专属配色与可选背景图。
    返回图片的本地保存路径。
    """
    from PIL import Image, ImageDraw

    # 画布大小（竖版，接近手机海报比例）
    width, height = 1080, 1920
    bg_color = "#f7f7f7"
    cfg = THEME_CONFIG.get(theme, DEFAULT_THEME) if theme else DEFAULT_THEME

    # 品牌名：用于标题 & 文件名
    if not deals:
        raise ValueError("deals is empty")
    if brand_name is None:
        brand_name = str(deals[0].get("brand", "")).strip() or "快餐品牌"

    image = Image.new("RGB", (width, height), bg_color)

    # 特殊活动：若有背景图则先绘制（覆盖画布）
    bg_name = cfg.get("background_image_name")
    if bg_name:
        bg_dir = os.path.join("data", "fastfood_deals", "backgrounds")
        bg_path = os.path.join(bg_dir, bg_name)
        if os.path.exists(bg_path):
            try:
                bg_img = Image.open(bg_path).convert("RGB")
                resample = getattr(Image, "Resampling", None)
                resample = resample.LANCZOS if resample else getattr(Image, "LANCZOS", 1)
                bg_img = bg_img.resize((width, height), resample)
                image.paste(bg_img, (0, 0))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[FastFoodDeals] Failed to load background image {bg_path}: {e}")

    draw = ImageDraw.Draw(image)

    # 字体
    title_font = _load_font(64)
    subtitle_font = _load_font(32)
    body_font = _load_font(28)
    price_font = _load_font(40)

    # 标题区域（特殊活动使用主题色）
    header_height = 260
    header_color = cfg.get("header_color", "#ff6b3b")
    header_subtitle_color = cfg.get("header_subtitle_color", "#ffe7d9")
    draw.rectangle([(0, 0), (width, header_height)], fill=header_color)

    base_title = cfg.get("title_text", "今日快餐比价早报")
    title_text = f"{brand_name} · {base_title}"
    today_str = datetime.now().strftime("%Y-%m-%d")
    date_text = f"日期：{today_str}"

    # 标题居中
    _draw_centered_text(draw, title_text, (width // 2, 90), title_font, fill="#ffffff")
    _draw_centered_text(draw, date_text, (width // 2, 170), subtitle_font, fill=header_subtitle_color)

    # 内容区域起始位置
    y = header_height + 40
    margin_x = 80
    card_height = 260
    card_gap = 30

    for idx, deal in enumerate(deals):
        if y + card_height + 40 > height:
            # 内容太多则不再绘制，避免溢出
            break

        card_top = y
        card_bottom = y + card_height
        # 卡片背景
        draw.rounded_rectangle(
            [(margin_x, card_top), (width - margin_x, card_bottom)],
            radius=32,
            fill="#ffffff",
        )

        # 左侧商品主图区域
        img_box_left = margin_x + 30
        img_box_top = card_top + 40
        img_box_right = img_box_left + 200
        img_box_bottom = card_bottom - 40
        ph_fill = cfg.get("card_placeholder_fill", "#ffe9dd")
        ph_outline = cfg.get("card_placeholder_outline", "#ffb89b")
        draw.rounded_rectangle(
            [(img_box_left, img_box_top), (img_box_right, img_box_bottom)],
            radius=24,
            fill=ph_fill,
            outline=ph_outline,
            width=3,
        )

        brand = str(deal.get("brand", "")).strip()
        brand_short = brand[:2] if brand else "快餐"
        card_accent = cfg.get("card_accent", "#ff6b3b")

        # 尝试加载商品主图；失败则回退为品牌简称文字
        img_url = str(deal.get("main_image_url", "")).strip()
        pasted_image = False
        # 跳过占位 URL（如 Mock 数据的 example.com），避免无意义请求与 SSL 警告
        is_placeholder = "example.com" in img_url or "example.org" in img_url
        if img_url.startswith("http") and not is_placeholder:
            try:
                resp = httpx.get(img_url, timeout=5.0)
                resp.raise_for_status()
                from PIL import Image  # 局部导入，避免顶层依赖

                prod_img = Image.open(BytesIO(resp.content)).convert("RGB")
                box_w = img_box_right - img_box_left - 16
                box_h = img_box_bottom - img_box_top - 16
                pw, ph = prod_img.size
                if pw > 0 and ph > 0 and box_w > 0 and box_h > 0:
                    scale = min(box_w / pw, box_h / ph)
                    new_w = int(pw * scale)
                    new_h = int(ph * scale)
                    resample = getattr(Image, "Resampling", None)
                    resample = resample.LANCZOS if resample else getattr(Image, "LANCZOS", 1)
                    prod_img = prod_img.resize((new_w, new_h), resample)
                    offset_x = img_box_left + (box_w - new_w) // 2 + 8
                    offset_y = img_box_top + (box_h - new_h) // 2 + 8
                    image.paste(prod_img, (offset_x, offset_y))
                    pasted_image = True
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[FastFoodDeals] Load product image failed for {img_url}: {e}")

        if not pasted_image:
            bx = (img_box_left + img_box_right) // 2
            by = (img_box_top + img_box_bottom) // 2
            _draw_centered_text(draw, brand_short, (bx, by), price_font, fill=card_accent)

        # 右侧文案区域
        text_x = img_box_right + 40
        text_y = card_top + 40

        title = str(deal.get("title", "今日主推新品"))
        category = str(deal.get("category", "今日推荐"))
        tag = str(deal.get("tag", "")).strip()
        activity = str(deal.get("activity", "")).strip()
        desc = str(deal.get("desc", "")).strip()

        # 第一行：分类 + 商品名
        draw.text(
            (text_x, text_y),
            f"{category} | {title}",
            font=subtitle_font,
            fill="#333333",
        )

        # 标签角标（如“新品上市”）
        if tag:
            tag_text = tag
            tag_w, tag_h = _text_size(draw, tag_text, body_font)
            tag_padding_x = 16
            tag_padding_y = 8
            tag_left = width - margin_x - tag_w - tag_padding_x * 2
            tag_top = text_y - 6
            tag_right = width - margin_x
            tag_bottom = tag_top + tag_h + tag_padding_y * 2
            draw.rounded_rectangle(
                [(tag_left, tag_top), (tag_right, tag_bottom)],
                radius=16,
                fill=card_accent,
            )
            draw.text(
                (tag_left + tag_padding_x, tag_top + tag_padding_y),
                tag_text,
                font=body_font,
                fill="#ffffff",
            )

        # 价格信息
        price = float(deal.get("price", 0.0))
        origin_price = deal.get("origin_price")

        price_y = text_y + 70
        draw.text(
            (text_x, price_y),
            f"今日价：¥{price:.1f}",
            font=price_font,
            fill="#ff3b30",
        )

        # 原价（可选）
        if origin_price is not None:
            op_text = f"原价 ¥{float(origin_price):.1f}"
            op_w, op_h = _text_size(draw, op_text, body_font)
            op_x = text_x
            op_y = price_y + 50
            draw.text(
                (op_x, op_y),
                op_text,
                font=body_font,
                fill="#999999",
            )
            # 原价中间画删除线
            draw.line(
                [(op_x, op_y + op_h // 2), (op_x + op_w, op_y + op_h // 2)],
                fill="#bbbbbb",
                width=2,
            )
            next_y = op_y + 40
        else:
            next_y = price_y + 50

        # 活动说明
        if activity:
            draw.text(
                (text_x, next_y),
                activity,
                font=body_font,
                fill=card_accent,
            )
            next_y += 40

        # 商品卖点说明
        if desc:
            draw.text(
                (text_x, next_y),
                desc,
                font=body_font,
                fill="#555555",
            )

        y = card_bottom + card_gap

    # 底部提示
    footer_text = "提示：以上价格与活动以各品牌官方实际为准，仅供参考。"
    footer_y = height - 80
    fw, fh = _text_size(draw, footer_text, body_font)
    draw.text(
        ((width - fw) // 2, footer_y),
        footer_text,
        font=body_font,
        fill="#999999",
    )

    # 保存到插件 data 目录下
    today_str = datetime.now().strftime("%Y%m%d")
    out_dir = os.path.join("data", "fastfood_deals")
    _ensure_directory(out_dir)
    brand_safe = _sanitize_brand_for_filename(brand_name)
    out_path = os.path.join(out_dir, f"fastfood_deals_{today_str}_{brand_safe}.png")
    image.save(out_path, format="PNG")

    logger.info(f"[FastFoodDeals] Poster generated at: {out_path}")
    return out_path


async def generate_poster(
    deals: List[Dict[str, Any]],
    theme: Optional[str] = None,
    brand_name: Optional[str] = None,
) -> str:
    """
    异步封装的海报生成函数。
    内部使用 asyncio.to_thread 调用同步的 Pillow 绘制逻辑，避免阻塞事件循环。
    theme: 特殊活动主题，如 "crazy_thursday"。
    brand_name: 品牌名称，用于标题与文件名。
    """
    return await asyncio.to_thread(_generate_poster_sync, deals, theme, brand_name)


@register(
    "fastfood_deals",
    "枫雪",
    "每日快餐优惠比价早报插件（FastFoodDeals）",
    "1.0.0",
    "https://github.com/runmango/astrbot_plugin_FastFoodDeals",
)
class FastFoodDeals(Star):
    """
    FastFoodDeals AstrBot 插件

    功能：
    - 完全绕过大模型与 Agent，对接固定数据源；
    - 每日定时生成一张快餐优惠对比海报；
    - 自动推送到配置中的 QQ 群。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config  # AstrBotConfig 继承自 dict，支持字典操作

        # 从配置中读取字段，提供合理默认值，方便可视化面板配置
        self.target_groups: List[str] = list(self.config.get("target_groups", []))
        self.target_brands: List[str] = list(self.config.get("target_brands", []))
        self.schedule_time: str = str(self.config.get("schedule_time", "08:00"))
        self.data_source: str = str(self.config.get("data_source", "mock")).strip().lower()
        self.rss_urls: List[str] = list(self.config.get("rss_urls", []))
        self.api_url: str = str(self.config.get("api_url", "")).strip()
        self.api_method: str = str(self.config.get("api_method", "get")).strip().lower()

        # 当前插件实例对应的定时任务 ID，便于卸载/重载时清理
        self._job_id: str = f"fastfood_deals_daily_{id(self)}"

        logger.info(
            "[FastFoodDeals] Plugin initialized with config: "
            f"target_groups={self.target_groups}, target_brands={self.target_brands}, "
            f"schedule_time={self.schedule_time}, data_source={self.data_source}",
        )

        # 注册定时任务
        self._register_daily_job()

    @filter.command("快餐早报")
    async def cmd_fastfood_report(self, event: AstrMessageEvent):
        """主动触发：获取今日快餐优惠比价早报并推送到当前会话（执行方式 A）。"""
        try:
            deals = await fetch_today_deals(
                self.target_brands,
                data_source=self.data_source,
                rss_urls=self.rss_urls,
                api_url=self.api_url,
                api_method=self.api_method,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] cmd fetch deals error: {e}")
            yield event.plain_result("今日快餐优惠数据获取失败，请稍后重试。")
            return
        if not deals:
            yield event.plain_result("今日暂无监控到的快餐优惠活动。")
            return
        theme = get_theme_for_today()

        # 按品牌拆分为多张海报：肯德基、麦当劳、德克士等各一张
        brand_map: Dict[str, List[Dict[str, Any]]] = {}
        for deal in deals:
            brand = str(deal.get("brand", "其他品牌")).strip() or "其他品牌"
            brand_map.setdefault(brand, []).append(deal)

        # 保证按配置顺序输出；未在配置中的品牌放在最后
        ordered_brands: List[str] = []
        for b in self.target_brands:
            if b in brand_map:
                ordered_brands.append(b)
        for b in brand_map:
            if b not in ordered_brands:
                ordered_brands.append(b)

        # 先发一条总的引导语
        intro_all = "为您奉上今日快餐菜单与活动早报，请查阅。"
        yield event.plain_result(intro_all)

        for brand in ordered_brands:
            brand_deals = brand_map.get(brand)
            if not brand_deals:
                continue
            try:
                poster_path = await generate_poster(brand_deals, theme=theme, brand_name=brand)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[FastFoodDeals] cmd poster error for {brand}: {e}")
                yield event.plain_result(f"{brand} 今日快餐优惠海报生成失败，请稍后重试。")
                continue
            yield event.image_result(poster_path)

    def _register_daily_job(self) -> None:
        """向全局 Scheduler 注册每日定时任务。"""
        scheduler = _get_scheduler()
        hour, minute = _parse_schedule_time(self.schedule_time)

        trigger = CronTrigger(hour=hour, minute=minute)

        try:
            scheduler.add_job(
                self._scheduled_task_entry,
                trigger=trigger,
                id=self._job_id,
                replace_existing=True,
                coalesce=True,
                misfire_grace_time=300,
            )
            logger.info(
                f"[FastFoodDeals] Daily job registered at {hour:02d}:{minute:02d}, job_id={self._job_id}",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] Failed to register daily job: {e}")

    async def _scheduled_task_entry(self) -> None:
        """
        Scheduler 回调入口。
        该方法负责调用核心逻辑，并做好异常兜底。
        """
        try:
            await self._run_daily_report()
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] Unexpected error in scheduled task: {e}")

    async def _run_daily_report(self) -> None:
        """
        真正执行“每日快餐优惠比价早报”的核心逻辑：
        1. 获取数据；
        2. 生成海报；
        3. 向配置的群聊推送消息。
        """
        if not self.target_groups:
            logger.warning("[FastFoodDeals] No target_groups configured, skip sending.")
            return

        try:
            deals = await fetch_today_deals(
                self.target_brands,
                data_source=self.data_source,
                rss_urls=self.rss_urls,
                api_url=self.api_url,
                api_method=self.api_method,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] Failed to fetch deals: {e}")
            await self._send_text_to_all(
                "今日快餐优惠数据获取失败，请稍后重试。",
            )
            return

        if not deals:
            logger.warning("[FastFoodDeals] No deals found for today.")
            await self._send_text_to_all("今日暂无监控到的快餐优惠活动。")
            return

        theme = get_theme_for_today()

        # 按品牌拆分为多张海报
        brand_map: Dict[str, List[Dict[str, Any]]] = {}
        for deal in deals:
            brand = str(deal.get("brand", "其他品牌")).strip() or "其他品牌"
            brand_map.setdefault(brand, []).append(deal)

        ordered_brands: List[str] = []
        for b in self.target_brands:
            if b in brand_map:
                ordered_brands.append(b)
        for b in brand_map:
            if b not in ordered_brands:
                ordered_brands.append(b)

        any_success = False
        posters_to_send: List[str] = []
        for brand in ordered_brands:
            brand_deals = brand_map.get(brand)
            if not brand_deals:
                continue
            try:
                poster_path = await generate_poster(brand_deals, theme=theme, brand_name=brand)
            except Exception as e:  # noqa: BLE001
                logger.error(f"[FastFoodDeals] Failed to generate poster for {brand}: {e}")
                await self._send_text_to_all(
                    f"{brand} 今日快餐优惠海报生成失败，但数据已获取成功，请稍后在控制台查看日志。",
                )
                continue

            posters_to_send.append(poster_path)
            any_success = True

        if not any_success:
            # 所有品牌都生成失败时兜底提示一次
            await self._send_text_to_all(
                "今日快餐优惠海报全部生成失败，但数据已获取成功，请稍后在控制台查看日志。",
            )
            return

        # 先统一发一句引导语，再依次发图片
        await self._send_text_to_all("为您奉上今日快餐菜单与活动早报，请查阅。")
        for poster_path in posters_to_send:
            await self._send_image_to_all(poster_path)

    async def _send_text_to_all(self, text: str) -> None:
        """向所有配置的群聊发送纯文本消息。"""
        if not self.target_groups:
            return

        chain = MessageChain().message(text)

        for group in self.target_groups:
            try:
                origin = _build_group_origin(group)
                await self.context.send_message(origin, chain)
                logger.info(f"[FastFoodDeals] Text sent to {origin}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[FastFoodDeals] Failed to send text to group {group}: {e}")

    async def _send_image_to_all(self, image_path: str, intro_text: Optional[str] = None) -> None:
        """向所有配置的群聊发送图片，可选附带一条文字说明。"""
        if not self.target_groups:
            return

        # 使用 AstrBot 的 MessageChain + 文件图片接口
        for group in self.target_groups:
            try:
                origin = _build_group_origin(group)
                if intro_text:
                    chain = MessageChain().message(intro_text).file_image(image_path)
                else:
                    chain = MessageChain().file_image(image_path)
                await self.context.send_message(origin, chain)
                logger.info(f"[FastFoodDeals] Image poster sent to {origin}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[FastFoodDeals] Failed to send image to group {group}: {e}")
                # 图片发送失败时，降级为纯文本
                try:
                    fallback_chain = MessageChain().message(
                        f"{intro_text}\n（图片发送失败，请联系管理员检查机器人文件读写权限。）",
                    )
                    await self.context.send_message(origin, fallback_chain)
                except Exception as inner_e:  # noqa: BLE001
                    logger.error(
                        f"[FastFoodDeals] Failed to send fallback text to group {group}: {inner_e}",
                    )

    async def terminate(self) -> None:
        """
        插件被卸载/停用时调用。
        清理已注册的定时任务，避免残留任务继续运行。
        """
        scheduler = _get_scheduler()
        try:
            scheduler.remove_job(self._job_id)
            logger.info(f"[FastFoodDeals] Job {self._job_id} removed on terminate.")
        except JobLookupError:
            # 任务可能已被重载/删除，无需报错
            pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"[FastFoodDeals] Failed to remove job {self._job_id}: {e}")


# ------------------------------
# requirements.txt（插件依赖示例，仅供参考）
# 实际部署时请在本插件目录下创建独立的 requirements.txt 文件。
#
# apscheduler>=3.10.4
# pillow>=10.0.0
# httpx>=0.27.0
# ------------------------------

