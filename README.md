# FastFoodDeals · 每日快餐优惠比价早报插件 🍟

**版本**：v1.0.1

> 为 AstrBot 提供“每日快餐优惠比价早报”主动推送能力，完全不依赖大模型（LLM）。

---

## ✨ 特性一览

- **零 LLM 依赖**：完全绕过大模型与 Agent，对话能力关闭，播报语气稳定、客观、专业。
- **定时海报推送**：每天固定时间生成“海报级”快餐优惠对比图，并主动推送到指定 QQ 群。
- **多品牌比价**：支持自定义监控品牌（如：肯德基、麦当劳、德克士等），自动计算优惠力度与最划算推荐。
- **可视化配置**：通过 AstrBot Web 管理面板配置推送群、品牌列表与推送时间，无需改代码。
- **多数据源可配置**：支持 `mock`（内置示例）、`rss`（如什么值得买优惠精选）、`api`（自定义 HTTP JSON 接口），在 Web 配置中选择并填写对应 URL 即可准确拉取数据。
- **执行方式 A（主动触发）**：在群内或私聊发送命令 **`/快餐早报`**，立即生成当日海报并推送到当前会话，无需等到定时时间。
- **特殊活动主题**：如 **疯狂星期四**（每周四）自动启用 KFC 风格红金配色与专属标题；可选放置背景图 `data/fastfood_deals/backgrounds/crazy_thursday.png` 以使用自定义背景。

---

## 🤖 AstrBot 行为说明

- 插件以 AstrBot `Star` 插件形式运行，通过 `@register` 装饰器注册，ID 为 `fastfood_deals`，作者为 `枫雪`。
- 插件加载时会读取 `_conf_schema.json` 生成的配置（包含 `target_groups`、`target_brands`、`schedule_time`），并在内部分配给实例字段。
- 启动后会在内部创建一个全局 `AsyncIOScheduler`，根据配置的 `schedule_time` 注册每日定时任务，定时拉取当日优惠并向指定 QQ 群主动推送海报。
- 在对话中收到 `/快餐早报` 指令时，会通过 AstrBot 的 `filter.command` 机制触发命令 Handler，立即执行一次完整的“拉取数据 → 生成海报 → 回复文本 + 图片”流程。
- 所有消息发送均通过 AstrBot 的 `MessageChain` 与 `context.send_message` 完成，仅使用固定文案和本地图像，不调用任何大模型或 Agent 能力。

---

## 🧱 插件结构

```text
FastFoodDeals/
├─ main.py               # 插件主入口，Star 类、定时任务、命令触发、海报生成
├─ _conf_schema.json     # AstrBot Web 配置 Schema
├─ metadata.yaml         # 插件元信息（id/name/version/适配平台）
├─ requirements.txt      # 插件依赖
├─ data/fastfood_deals/backgrounds/  # 特殊活动背景图（如 crazy_thursday.png）
└─ docx/
   └─ FastFoodDeals使用说明.md  # 详细中文说明，可转为 .docx
```

---

## 🚀 安装

1. **拷贝插件目录**

   将整个 `FastFoodDeals` 文件夹放入 AstrBot 项目的 `data/plugins/` 目录下，例如：

   ```bash
   cd /path/to/AstrBot/data/plugins
   # 将 FastFoodDeals 复制到此处
   ```

2. **安装依赖**

   在 AstrBot 使用的 Python 环境中，进入插件目录执行：

   ```bash
   cd /path/to/AstrBot/data/plugins/FastFoodDeals
   pip install -r requirements.txt
   ```

3. **启动 / 重载 AstrBot**

   - 启动 AstrBot（或在 Web 管理面板中重载插件）；
   - 在 WebUI → 插件管理 中启用 `FastFoodDeals` 插件。

---

## ⚙️ 配置说明

插件通过 `_conf_schema.json` 向 AstrBot 注册配置项，最终在 WebUI 中可视化展示。  
当前支持的配置字段如下：

```json
{
  "target_groups": {
    "description": "需要推送的 QQ 群聊号列表，例如 [\"123456789\"]",
    "type": "list",
    "items": {
      "type": "string"
    },
    "default": []
  },
  "target_brands": {
    "description": "需要监控的快餐品牌，例如 [\"肯德基\", \"麦当劳\", \"德克士\"]",
    "type": "list",
    "items": {
      "type": "string"
    },
    "default": [
      "肯德基",
      "麦当劳",
      "德克士"
    ]
  },
  "schedule_time": {
    "description": "每天定时发送的时间（24 小时制），例如 \"08:00\"",
    "type": "string",
    "default": "08:00"
  }
}
```

### WebUI 中的典型配置示例

- **target_groups**
  - 示例：`["123456789", "987654321"]`
  - 说明：机器人将向这些 QQ 群每天推送一次海报。

- **target_brands**
  - 示例：`["肯德基", "麦当劳", "德克士"]`
  - 说明：参与比价的快餐品牌列表，可自由增删。

- **schedule_time**
  - 示例：`"08:00"`
  - 说明：每天 08:00 触发任务；若格式不正确会自动回退为 `08:00` 并在日志提示。

---

## 🧩 功能设计

### 1. 完全绕过 LLM / Agent

- 插件内部 **不调用任何大模型接口**；
- 所有文案与播报内容均来自固定模板与优惠数据；
- 使用 AstrBot 的 `MessageChain` 主动消息接口直接推送。

### 2. 数据获取（可配置数据源）

- 核心函数：`async def fetch_today_deals(target_brands, data_source=..., rss_urls=..., api_url=..., api_method=...)`
- 在 Web 配置中设置 **data_source**：
  - **mock**：内置示例数据，无需外网。
  - **rss**：从 **rss_urls** 拉取（如 `["https://feed.smzdm.com/"]` 什么值得买优惠精选），按 **target_brands** 关键词过滤（标题/描述含「肯德基」「麦当劳」等才会展示）。
  - **api**：从 **api_url** 拉取 JSON 数组，每项需含 `brand`、`title`、`price` 等字段（详见下方结构），支持 `api_method` 为 get/post。
- 返回结构示例（每条）：

```python
{
    "date": "2025-01-01",
    "brand": "肯德基",
    "title": "早餐超值双人套餐",
    "original_price": 32.0,
    "final_price": 19.9,
    "discount_percent": 37.8,
    "main_image_url": "https://example.com/肯德基/deal_0.jpg",
    "recommendation": "适合两人早餐搭配，性价比高。"
}
```

> 你可以在此函数内对接自己的爬虫 / API，只要保持字段名与含义一致即可。

### 3. 海报生成（Poster Generator）

- 使用 **Pillow** 在本地生成竖版“海报级”图片（默认 `1080 × 1920`）；
- 包含内容：
  - 大标题：`今日快餐比价早报`；
  - 当前日期；
  - 每个品牌一张卡片：品牌名、套餐名、原价 / 到手价 / 优惠力度、购买建议；
  - 自动标记“今日最划算”优惠；
  - 页脚免责声明。
- 输出路径（示例）：  
  `data/fastfood_deals/fastfood_deals_YYYYMMDD.png`
- **特殊活动**：当 `get_theme_for_today()` 返回主题（如周四的 `crazy_thursday`）时，自动使用该主题的配色与标题；若在 `data/fastfood_deals/backgrounds/` 下放置同名背景图（如 `crazy_thursday.png`），会先铺满背景再绘制内容。

### 4. 执行方式 A：主动命令触发

在任意已接入的群或私聊中发送：

- **`/快餐早报`**

机器人会立即拉取当日优惠数据、根据是否周四等应用对应主题（如疯狂星期四）、生成海报，并在**当前会话**中回复一句引导语 + 海报图片。无需等到定时推送时间。

### 5. 定时任务 & 主动发送

- 使用 `apscheduler` 的 `AsyncIOScheduler` 创建每日定时任务；
- 从配置中解析 `schedule_time`（`HH:MM`）为 CronTrigger；
- 每天到点自动执行：
  1. 拉取今日优惠数据；
  2. 生成海报图片；
  3. 向 `target_groups` 中的所有群发送“文本引导 + 图片”：

     > 为您奉上今日快餐优惠货比三家早报，请查阅。

- 主动发送接口示例（内部逻辑）：

```python
from astrbot.api.event import MessageChain

chain = MessageChain().message(intro_text).file_image(poster_path)
await context.send_message("aiocqhttp:group:123456789", chain)
```

> 若你使用的并非 OneBot v11 / aiocqhttp 适配器，可修改 `_build_group_origin` 实现以适配不同平台。

---

## 🛡 异常处理

插件在多个关键点做了兜底：

- **数据获取失败**：发送纯文本“今日快餐优惠数据获取失败，请稍后重试。”；
- **无优惠数据**：发送“今日暂无监控到的快餐优惠活动。”；
- **海报生成失败**：发送说明性文本，并在日志中输出详细报错；
- **图片发送失败**：降级为纯文本提示用户检查机器人文件读写权限。

所有异常均通过 `astrbot.api.logger` 记录，避免任务直接崩溃。

---

## 🔧 开发与定制

1. **本地开发**
   - 按 AstrBot 官方插件开发流程，将本仓库作为子插件放入 `data/plugins/`；
   - 开启 AstrBot 热重载后，可在 WebUI 插件管理中一键重载当前插件。

2. **接入真实数据源**
   - 修改 `main.py` 中的 `fetch_today_deals`，在内部使用 `httpx` 等库请求你的接口；
   - 将接口返回映射为当前使用的数据结构；
   - 保持函数签名与字段名不变，无需调整其余逻辑。

3. **调整视觉样式**
   - 修改 `_generate_poster_sync` 中的布局、颜色与字体；
   - 如需加载真实商品主图，可在该函数中使用 `Image.open(BytesIO(...))` 将远程图片绘制到卡片区域。

---

## 📦 依赖

`requirements.txt` 中已列出所有运行时依赖：

```txt
apscheduler>=3.10.4
pillow>=10.0.0
httpx>=0.27.0
```

确保你的 AstrBot 运行环境已安装这些依赖。

---

## 📄 许可证

根据你实际仓库要求选择合适的开源协议（例如 MIT / Apache-2.0 / GPL-3.0 等），  
并在根目录添加对应的 `LICENSE` 文件。

