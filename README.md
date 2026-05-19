# Market Agent

台股智慧分析 Discord Bot，基於 **LangGraph multi-agent 架構**，整合即時新聞、技術面、基本面、籌碼面與社群訊號，生成有數據來源的投資分析報告。

> **每一筆數據都標注來源，不憑空生成數字。**

---

## 功能

- 📰 **即時新聞** — RSS（Bloomberg、FT、經濟日報、MoneyUDN）+ NewsAPI + GNews 多源整合，任一來源失敗不影響其他；Redis 快取 30 分鐘，重複查詢自動跳過爬蟲
- 🏭 **類股查詢** — 輸入「半導體」「傳產」「金融股」等關鍵字，自動從 TWSE 抓取該產業所有成份股（1077 檔 / 32 產業），fallback 至代表股清單
- 📈 **技術面分析** — RSI、MACD、MA20、EMA12（yfinance + pandas-ta）✅
- 📊 **基本面分析** — PE、PB、EPS、ROE、分析師評等（Yahoo Finance）✅
- 🧩 **籌碼面分析** — 三大法人買賣超（TWSE 公開 API）✅ | 融資融券 ⚠️ API 不穩定
- 💬 **社群訊號** — PTT Stock 板關鍵字監控（大單、訂單、法說等）
- 🧠 **RAG 知識庫** — pgvector 向量搜尋，自訂技術分析知識（需 Docker 啟動 DB）
- 💾 **對話記憶** — Redis session + PostgreSQL 長期記憶（需 Docker 啟動 DB）
- 🤖 **LLM 可切換** — Ollama（本地）/ OpenAI / Gemini / vLLM，改 `.env` 即可

---

## Multi-Agent 架構

Multi-agent 的核心定義在 **[`src/agents/graph.py`](src/agents/graph.py)**，使用 **LangGraph `StateGraph`** 實作。

### 流程圖

```
Discord 訊息
      │
      ▼
┌─────────────────┐
│  Orchestrator   │  ← 意圖分類（daily_brief / stock_query）
│  orchestrator.py│    + ticker 提取（LLM + regex）
└────────┬────────┘
         │ add_conditional_edges（fan-out）
         │
    ┌────┴─────────────────────────────────────┐
    │  並行執行（asyncio，全部完成後才進 synthesizer）  │
    ├──────────────┬────────────┬──────────────┤
    ▼              ▼            ▼              ▼
news_agent  technical_agent  chip_agent  social_agent
新聞抓取     技術指標          三大法人      PTT訊號
    │              │            │              │
    │       fundamental_agent   │          rag_agent
    │         基本面數據         │          向量搜尋
    └──────────────┴────────────┴──────────────┘
                         │
                         ▼
              ┌──────────────────┐
              │   Synthesizer    │  ← 整合所有數據 → 生成報告
              │  synthesizer.py  │    每個數據點都引用來源 URL
              └──────────────────┘
                         │
                         ▼
              Discord 回覆（自動分段）
```

### 各 Agent 說明

| 檔案 | 職責 | 數據來源 |
|------|------|---------|
| [`orchestrator.py`](src/agents/orchestrator.py) | 意圖分類、ticker/sector 提取、路由決策（含快取判斷） | LLM + Redis |
| [`news_agent.py`](src/agents/news_agent.py) | 抓取近 24h 新聞（Redis 快取命中時由 orchestrator 跳過）| RSS（Bloomberg/FT/經濟日報/MoneyUDN）、NewsAPI、GNews |
| [`technical_agent.py`](src/agents/technical_agent.py) | RSI、MACD、MA20、MA60 ✅、BB ✅、EMA12 ✅ | yfinance + pandas-ta |
| [`fundamental_agent.py`](src/agents/fundamental_agent.py) | PE、PB、EPS、ROE、分析師評等 ✅ | Yahoo Finance |
| [`chip_agent.py`](src/agents/chip_agent.py) | 三大法人買賣超 ✅ / 融資融券 ⚠️ | TWSE 公開 API |
| [`social_agent.py`](src/agents/social_agent.py) | PTT 關鍵字訊號 | PTT Stock |
| [`rag_agent.py`](src/agents/rag_agent.py) | 知識庫向量搜尋 | pgvector |
| [`synthesizer.py`](src/agents/synthesizer.py) | 整合所有數據，呼叫 LLM 生成報告 | 所有以上 |

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
| `ollama` | 11434 | 本地 LLM（預設） |

### 3. 拉取 LLM 模型

```bash
docker compose exec ollama ollama pull llama3.1:8b
```

### 4. 初始化知識庫（首次）

```bash
docker compose exec app python scripts/init_knowledge_base.py
```

### 5. 使用 Discord Bot

在 Discord 中：

| 指令 | 說明 |
|------|------|
| `/brief` | 今日市場摘要與投資建議 |
| `/stock 2330 2454` | 分析指定股票 |
| `/clear` | 清除對話記憶 |
| `/help` | 顯示說明 |
| 直接 @bot 問話 | 自由對話模式 |

---

## 切換 LLM

只需修改 `.env`，不需改任何程式碼：

```env
# Ollama（本地，預設）
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b

# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...

# Gemini
LLM_PROVIDER=gemini
LLM_MODEL=gemini-1.5-flash
GEMINI_API_KEY=...

# vLLM（本地 OpenAI 相容）
LLM_PROVIDER=vllm
LLM_MODEL=mistral-7b
VLLM_BASE_URL=http://localhost:8000
```

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
    ├── llm.py                   # LLM 抽象層（LiteLLM + LangChain）
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
    │   ├── chip_data.py         # TWSE API + goodinfo scraper
    │   └── social_signal.py     # PTT scraper
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

---

## 技術棧

| 層 | 技術 |
|----|------|
| Agent 編排 | LangGraph 0.2+ |
| LLM 抽象 | LiteLLM + LangChain |
| 本地 LLM | Ollama / vLLM |
| 股票數據 | yfinance, pandas-ta |
| 台股籌碼 | TWSE 公開 API, goodinfo scraper |
| 新聞 | feedparser (RSS), NewsAPI |
| 社群訊號 | httpx + BeautifulSoup (PTT) |
| 向量搜尋 | pgvector + sentence-transformers (BAAI/bge-m3) |
| 對話記憶 | Redis（短期）+ PostgreSQL（長期） |
| Bot | discord.py 2.4+ |
| 部署 | Docker Compose |
