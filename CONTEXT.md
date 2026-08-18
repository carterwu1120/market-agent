# Market Agent

台股智慧分析 Discord bot：整合新聞、技術面、基本面、籌碼面、社群訊號，交由 Claude Code 生成有數據來源的投資分析報告。

## Language

**Daily brief**:
每日固定排程（08:30／12:00／14:30）或 `/brief` 觸發的市場摘要。資料抓取是**固定的純 Python 流程**，保證每次都查完全部資料來源，不經過 LLM 決定要不要查——因為排程推播無人在場即時發現遺漏。
_Avoid_: 每日簡報（可用於使用者介面文案，但程式碼/文件討論架構時一律用 daily brief）

**React**:
除了 daily brief 以外的所有查詢（個股、產業、主題、歷史、比較、跨輪追問）統稱的路徑。完全交給 Claude Code 自己的 agentic tool-calling loop（透過 MCP）決定要呼叫哪些工具、呼叫幾次，沒有任何強制或保證一定會呼叫特定工具的機制。
_Avoid_: ReAct agent（可能誤導成有獨立的 LangChain ReAct 實作——那份已移除，唯一實作是 Claude Code 原生 loop）

**Intent**:
一則使用者訊息被分類成 `daily_brief` 或 `react` 兩者之一。曾經有更細的分類（`stock_query`／`sector_query`／`theme_query`／`history_query`），但那是舊架構的殘留，實際程式碼從未再產生這些值。
_Avoid_: 把 intent 當成「查詢類型」的完整列舉——它現在只回答一個是非題：要不要走 daily brief 那條固定路徑。
