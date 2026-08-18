# Market Agent

台股智慧分析 Discord Bot，基於 **LangGraph multi-agent 架構**，整合即時新聞、技術面、基本面、籌碼面與社群訊號，生成有數據來源的投資分析報告。

> **每一筆數據都標注來源，不憑空生成數字。**

---

## 功能

- 📰 **即時新聞** — RSS（Bloomberg、FT、經濟日報、MoneyUDN）+ NewsAPI + GNews 多源整合，任一來源失敗不影響其他；Redis 快取 30 分鐘，重複查詢自動跳過爬蟲
- 🏭 **類股查詢** — 輸入「半導體」「傳產」「金融股」等關鍵字，自動從 TWSE 抓取該產業所有成份股（1077 檔 / 32 產業），fallback 至代表股清單
- 🎯 **概念股查詢** — 輸入「機器人」「元宇宙」「低軌衛星」「AI人工智慧」等主題，從 **CMoney 概念股分類**（159 個概念，涵蓋所有熱門題材）直接取得結構化個股清單；CMoney 無匹配時 fallback 至新聞關鍵字提取（鉅亨 + UDN）
- 📈 **技術面分析** — RSI、MACD、MA20/60、EMA12、乖離率、布林帶（yfinance + pandas-ta）；Redis 快取 30 分鐘，避免重複計算 ✅
- 📊 **基本面分析** — PE、PB、EPS、ROE、分析師評等（Yahoo Finance）；Redis 快取 24 小時 ✅
- 🧩 **籌碼面分析** — 三大法人買賣超（TWSE 公開 API）✅ | 融資融券 ⚠️ API 不穩定
- 💬 **社群訊號** — PTT Stock 板關鍵字監控（大單、訂單、法說等）
- 🧠 **RAG 知識庫** — pgvector 向量搜尋，自訂技術分析知識（需 Docker 啟動 DB）
- 💾 **頻道共用對話記憶** — Redis session 以頻道為單位共享，每則訊息附帶 `[username]` 前綴，LLM 能辨別不同使用者的發言並判斷是否為接話；每輪回覆儲存 `conclusion`、`symbols`、`intent`，支援跨使用者的 follow-up（「那聯發科呢？」即使是不同人問也能繼承話題）
- 🔍 **ReAct 研究模式** — 複雜/比較型問題（「比較半導體和航運哪個強」「找最值得買的機器人股」）自動進入 ReAct loop，LLM 自主決定呼叫哪些工具、呼叫幾次，直到得出結論；注入對話歷史，支援跨輪比較
- 📣 **法說會與技術新聞** — 鉅亨網個股搜尋，優先抓法說會、技術突破、產品相關報導，作為「獨家技術亮點」段落的唯一來源
- ⏰ **定時排程報告** — 每日自動在 08:30（盤前）、12:00（盤中）、14:30（收盤後）發送市場報告至指定 Discord 頻道；設定 `SCHEDULE_REPORT_CHANNEL_ID` 即可啟用，無需手動觸發
- 🤖 **LLM 後端可切換** — 預設 **Claude Code CLI**（`claude -p`，不需 API key，算在 Claude Code 訂閱額度內）；也可切回 OpenAI / Gemini / Vertex AI，改 `.env` 即可

---

## Multi-Agent 架構

Multi-agent 的核心定義在 **[`src/agents/graph.py`](src/agents/graph.py)**，使用 **LangGraph `StateGraph`** 實作。

### 流程圖

系統有**兩條執行路徑**，由 orchestrator 根據問題類型決定走哪條：

```mermaid
flowchart TD
    subgraph MEM["記憶層跨輪對話"]
        REDIS["Redis\nsession 歷史 · symbols · intent · conclusion"]
        PG["PostgreSQL\n長期記憶 · RAG 知識庫向量"]
    end

    USER([使用者輸入]) --> ORCH

    MEM -->|注入 conversation_history| ORCH

    ORCH["Orchestrator\nLLM 分類 intent\ndaily_brief / stock_query / sector_query\ntheme_query / research / follow_up"]

    ORCH -->|intent = research| RA
    ORCH -->|其他 intent\nfan-out 並行| NA & TA & FA & CA & SA & RAGA

    subgraph RA["research_agent（ReAct Loop）"]
        LLM_R["LLM 自主決定\n呼叫哪些工具"] -->|tool_calls| TN["ToolNode\n5 種工具"]
        TN -->|工具結果| LLM_R
    end

    NA["news_agent\n爬取近 24h 新聞並摘要"]
    TA["technical_agent\n抓股價計算 RSI/MACD/均線/乖離率"]
    FA["fundamental_agent\n抓 PE/ROE/EPS 等基本面數據"]
    CA["chip_agent\n抓三大法人買賣超"]
    SA["social_agent\n爬 PTT 擷取社群情緒訊號"]
    RAGA["rag_agent\n向量搜尋知識庫取得相關背景知識"]

    NA & TA & FA & CA & SA & RAGA --> SYN

    SYN["Synthesizer\nLLM 整合報告\n解析 conclusion"]

    RA -->|直接輸出，不經 synthesizer| END([最終報告\n儲存 symbols · intent · conclusion → Redis])
    SYN --> END
```

### 兩條路徑的關鍵差異

| | **一般查詢路徑** | **ReAct 研究路徑** |
|--|--|--|
| 觸發條件 | 單一明確問題（「台積電分析」「半導體類股」）| 複雜/比較型問題（「比較兩個產業」「找最強的股」）|
| 執行方式 | 固定 fan-out，所有 agent 並行跑完 | LLM 自主決定呼叫哪些工具、幾次 |
| 工具決策者 | Python 函數 `_route_after_orchestrator` | LLM 本身（ReAct loop）|
| 報告生成 | synthesizer 整合所有 agent 的結果 | research_agent 直接輸出 |
| 速度 | 快（固定路徑）| 較慢（動態迭代，最多 6 輪）|

### 各 Agent 說明

#### Graph Nodes（LangGraph 上的節點）

| 檔案 | 層級 | 職責 | 數據來源 |
|------|------|------|---------|
| [`orchestrator.py`](src/agents/orchestrator.py) | 入口 | intent 分類 + ticker/sector 提取 + 路由決策 | LLM + Redis |
| [`research_agent.py`](src/agents/research_agent.py) | 決策層（ReAct）| 複雜問題的 ReAct loop，LLM 自主呼叫工具直到得出結論 | LLM + 內建工具 |
| [`news_agent.py`](src/agents/news_agent.py) | 執行層 | 抓取近 24h 新聞（Redis 快取命中時跳過）| RSS / NewsAPI / GNews |
| [`technical_agent.py`](src/agents/technical_agent.py) | 執行層 | RSI、MACD、MA20/60、乖離率、BB + 法說會新聞 | yfinance + pandas-ta + 鉅亨網 |
| [`fundamental_agent.py`](src/agents/fundamental_agent.py) | 執行層 | PE、PB、EPS、ROE、分析師評等 | Yahoo Finance |
| [`chip_agent.py`](src/agents/chip_agent.py) | 執行層 | 三大法人買賣超 ✅ / 融資融券 ⚠️ | TWSE 公開 API |
| [`social_agent.py`](src/agents/social_agent.py) | 執行層 | PTT 關鍵字訊號 | PTT Stock |
| [`rag_agent.py`](src/agents/rag_agent.py) | 執行層 | 知識庫向量搜尋 | pgvector |
| [`synthesizer.py`](src/agents/synthesizer.py) | 整合層 | 整合所有 agent 結果，呼叫 LLM 生成報告 | 所有執行層 |

#### research_agent 內建工具（不是 graph nodes，是 ReAct 內部工具）

| 工具函數 | 對應的底層工具 |
|---------|-------------|
| `sector_lookup(keyword)` | `sector_data.get_sector_symbols()` |
| `theme_lookup(keyword)` | `theme_search.search_theme_stocks()` |
| `technical_analysis(symbol)` | `stock_data.get_technical_indicators()` |
| `fundamental_analysis(symbol)` | `stock_data.get_fundamental_data()` |
| `company_news(symbol)` | `company_insight.get_company_insights()` |

> **重要**：這些工具只存在於 `research_agent` 內部，不是 LangGraph graph 上的獨立節點。`research_agent` 是唯一有決策能力的節點，其他執行層 agent 只做固定的一件事。

### LangGraph 核心概念（對應程式碼）

```python
# src/agents/graph.py

builder = StateGraph(AgentState)          # 共享狀態定義於 state.py

builder.set_entry_point("orchestrator")

# Conditional fan-out：根據 intent 與 cache 狀態決定啟動哪些 agent
builder.add_conditional_edges(
    "orchestrator",
    _route_after_orchestrator,            # 回傳要執行的 node 名稱列表
    {node: node for node in _ALL_DATA_AGENTS},
)

# 所有 data agent 完成後 → synthesizer（自動 join）
for node in _ALL_DATA_AGENTS:
    builder.add_edge(node, "synthesizer")
```

**動態 routing**：`_route_after_orchestrator` 根據 `state.news_cached` 決定是否把 `news_agent` 加入 fan-out 清單。Redis 有新聞快取（TTL 30 分鐘）時，orchestrator 直接跳過 news_agent，節省 20–30 秒爬蟲時間。這是 LangGraph 相較傳統靜態 pipeline 的核心優勢：**每次執行的圖路徑可依 runtime 狀態動態調整**。

**共享狀態**（[`state.py`](src/agents/state.py)）：所有 agent 讀寫同一個 `AgentState`，使用 `operator.add` reducer 讓各 agent 的結果自動 append 合併，不互相覆蓋。

### 動態路由流程圖

```
第一次查詢（無快取）：
orchestrator → [news_agent, technical_agent, chip_agent, ...] → synthesizer
                      ↓
               爬蟲 + 存 Redis（TTL 30 min）

30 分鐘內再次查詢（快取命中）：
orchestrator → [technical_agent, chip_agent, social_agent, rag_agent] → synthesizer
                      ↑
               news_agent 被跳過，synthesizer 直接從 Redis 讀新聞
```

---

## 快速開始

### 前置需求

- Docker & Docker Compose
- Discord Bot Token（[Developer Portal](https://discord.com/developers/applications) 建立）
- [Claude Code CLI](https://docs.claude.com/claude-code) 已安裝並登入（`claude` 指令可用），且 Claude Code 訂閱有效 —— 預設 LLM 後端需要它；不想用的話改 `.env` 的 `LLM_BACKEND` 見下方「切換 LLM 後端」

### 1. 設定環境變數

```bash
cp .env.example .env
```

最少需填：
```env
DISCORD_BOT_TOKEN=你的token
POSTGRES_PASSWORD=自訂密碼
```

### 2. 啟動服務

```bash
docker compose up -d
```

服務清單：
| 服務 | Port | 說明 |
|------|------|------|
| `app` | — | Discord bot 主程式 |
| `postgres` | 5432 | PostgreSQL + pgvector |
| `redis` | 6379 | Session cache |

> LLM 預設走本機 Claude Code CLI（不在 docker-compose 裡，見上方「前置需求」）。

### 3. 初始化知識庫（首次）

```bash
docker compose exec app python scripts/init_knowledge_base.py
```

### 4. 使用 Discord Bot

在 Discord 中：

| 指令 | 說明 |
|------|------|
| `/brief` | 今日市場摘要與投資建議 |
| `/stock 2330 2454` | 分析指定股票 |
| `/clear` | 清除對話記憶 |
| `/help` | 顯示說明 |
| 群組頻道：@bot 問話 | 自由對話模式（支援跨使用者 follow-up）|
| 私訊（DM）：直接打字，不用 @bot | 一對一對話模式，同樣支援 follow-up |

> **DM 模式須知**：`on_message` 已原生支援 DM 自由文字對話（[`src/bot/discord_bot.py`](src/bot/discord_bot.py) 的 `isinstance(message.channel, discord.DMChannel)` 判斷），不需要額外設定。但要能收到 DM 內文，需在 [Discord Developer Portal](https://discord.com/developers/applications) → 你的 App → **Bot** 頁 → **Privileged Gateway Intents** 區塊，手動開啟 **Message Content Intent** 並存檔——這個設定無法純靠程式碼開啟，光有 `intents.message_content = True` 不夠。改完設定要重啟 bot 才生效。
>
> Slash command（`/brief` 等）能否在 DM 使用，取決於 bot 的安裝方式（Guild Install vs User Install），請至 Developer Portal 的 **Installation** 頁確認。

#### 啟用定時排程報告

在 `.env` 設定目標頻道 ID：

```env
SCHEDULE_REPORT_CHANNEL_ID=你的頻道ID   # 右鍵頻道 → 複製頻道 ID（需開啟開發者模式）
SCHEDULE_ENABLED=true                    # 預設已開啟
```

啟動後自動在以下時間發送報告：

| 時間 | 內容 |
|------|------|
| 08:30 | 盤前報告：昨收、隔夜美股、三大法人、今日開盤重點 |
| 12:00 | 盤中報告：目前指數與成交量、盤勢強弱、下午方向 |
| 14:30 | 收盤報告：今日漲跌幅、三大法人明細、明日操作建議 |

---

## 本地 CLI 測試（不需要 Discord）

不想設定 Discord Bot 時，可直接用 CLI 測試完整 agent pipeline：

```bash
python -m src.cli
```

| 指令 | 說明 |
|------|------|
| `/brief` | 今日市場摘要 |
| `/stock 2330 2454` | 分析指定股票 |
| `/schedule pre\|mid\|post` | 觸發排程報告（盤前／盤中／收盤後）|
| `/clear` | 清除當前 session |
| `/help` | 顯示說明 |
| 直接輸入問題 | 自由對話（支援 follow-up） |

> CLI 與 Discord Bot 使用同一套 `run_agent` pipeline，行為完全一致。DB 服務（PostgreSQL、Redis）需在 Docker 中運行；若未啟動，記憶與 RAG 功能自動降級，基本查詢仍可使用。

---

## 切換 LLM 後端

只需修改 `.env`，不需改任何程式碼：

```env
# Claude Code CLI（預設，不需 API key）
# 需求：本機已安裝並登入 `claude`（Claude Code CLI），且 Claude Code 訂閱有效
LLM_BACKEND=claude_code

# OpenAI（metered API key）
LLM_BACKEND=openai
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...

# Gemini（Developer API）
LLM_BACKEND=gemini
LLM_PROVIDER=gemini
LLM_MODEL=gemini-2.0-flash
GEMINI_API_KEY=...

# Vertex AI（GCP Application Default Credentials）
LLM_BACKEND=vertex
LLM_PROVIDER=vertex
LLM_MODEL=gemini-2.5-flash
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
# 需先執行：gcloud auth application-default login
```

### Claude Code 後端的兩種呼叫形狀

| 用途 | 函數 | 機制 | 使用位置 |
|------|------|------|---------|
| 單次分類/擷取/報告生成（無工具） | `claude_code_chat()` | `claude -p --tools ""`，純文字 in/out | orchestrator（intent 分類）、market_agent（熱門股擷取）、synthesizer（報告生成）|
| 需要即時查資料的 ReAct 迴圈 | `claude_code_research()` | `claude -p --mcp-config`，Claude Code 用原生 MCP tool-calling 呼叫 [`src/mcp_server.py`](src/mcp_server.py) 暴露的 11 個工具 | research_agent |

> **為什麼分兩種**：早期曾嘗試用純文字 prompt 要求 `claude -p` 輸出 `{"action": "...", "args": {...}}` 這種自訂 JSON 協議來模擬 tool-calling，實測約 30–40% 時候會失敗——`claude -p` 背後是完整的 agent runtime，不是單純的文字補全 API，遇到「不確定工具是否真的存在」的情境會自行幻想/扮演整個工具呼叫與回傳結果。改用真正的 MCP tool-calling 後，這個失敗模式完全消失（測試中連續 10/10 次正確執行，含多工具串接）。單次分類這類「不需要工具」的呼叫則沒有這個問題，維持純文字 `claude -p` 即可穩定運作。

---

## 專案結構

```
market-agent/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── data/
│   └── knowledge_base/          # 放 .md/.txt 會自動被 RAG 索引
│       └── technical_analysis_basics.md
├── scripts/
│   └── init_knowledge_base.py   # 初始化向量知識庫
└── src/
    ├── main.py                  # 啟動入口
    ├── config.py                # 所有設定（pydantic-settings）
    ├── llm.py                   # LiteLLM 備選後端（openai/gemini/vertex）
    ├── llm_claude_code.py       # Claude Code CLI 後端（預設，claude_code_chat / claude_code_research）
    ├── llm_router.py            # 後端切換（讀 settings.llm_backend）
    ├── mcp_server.py            # MCP server：暴露 11 個工具給 claude -p --mcp-config 呼叫
    ├── agents/
    │   ├── graph.py             # ★ LangGraph 圖定義（核心）
    │   ├── state.py             # 共享狀態 AgentState
    │   ├── orchestrator.py      # 路由 agent
    │   ├── news_agent.py
    │   ├── technical_agent.py
    │   ├── fundamental_agent.py
    │   ├── chip_agent.py
    │   ├── social_agent.py
    │   ├── rag_agent.py
    │   └── synthesizer.py       # 最終報告生成
    ├── tools/                   # 各數據源工具函數
    │   ├── news_fetcher.py      # RSS + NewsAPI
    │   ├── stock_data.py        # yfinance（價格、技術、基本面）
    │   ├── chip_data.py         # TWSE API（三大法人、融資融券）
    │   ├── social_signal.py     # PTT scraper
    │   ├── sector_data.py       # TWSE ISIN 類股查詢（官方產業分類）
    │   ├── cmoney_concept.py    # CMoney 概念股爬蟲（159 個主題分類）
    │   └── theme_search.py      # 主題搜尋（CMoney 優先 + 新聞 fallback）
    ├── memory/
    │   ├── models.py            # SQLAlchemy ORM（含 pgvector）
    │   ├── database.py          # async engine + session factory
    │   ├── session_store.py     # Redis 短期記憶
    │   └── conversation_repo.py # PostgreSQL 長期記憶
    ├── rag/
    │   ├── embedder.py          # sentence-transformers / OpenAI
    │   └── knowledge_store.py   # pgvector 存取 + 相似度搜尋
    └── bot/
        └── discord_bot.py       # Discord slash commands + 訊息處理
```

---

## 擴充指引

### 新增一個 Agent

1. 在 [`src/agents/`](src/agents/) 建立新檔案，實作 `async def your_agent_node(state: AgentState) -> dict`
2. 在 [`graph.py`](src/agents/graph.py) `build_graph()` 加入 `builder.add_node()`
3. 把新 agent 加入 `_ALL_DATA_AGENTS` 或在 `_route_after_orchestrator` 中指定觸發條件

### 新增知識庫文件

把 `.md` 或 `.txt` 放進 `data/knowledge_base/`，重新執行：
```bash
python scripts/init_knowledge_base.py
```

### 新增 Telegram 支援

在 `src/bot/` 建立 `telegram_bot.py`，直接呼叫 `from src.agents.graph import run_agent`，agent 核心完全共用。

### 新增一個 MCP 工具（給 Claude Code ReAct 用）

`research_agent` 走 Claude Code 後端時，工具清單來自 [`src/mcp_server.py`](src/mcp_server.py)，不是 `research_agent.py` 裡的 LangChain `@tool`（那份是 LiteLLM 後端專用，兩邊目前各自維護一份同名工具，邏輯本體共用 `src/tools/*.py`）。新增工具時兩邊都要加：

1. 在 `research_agent.py` 用 `@tool` 註冊一份（LiteLLM 路徑用）
2. 在 `mcp_server.py` 用 `@mcp.tool()` 註冊同名同邏輯的一份（Claude Code 路徑用）
3. 兩邊都指向同一個 `src/tools/*.py` 底層函數，不要各寫一份邏輯

### 已知的資料品質注意事項

網頁爬蟲類工具（`sector_data.py`、`theme_search.py`/`cmoney_concept.py`）依賴目標網站的 HTML class 結構，改版會直接讓工具失效或混入雜訊，且不會拋錯——回傳的清單長度看起來正常，內容卻可能不對。曾發生過的實例：`cmoney_concept.py` 的選股器一度沒有限定在概念股表格區塊內，導致把導覽列固定連結（如台積電、大盤 ETF）也當成任一主題的成分股回傳，兩個完全不相關的主題查詢結果有一半重疊卻毫無察覺（見 commit `ee64efa`）。修改或新增這類爬蟲工具後，建議至少手動跑兩個不同關鍵字比對結果、確認沒有異常重疊，而不是只看「有沒有回傳資料」。

---

## 技術棧

| 層 | 技術 |
|----|------|
| Agent 編排 | LangGraph 0.2+ |
| LLM（預設） | Claude Code CLI（`claude -p` + MCP tool-calling） |
| LLM（備選） | LiteLLM + LangChain（OpenAI / Gemini / Vertex AI） |
| 股票數據 | yfinance, pandas-ta |
| 台股籌碼 | TWSE 公開 API, goodinfo scraper |
| 新聞 | feedparser (RSS), NewsAPI |
| 社群訊號 | httpx + BeautifulSoup (PTT) |
| 向量搜尋 | pgvector + sentence-transformers (BAAI/bge-m3) |
| 對話記憶 | Redis（短期）+ PostgreSQL（長期） |
| Bot | discord.py 2.4+ |
| 部署 | Docker Compose |
