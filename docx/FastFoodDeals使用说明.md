# FastFoodDeals 插件使用说明

> 说明文件位于 `FastFoodDeals/docx/` 目录中，便于后续转换为 `.docx` 文档。

## 一、插件简介

`FastFoodDeals` 是 AstrBot 的一个定时推送类插件，每天在指定时间生成一张“今日快餐优惠比价早报”海报，并主动推送到配置的 QQ 群。

- 不依赖任何大模型（LLM）与 Agent 对话能力；
- 使用本地逻辑 + Mock 数据（可后续接入真实 API）；
- 使用 Pillow 生成“海报级”图片，展示多品牌优惠对比与购买建议。
- **执行方式 A**：发送命令 **/快餐早报** 可主动触发当日海报并推送到当前会话。
- **特殊活动**：如疯狂星期四（每周四）自动使用红金配色与专属标题，并可选用自定义背景图。

## 二、目录结构

- `main.py`：插件主入口，包含 Star 类实现、定时任务、消息发送逻辑；
- `_conf_schema.json`：插件配置 Schema，供 AstrBot Web 面板可视化配置；
- `metadata.yaml`：插件元信息（名称、作者、版本、支持平台等）；
- `requirements.txt`：插件依赖；
- `docx/FastFoodDeals使用说明.md`：本说明文档（可复制内容生成 `.docx`）。
- `data/fastfood_deals/backgrounds/`：特殊活动背景图目录，如放置 `crazy_thursday.png` 可在周四使用自定义背景。

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

- `data_source`：数据源类型（`mock` / `rss` / `api`）  
- `rss_urls`：RSS 订阅地址列表（当 data_source 为 `rss` 时必填）  
- `api_url`、`api_method`：自定义 API 地址与请求方法（当 data_source 为 `api` 时使用）

## 五、功能说明

1. **数据获取（Mock）**  
   - 当前使用内置 Mock 函数 `fetch_today_deals` 生成示例优惠数据；
   - 返回内容包含：品牌、套餐名称、原价、到手价、优惠力度、主图 URL（占位）、推荐语等；
   - 后续可将该函数替换为真实数据源（如爬虫、内部 API），保持返回字段不变即可。

2. **海报生成**  
   - 使用 Pillow 生成竖版海报（1080x1920），包含：
     - 标题：“今日快餐比价早报”（周四为“疯狂星期四 · 今日快餐比价早报”）；
     - 当前日期；
     - 各品牌卡片（品牌 + 商品主图占位 + 原价/到手价/优惠力度/推荐文案）；
     - 自动标记“今日最划算”套餐；
     - 底部免责声明。
   - 图片默认保存在 `data/fastfood_deals/fastfood_deals_YYYYMMDD.png`。
   - **特殊活动**：周四自动启用“疯狂星期四”主题（红金配色）；若在 `data/fastfood_deals/backgrounds/` 下放置 `crazy_thursday.png`，将作为海报背景图使用。

3. **执行方式 A：主动命令触发**  
   - 在群或私聊中发送 **/快餐早报**，立即生成当日海报并推送到当前会话，无需等待定时时间。

4. **定时推送**  
   - 使用 `apscheduler` 在插件内部维护一个全局 AsyncIOScheduler；
   - 根据 `schedule_time` 每天固定时间触发；
   - 每次触发会：
     1. 拉取当天优惠数据；
     2. 根据日期应用特殊活动主题（如周四疯狂星期四）；
     3. 生成海报图片；
     4. 向所有配置的群号发送“引导语 + 海报图片”。

5. **消息发送**  
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

## 七、如何准确获取数据源（推荐做法）

插件已支持三种数据源，在 Web 配置中选择即可，无需改代码：

1. **mock**（默认）：内置示例数据，用于试跑与演示。
2. **rss**：从 RSS 订阅拉取真实优惠。
   - 在配置中设置 **data_source** 为 `rss`，**rss_urls** 填写订阅地址，例如：
     - 什么值得买优惠精选：`https://feed.smzdm.com/`
   - 插件会按 **target_brands**（肯德基、麦当劳等）在标题/描述中做关键词过滤，并尝试从文案中解析价格（¥xx、xx元）。
3. **api**：自建或第三方 JSON 接口。
   - 设置 **data_source** 为 `api`，**api_url** 填接口地址，**api_method** 选 get 或 post。
   - 接口需返回数组，每项字段可为：`brand`、`title`、`price`、`origin_price`、`category`、`tag`、`activity`、`desc`、`main_image_url`（或中文名：品牌、商品、到手价、原价等，插件会做兼容映射）。

若你自行维护爬虫或聚合服务，只需对外提供上述结构的 JSON 数组，并在插件中选用 **api** 数据源即可实现准确获取。

自建 API 返回的每项可包含（中英文字段名均可兼容）：`brand`/品牌、`title`/商品/`name`、`price`/到手价/`final_price`、`origin_price`/原价/`original_price`、`category`/分类、`tag`/标签、`activity`/活动、`desc`/推荐/`recommendation`、`main_image_url`/`image`。

---

### 推荐 RSS 源（肯德基 / 麦当劳 / 德克士相关）

**说明**：目前没有仅针对「肯德基 / 麦当劳 / 德克士每日菜单」的专用 RSS。肯德基、麦当劳、德克士官方也不提供菜单或优惠的 RSS。因此只能依赖**综合优惠类 RSS**，由插件根据 **target_brands** 在标题、描述里做关键词过滤，筛出包含这些品牌名的条目。

以下为可用的综合优惠 RSS，建议在 **data_source** 选 `rss` 时使用（可多选）：

| 推荐 RSS 地址 | 说明 |
|---------------|------|
| `https://feed.smzdm.com/` | **什么值得买 - 优惠精选**：每日网友爆料 + 编辑精选，常含肯德基/麦当劳/德克士/汉堡王等快餐优惠、券活动。 |
| `https://faxian.smzdm.com/feed` | **什么值得买 - 发现频道**：爆料更新更频繁，品类更广，同样会出现快餐、外卖、本地生活类好价。 |

**配置示例**（Web 配置中）：

- **data_source**：`rss`
- **rss_urls**：`["https://feed.smzdm.com/", "https://faxian.smzdm.com/feed"]`
- **target_brands**：`["肯德基", "麦当劳", "德克士"]`

插件会只保留标题或描述中出现上述任一品牌的条目，并尽量从文案中解析价格（如 ¥19.9、32 元）。若某天没有匹配条目，早报可能为空或条数较少，属正常情况。

**可选**：若你自建或使用 [RSSHub](https://docs.rsshub.app) 等聚合服务，且该服务支持「按关键词筛选什么值得买」的路由，可以把对应 feed 加入 **rss_urls**，能进一步减少无关条目、提高与快餐相关的比例。

## 八、注意事项

- 插件不使用任何大模型（LLM）或 Agent，对话能力完全关闭；
- 建议将说明文档内容复制到 Word 中另存为 `.docx`，以便在团队内分发；
- 更新插件代码后，可在 AstrBot Web 管理面板中使用“重载插件”功能热更新。

