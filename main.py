import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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


async def fetch_today_deals(target_brands: List[str]) -> List[Dict[str, Any]]:
    """
    模拟获取今日快餐优惠数据的异步函数。

    后续你可以将这里替换为真实的数据源（例如：爬取「什么值得买」、
    请求你的内部接口等），只需要保证返回结构保持一致即可。
    """
    if not target_brands:
        target_brands = ["肯德基", "麦当劳", "德克士"]

    today_str = datetime.now().strftime("%Y-%m-%d")
    deals: List[Dict[str, Any]] = []

    # 这里简单构造几条 Mock 数据，方便后续接入真实接口时对齐字段
    base_presets = [
        {
            "title": "早餐超值双人套餐",
            "original_price": 32.0,
            "final_price": 19.9,
            "recommendation": "适合两人早餐搭配，性价比高。",
        },
        {
            "title": "午餐精选堡+饮料",
            "original_price": 36.0,
            "final_price": 22.9,
            "recommendation": "工作日午餐刚刚好，饱腹又不贵。",
        },
        {
            "title": "家庭分享桶",
            "original_price": 89.0,
            "final_price": 59.9,
            "recommendation": "三四人聚餐首选，适合聚会分享。",
        },
    ]

    for idx, brand in enumerate(target_brands):
        preset = base_presets[idx % len(base_presets)]
        discount_percent = round(
            (1 - preset["final_price"] / preset["original_price"]) * 100,
            1,
        )
        deals.append(
            {
                "date": today_str,
                "brand": brand,
                "title": preset["title"],
                "original_price": preset["original_price"],
                "final_price": preset["final_price"],
                "discount_percent": discount_percent,
                # 商品主图 URL：仅作为展示字段，当前示例不强依赖真实图片
                "main_image_url": f"https://example.com/{brand}/deal_{idx}.jpg",
                "recommendation": preset["recommendation"],
            },
        )

    return deals


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
        "title_text": "疯狂星期四 · 今日快餐比价早报",
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
    "title_text": "今日快餐比价早报",
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

    # 找出最划算的优惠（按 final_price 从低到高）
    best_deal_brand: Optional[str] = None
    if deals:
        sorted_deals = sorted(deals, key=lambda d: d.get("final_price", 9999))
        best_deal_brand = sorted_deals[0].get("brand")

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

        # 左侧商品主图占位框
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

        # 在占位框中绘制品牌简称
        brand = str(deal.get("brand", "")).strip()
        brand_short = brand[:2] if brand else "快餐"
        card_accent = cfg.get("card_accent", "#ff6b3b")

        bx = (img_box_left + img_box_right) // 2
        by = (img_box_top + img_box_bottom) // 2
        _draw_centered_text(draw, brand_short, (bx, by), price_font, fill=card_accent)

        # 右侧文案区域
        text_x = img_box_right + 40
        text_y = card_top + 40

        title = str(deal.get("title", "今日主推套餐"))
        draw.text((text_x, text_y), f"{brand} | {title}", font=subtitle_font, fill="#333333")

        # 价格信息
        original_price = float(deal.get("original_price", 0.0))
        final_price = float(deal.get("final_price", 0.0))
        discount_percent = float(deal.get("discount_percent", 0.0))

        price_y = text_y + 70
        draw.text(
            (text_x, price_y),
            f"原价：¥{original_price:.1f}",
            font=body_font,
            fill="#999999",
        )

        # 原价中间画删除线
        op_text = f"原价：¥{original_price:.1f}"
        op_w, op_h = _text_size(draw, op_text, body_font)
        draw.line(
            [(text_x, price_y + op_h // 2), (text_x + op_w, price_y + op_h // 2)],
            fill="#bbbbbb",
            width=2,
        )

        final_y = price_y + 50
        draw.text(
            (text_x, final_y),
            f"到手价：¥{final_price:.1f}",
            font=price_font,
            fill="#ff3b30",
        )

        discount_y = final_y + 60
        draw.text(
            (text_x, discount_y),
            f"优惠力度：约 {discount_percent:.1f}%",
            font=body_font,
            fill=card_accent,
        )

        # 推荐语
        recommend = str(deal.get("recommendation", "适合作为今日的实惠之选。"))
        rec_y = discount_y + 40
        draw.text(
            (text_x, rec_y),
            f"建议：{recommend}",
            font=body_font,
            fill="#555555",
        )

        # 最划算高亮标记
        if best_deal_brand and brand == best_deal_brand:
            badge_text = "今日最划算"
            badge_w, badge_h = _text_size(draw, badge_text, body_font)
            badge_padding_x = 18
            badge_padding_y = 10
            badge_left = width - margin_x - badge_w - badge_padding_x * 2
            badge_top = card_top + 26
            badge_right = width - margin_x - 26
            badge_bottom = badge_top + badge_h + badge_padding_y * 2
            b_fill = cfg.get("badge_fill", "#ffdd55")
            b_text_color = cfg.get("badge_text_color", "#7a4b00")
            draw.rounded_rectangle(
                [(badge_left, badge_top), (badge_right, badge_bottom)],
                radius=18,
                fill=b_fill,
            )
            draw.text(
                (badge_left + badge_padding_x, badge_top + badge_padding_y),
                badge_text,
                font=body_font,
                fill=b_text_color,
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

        # 当前插件实例对应的定时任务 ID，便于卸载/重载时清理
        self._job_id: str = f"fastfood_deals_daily_{id(self)}"

        logger.info(
            "[FastFoodDeals] Plugin initialized with config: "
            f"target_groups={self.target_groups}, "
            f"target_brands={self.target_brands}, "
            f"schedule_time={self.schedule_time}",
        )

        # 注册定时任务
        self._register_daily_job()

    @filter.command("快餐早报")
    async def cmd_fastfood_report(self, event: AstrMessageEvent):
        """主动触发：获取今日快餐优惠比价早报并推送到当前会话（执行方式 A）。"""
        try:
            deals = await fetch_today_deals(self.target_brands)
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
            intro = f"为您奉上 {brand} 今日快餐优惠货比三家早报，请查阅。"
            yield event.plain_result(intro)
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
            deals = await fetch_today_deals(self.target_brands)
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

            intro_text = f"为您奉上 {brand} 今日快餐优惠货比三家早报，请查阅。"
            await self._send_image_to_all(poster_path, intro_text)
            any_success = True

        if not any_success:
            # 所有品牌都生成失败时兜底提示一次
            await self._send_text_to_all(
                "今日快餐优惠海报全部生成失败，但数据已获取成功，请稍后在控制台查看日志。",
            )

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

    async def _send_image_to_all(self, image_path: str, intro_text: str) -> None:
        """向所有配置的群聊发送“文字 + 图片”消息。"""
        if not self.target_groups:
            return

        # 使用 AstrBot 的 MessageChain + 文件图片接口
        for group in self.target_groups:
            try:
                origin = _build_group_origin(group)
                chain = MessageChain().message(intro_text).file_image(image_path)
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

