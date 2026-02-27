# FastFoodDeals 插件使用说明

> 说明文件位于 `FastFoodDeals/docx/` 目录中，便于后续转换为 `.docx` 文档。

## 一、插件简介

`FastFoodDeals` 是 AstrBot 的一个定时推送类插件，每天在指定时间生成一张“今日快餐优惠比价早报”海报，并主动推送到配置的 QQ 群。

- 不依赖任何大模型（LLM）与 Agent 对话能力；
- 使用本地逻辑 + Mock 数据（可后续接入真实 API）；
- 使用 Pillow 生成“海报级”图片，展示多品牌优惠对比与购买建议。

## 二、目录结构

- `main.py`：插件主入口，包含 Star 类实现、定时任务、消息发送逻辑；
- `_conf_schema.json`：插件配置 Schema，供 AstrBot Web 面板可视化配置；
- `metadata.yaml`：插件元信息（名称、作者、版本、支持平台等）；
- `requirements.txt`：插件依赖；
- `docx/FastFoodDeals使用说明.md`：本说明文档（可复制内容生成 `.docx`）。

## 三、安装步骤

1. 将 `FastFoodDeals` 整个文件夹放入 AstrBot 的 `data/plugins/` 目录下；
2. 在 AstrBot 所在 Python 环境中，进入 `FastFoodDeals` 目录，安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

3. 启动 AstrBot（或在 Web 管理面板中重载插件）。

## 四、插件配置

在 AstrBot Web 管理面板 → 插件管理 → `FastFoodDeals` → 配置 中，可看到以下配置项：

- `target_groups`：需要推送的 QQ 群号列表  
  - 类型：列表（string）  
  - 示例：`["123456789", "987654321"]`

- `target_brands`：需要监控的快餐品牌  
  - 类型：列表（string）  
  - 示例：`["肯德基", "麦当劳", "德克士"]`

- `schedule_time`：每天定时发送时间  
  - 类型：字符串（24 小时制）  
  - 示例：`"08:00"`  
  - 若格式非法，会自动回退到 `08:00` 并在日志中提示。

## 五、功能说明

1. **数据获取（Mock）**  
   - 当前使用内置 Mock 函数 `fetch_today_deals` 生成示例优惠数据；
   - 返回内容包含：品牌、套餐名称、原价、到手价、优惠力度、主图 URL（占位）、推荐语等；
   - 后续可将该函数替换为真实数据源（如爬虫、内部 API），保持返回字段不变即可。

2. **海报生成**  
   - 使用 Pillow 生成竖版海报（1080x1920），包含：
     - 标题：“今日快餐比价早报”；
     - 当前日期；
     - 各品牌卡片（品牌 + 商品主图占位 + 原价/到手价/优惠力度/推荐文案）；
     - 自动标记“今日最划算”套餐；
     - 底部免责声明。
   - 图片默认保存在 `data/fastfood_deals/fastfood_deals_YYYYMMDD.png`。

3. **定时推送**  
   - 使用 `apscheduler` 在插件内部维护一个全局 AsyncIOScheduler；
   - 根据 `schedule_time` 每天固定时间触发；
   - 每次触发会：
     1. 拉取当天优惠数据；
     2. 生成海报图片；
     3. 向所有配置的群号发送“引导语 + 海报图片”。

4. **消息发送**  
   - 使用 AstrBot 的 `MessageChain` 主动群发消息；
   - QQ 群的 `unified_msg_origin` 默认按 `aiocqhttp:group:<群号>` 构造；
   - 如使用其他适配器，只需修改 `_build_group_origin` 的实现。

## 六、异常处理与兜底策略

- 数据获取失败：  
  - 不会导致插件崩溃，会向目标群发送一条纯文本提示：  
    “今日快餐优惠数据获取失败，请稍后重试。”

- 今日无数据：  
  - 发送纯文本：  
    “今日暂无监控到的快餐优惠活动。”

- 海报生成失败：  
  - 数据成功但绘图异常时，发送纯文本提示，并在日志中写明原因。

- 图片发送失败：  
  - 降级为纯文本发送，并提示检查机器人文件读写权限等问题。

## 七、如何接入真实数据源（示例思路）

1. 在 `main.py` 中找到 `fetch_today_deals` 函数；
2. 在函数内部使用 `httpx`、`aiohttp` 等异步 HTTP 客户端调用你的真实 API；
3. 将 API 返回结果映射为当前使用的字段结构：

   - `brand`：品牌名称；
   - `title`：套餐名称或优惠标题；
   - `original_price` / `final_price`：原价与到手价；
   - `discount_percent`：折扣百分比，可自行计算；
   - `main_image_url`：主图地址（可保留占位或用于后续加载真实图片）；
   - `recommendation`：购买建议文案。

4. 保持函数签名与返回格式不变，即可无缝替换 Mock 数据。

## 八、注意事项

- 插件不使用任何大模型（LLM）或 Agent，对话能力完全关闭；
- 建议将说明文档内容复制到 Word 中另存为 `.docx`，以便在团队内分发；
- 更新插件代码后，可在 AstrBot Web 管理面板中使用“重载插件”功能热更新。

