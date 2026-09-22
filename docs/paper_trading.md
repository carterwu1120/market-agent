# 紙上交易迴圈（Paper Trading Loop）

## 2026-09：績效與執行修正

- 缺少任何持倉報價時，總資產與總報酬回傳 `null`，Discord、CLI 與 MCP 顯示「估值不完整」。現金與已實現統計仍可用，不把缺報價部位當成零元。
- 買入條件依建立時間計算有效期，`PAPER_TRADING_CONDITION_TTL_SECONDS` 預設 86400 秒（24 小時，含非交易時段）。重啟後同樣套用既有條件；過期條件取消並記錄 `condition_expired`，必須重新評估、建立新條件。賣出保護條件不受此期限限制。
- 條件買入在交易鎖內重新檢查條件是否有效及最新報價。`close` 條件重新比對門檻；向上突破買入另受 `PAPER_TRADING_MAX_CHASE_PCT=2.0` 追價上限保護。這是執行有效性檢查，尚非基本面、公告與投資論點的全面重新研究。
- 只有成功成交才把條件標記為 triggered；失敗保留條件，下輪重試並記錄錯誤。SQLite 平倉寫入失敗會向上傳遞，避免誤報成交。
- 停損、條件單、研究使用獨立 asyncio 任務，關閉主迴圈時一併取消。仍依賴程式在線與資料來源回應，不保證精確每 60 秒執行。
- 每次決策保存 `decision_input`（資料包、提示與策略筆記）、`decision_proposed`、`decision_executed`，以同一 id 串聯；開市期間另每 30 分鐘保存 `equity_snapshot`。紀錄存於既有 `paper_trading_log`，不需要搬移帳本。

尚未納入交易成本、基準比較、供應鏈論點重新驗證；快照從新版啟動後開始累積，不回填過去行情。此次變更不修改既有成交，也不自動平倉；需重啟程式生效。

實驗性功能：在台股交易時間內持續運作、自己判斷買賣的模擬交易背景任務。完全不牽涉真實資金或第三方交易帳戶——原本評估過接 CMoney 大富翁模擬帳戶，但它的登入系統已經換成 OIDC，唯一的第三方套件已經失效多年，所以改成自己記帳、自己算損益。

## 跟核心功能的關係

**只有一個程式要啟動：`uv run python -m src.main`**。紙上交易迴圈是這個 Discord bot 啟動時，依 `.env` 的 `PAPER_TRADING_ENABLED` 開關決定要不要一起以背景任務啟動——不是另一個要分別啟動的程式。

```mermaid
flowchart TD
    subgraph MAIN["src.main（唯一要啟動的程式，同一個 process）"]
        BOT["Discord Bot\n/brief · /stock · /performance · ..."]
        SCHED["scheduler.py\n定時報告 08:30 / 12:00 / 14:30"]
        PTL["paper_trading_loop.py\n背景任務，PAPER_TRADING_ENABLED=true 才啟動"]
    end

    PTL --> BROAD["廣掃\n新聞選股；新標的最多 2 檔預熱公司競爭力證據"]
    PTL --> TIGHT["緊盯\n每輪最多兩個目標"]
    TIGHT --> PACKET["固定並行資料收集\n技術 · 基本 · 籌碼 · 公告 · 新聞 · 精簡競爭力快取"]
    PACKET --> DECIDE["單次 LLM JSON 決策"]
    DECIDE --> GUARD["程式驗證範圍與風控"]
    GUARD --> SQLITE[("PaperBroker / SQLite\ndata/market_agent.db")]
    BOT -.->|"自由提問"| REACT["ReAct + MCP\n僅供 Discord 研究"]
    BOT --> SQLITE
    SCHED --> SQLITE
```

之後如果要正式上線自動交易，只要把 `PAPER_TRADING_ENABLED` 關掉，核心的查股票、`/brief`、`/stock` 功能完全不受影響。背景交易使用獨立的 `paper_trading_decision.py`，Discord 自由提問才使用 ReAct；兩者共用資料函式與 Broker 業務規則，但不共用執行流程。

真正要留意的 trade-off：因為現在共用同一個 process，紙上交易迴圈如果哪天真的丟出沒接住的例外把整個 process 弄掛，Discord bot 也會跟著死掉（反之亦然）。`run()` 已經把每個週期包在 try/except 裡、錯誤只會被記錄不會往外炸，風險不高，但這是唯一真的要拿來換的東西。

### 執行層是可以替換的（`Broker` 介面）

`paper_trade_buy`/`sell` 背後的 `buy()`/`sell()`（`src/tools/paper_trading_actions.py`）只負責業務規則（部位上限、分配額度計算、格式驗證），真正「執行交易」的部分抽成一個獨立的 `Broker` 介面（`src/tools/broker.py`），現在唯一的實作是 `PaperBroker`（`src/tools/paper_broker.py`，寫進 `paper_positions` 這張模擬帳本）。這是為了以後如果要接真實券商（例如永豐金 Shioaji）鋪路——寫一個新的 `ShioajiBroker` 實作同樣的介面，`buy()`/`sell()` 的業務規則完全不用改。

但這不代表「換一個 class 就能無痛切換成真的下單」——`docs/adr/0002-execution-backend-seam.md` 記錄了兩個目前刻意先不解決的落差：真實下單是非同步的（Shioaji 送出訂單後用 callback 通知成交，不是像現在這樣查到價格就假設成交）、現金的真相來源不一樣（真實帳戶要問券商 API，不是我們自己算的模擬帳本）。而且機械式停損/條件單「完全自動觸發、不經過人」這個設計，前提是「反正沒有真的錢」——真的要接真實下單時，這個風險胃口需要重新討論，不是介面換掉就自動安全。

## 決策依據什麼資訊——背景交易不使用 ReAct

實際運行紀錄顯示，讓無人值守的背景交易使用完整 ReAct，會對每檔股票反覆呼叫多個工具；當觀察名單和持倉一起送入時，經常跑滿 300 秒仍無法產生最終決策。因此背景流程改回可控的固定資料管線：程式並行取得技術面、基本面、三大法人、MOPS 公告與目標新聞，再附上已存在的精簡公司競爭力快取，組成最多兩檔的 Decision Packet，最後用一次無工具的 LLM 呼叫產生 JSON。

公司競爭力資料採**條件式蒐集**：廣掃發現全新觀察標的時，每輪最多替 2 檔搜尋技術／產品、量產／商業化、供應鏈地位與競爭／替代風險，存入 SQLite 並快取 7 天。30 分鐘緊盯只讀快取且每類最多帶入 2 筆標題與網址，不會重新搜尋，也不會把完整搜尋摘要塞進每次 prompt。搜尋結果只是待查證證據；新技術不等於獨有、進入供應鏈不等於不可替代，公司品質也不等於目前估值適合買進。

LLM 只提出 `buy`、`sell`、`hold`、`defer`、`watch_long_term`、`set_condition`、`cancel_condition` 或 `drop_watchlist`；程式會驗證股票必須在本輪範圍內、動作必須符合標的角色，再交給既有 `buy()`/`sell()`/條件單或觀察名單函式執行。Discord 的開放式投資研究仍保留 ReAct，背景自動化則優先追求可預測、可稽核與可熔斷。

觀察標的另外可以回傳 `defer` 或 `watch_long_term`：前者表示資料或價格尚未成熟，保留在一般觀察名單；後者只有在長線評估達門檻時才會成功，會保存分類、分數與投資論點，並轉入低頻長期候選池。買進與買入條件單都必須明確提供 `short_term` 或 `long_term`，背景 executor 不再把缺失或錯誤值默默改成短線。

### 長線評估第一版

`long_term_assessment.py` 根據現有資料產生 0–100 的證據分數，檢查營收成長、ROE、毛利率、負債權益比、本益比，以及技術、量產／商業化、供應鏈與競爭風險線索。分類只有四種：

- `verified_advantage`：基本面與商業化／供應鏈證據皆達初步門檻
- `developing`：已有部分商業化或供應鏈線索，但仍待持續驗證
- `theme_only`：目前主要只有技術或題材線索
- `insufficient_evidence`：資料不足

這是候選分流器，不是自動認定公司真的有護城河。只有前兩類能轉成長期觀察，而且 LLM 仍須分別說明投資論點、風險與估值；純突破、爆量或熱門新聞不能作為長線分類理由。

### Discord ReAct 的交易工具隔離

因為交易決策也是走 `run_research()`（跟 `/stock` 同一個函式），一開始 `paper_trade_buy`/`paper_trade_sell` 是跟 `technical_analysis` 那些查詢工具放在同一份工具清單裡——代表你打 `/stock 2330` 只是想看技術面，Claude 手上卻也拿得到下單工具，理論上可能「順便」幫你買一張。

修法是把工具清單拆成兩份（`src/llm_claude_code.py`）：

- `USER_FACING_TOOL_NAMES`（16 個，不含交易工具）—— `run_research()` 的預設值，`/stock`、自由問答都用這份；其中 `company_moat_analysis` 只在長期競爭力或明確技術／供應鏈事件時按需使用
- `PAPER_TRADING_TOOL_NAMES` 保留給相容性與測試，但背景迴圈已不再把交易工具交給 LLM

背景決策完全不暴露 MCP 或交易工具；它拿到的是程式已收集好的資料包。即使 LLM 回傳越權股票或不合法動作，executor 也會拒絕。

這樣「查股票」的對話**物理上**沒有下單工具可以用，不是靠 prompt 裡寫規則約束。

### 個人策略筆記也會納入判斷

`data/knowledge_base/` 底下的個人交易筆記（均線、KD、籌碼、型態學等判斷邏輯）會在每次背景決策時由 `paper_trading_decision.py` 重新讀取並附加到 Decision Packet prompt；改筆記不需要重啟。Discord `/stock` 的 ReAct 路徑也會透過 `research_agent.py` 注入同一份筆記，`daily_brief.py` 則維持自己的固定資料管線。

這份筆記在背景決策與 `/stock` 都是固定注入，不讓模型自行選擇是否讀取。筆記約 17KB（幾千個 token）的成本，被視為換取「保證不漏看使用者策略」的合理代價。

## 運作方式

只在台股交易時間內動作（週一到週五 09:00–13:30，其餘時間直接睡到下個交易時段開始，不會浪費 API 成本查休市的行情）。

```mermaid
flowchart TD
    START(["每 60 秒檢查一次"]) --> TH{"現在是交易時間？\n週一~五 09:00-13:30"}
    TH -->|否| SLEEP["睡到下個交易時段開始"] --> START
    TH -->|是| B{"距上次廣掃\n≥ 40 分鐘？"}
    B -->|是| BROAD["廣掃：抓新聞 → 找熱門股 → 加入觀察名單\n+ 新標的競爭力證據預熱（最多 2 檔）\n+ 過期保險清單 + 檢視長期持倉"]
    B -->|否| T
    BROAD --> T{"距上次緊盯\n≥ 30 分鐘？"}
    T -->|否| START
    T -->|是| GATHER["公平輪替：優先 1 檔觀察股 + 1 檔持倉\n總數最多 2 檔"]
    GATHER --> RESEARCH["程式並行抓固定資料＋讀取精簡競爭力快取\n形成 Decision Packet"]
    RESEARCH --> DECIDE["LLM 單次輸出 JSON 決策"]
    DECIDE --> ACT{"要交易嗎？"}
    ACT -->|買進| BUY["buy(horizon)\n風控驗證後交給 Broker"]
    ACT -->|賣出| SELL["sell()\n風控驗證後交給 Broker"]
    ACT -->|轉長線| LONG["watch_long_term\n保存評分、論點並降低檢視頻率"]
    ACT -->|資料未成熟| DEFER["defer\n保留一般觀察"]
    ACT -->|不追蹤了| DROP["drop_watchlist()\n從觀察名單移除"]
    ACT -->|不動作| HOLD["維持觀察 / 繼續持有"]
    BUY --> NOTIFY["發 Discord 通知"]
    SELL --> NOTIFY
    NOTIFY --> START
    LONG --> START
    DEFER --> START
    DROP --> START
    HOLD --> START
```

短線觀察名單與短線（`short_term`）持倉每 30 分鐘公平輪替；長期觀察候選與長期（`long_term`）持倉則使用 `PAPER_TRADING_LONG_TERM_REVIEW_SECONDS`，預設每 7 天檢視一次。長期候選不受 5 小時 `WATCHLIST_TTL` 的當日清理影響。每輪總數最多兩檔，機械停損與條件單仍每 60 秒檢查，不受 LLM 頻率影響。

觀察名單依「最久未檢查、最早加入」排序，持倉也按最久未檢查輪替。決策失敗會寫入 `research_failed` 並進入 `PAPER_TRADING_FAILURE_COOLDOWN_SECONDS` 冷卻，避免同一批每 30 分鐘重複超時、讓後方標的永遠排不到。Agent 對觀察標的必須在買進、設定條件單、移出觀察名單之間做出處置；持倉才允許 `hold`。

進出場價格一律來自 `paper_trade_buy`/`paper_trade_sell` 工具自己即時查到的真實股價，不是 LLM 自己講的數字——跟這專案其他所有功能同一個原則。

## 資金模擬——不只看單筆 % 報酬，也看真實資金會怎麼變

一開始績效只有 `evaluate_paper_trades()` 算的「每筆漲跌幾 %」（`win_rate`/`avg_return_pct`）——這個指標很乾淨，跟部位大小無關，適合看「選股/進出場判斷準不準」，但看不出「如果拿真的錢照這個策略操作，帳戶會變多少」。現在疊加一層資金模擬，**兩者並存、互不取代**：

- `.env` 的 `PAPER_TRADING_STARTING_CAPITAL`（預設 500,000）設定模擬帳戶的起始本金
- `paper_trade_buy` 多一個 `allocation_pct` 參數——這筆要押多少 % 本金，由 agent 自己依信心程度判斷（跟 `horizon` 一樣是 agent 的策略判斷，不是查證得到的市場數據）；系統用 `PAPER_TRADING_MIN_ALLOCATION_PCT`/`PAPER_TRADING_MAX_ALLOCATION_PCT`（預設 5%~20%）自動夾住，避免一次判斷失常就重壓單一檔
- 股數 = 分配金額 ÷ 進場價，一律用整股計算，紀錄在 `paper_positions` 的 `shares`/`allocation_amount` 欄位（下單當下就固定，之後就算調整 `.env` 的百分比範圍也不會回頭影響已經開的部位）
- 模擬現金不夠分配這筆，`paper_trade_buy` 直接拒絕——這是比 20 檔持倉上限更早發生作用的真實限制
- `src/agents/paper_trading.py` 的 `get_available_cash()`/`simulate_portfolio_equity()` 都是**重播 `paper_positions` 的紀錄現算**，不是另外存一個會漂移的現金餘額欄位——`paper_positions` 本身就是唯一真相來源

`simulate_portfolio_equity()` 也會算「已平倉最大回撤」，但這裡誠實講一個限制：因為沒有存逐日的權益快照，這個回撤只能從「每次平倉時的已實現損益」重建曲線，不包含還持有中部位期間的浮動震盪——不是真正連續的權益曲線，只是一個實用的近似值。

`/performance` 跟 `paper_trade_status`（agent 下單前查額度用）都看得到：可用現金、目前總資產、累計報酬、已平倉最大回撤。

## 持倉上限——防失控，不是操作上限

`paper_trade_buy` 一開始沒有任何數量限制，agent 理論上可以一路買下去。現在短線（`short_term`）跟長期（`long_term`）部位**各自獨立設上限**（`.env` 的 `PAPER_TRADING_MAX_SHORT_TERM_POSITIONS`/`PAPER_TRADING_MAX_LONG_TERM_POSITIONS`，預設各 20 檔），不是兩種合計一個總量——這樣短線頻繁進出不會把長期持有的額度吃光。達上限時 `paper_trade_buy` 直接回傳錯誤，agent 看到錯誤訊息後必須先賣出既有部位才能再買，不會硬闖。

刻意設得寬鬆（20 檔，不是像 5 檔那種平常就會碰到的數字）：紙上交易不牽涉真實資金，「市場真的很好、agent 想多買幾檔」不該被擋，真正要防的是 bug 或誤判造成的無限亂買——跟 `WATCHLIST_TTL` 同一種「硬規則當最後一道保險，不是日常操作限制」的思路。

## 觀察名單怎麼移除——交給 agent 判斷，不是機械倒數

一開始的版本是「2 小時沒買就自動過期」，但這樣不太對：agent 可能還在觀察、還沒到判斷時機，時間到就被機械式踢掉不合理。改成**主要靠 react 自己呼叫 `watchlist_drop`**——它判斷「這支不用再追了」（不管是決定不交易，還是已經處理完畢）就主動移除，這才是正常的操作邏輯。

機械式的過期時間（`WATCHLIST_TTL`，現在調長到 5 小時，涵蓋整個交易時段）還留著，但只當**保險機制**，防止 agent 忘記呼叫 `watchlist_drop` 導致名單一直卡著沒清，不是主要的移除方式。

## 資料存在哪

`paper_positions` 表（`src/memory/store.py`），一個部位一列，紀錄開倉價/日期/理由/`horizon`（short_term/long_term），平倉後補上出場價/日期/原因。

觀察名單改成 `paper_watchlist` 表，**不是只存在記憶體裡**——原因是 `watchlist_drop` 這個 MCP 工具是在另一個 process（`claude -p` 呼叫時另外開的子程序）裡執行的，跟主程式的 `paper_trading_loop.py` 不是同一個 Python process，沒辦法直接改對方記憶體裡的東西，只能透過資料庫這個共用的媒介溝通。

## 查看結果

**Discord**：`/performance` 顯示持倉損益（持有中浮動、已平倉實現）跟整體勝率/平均報酬；`/watchlist` 顯示觀察名單；`/status` 一次顯示 backend、紙上交易開關、現金、資產、持倉、觀察名單與條件單；`/log [筆數]` 顯示最近 1–50 筆永久稽核紀錄。這些都是唯讀查詢，不會呼叫 LLM。有真的買進/賣出時，也會直接發一則訊息到你設定的 `SCHEDULE_REPORT_CHANNEL_ID` 頻道。

**終端面板（不需要 Discord）**：`uv run python -m src.cli` 進去之後打 `/status`，一次看到：

- 模擬帳戶：起始本金、目前總資產、累計報酬、可用現金、已平倉最大回撤
- 持倉表：股票、狀態（持有中/已平倉）、短線/長期、股數、進場價、現價或出場價、損益 %
- 觀察名單：股票、加入時間、目前行情（現場向設定的 provider 查詢）、有沒有被緊盯過
- 有效條件單：id、股票、條件內容、動作（買/賣）

`/status` 純唯讀查詢，不會寫入任何資料。可以在 `python -m src.main`（Discord bot + 紙上交易迴圈）持續運行的同時，另外開一個終端機跑 `python -m src.cli` 觀察——兩個 process 共用同一個 `data/market_agent.db`（WAL mode 支援併發讀寫），互不影響。背後直接重用 `evaluate_paper_trades()`/`simulate_portfolio_equity()`/`get_watchlist()`/`get_active_conditions()` 這幾個函式，跟 Discord 的 `/performance`、agent 的 `paper_trade_status` 看到的是同一套資料來源，不會有兩邊數字對不起來的疑慮。

## 稽核紀錄——「這段期間到底發生了什麼事」

`loguru` 平常印的 log 只在終端機/log 檔裡，不是結構化資料、也沒辦法之後查詢。`paper_trading_log` 表（`src/memory/store.py`）是專門記錄紙上交易迴圈本身動作的**永久稽核紀錄**——跟 `conversation_log`（記錄聊天）同一種精神，但記的是迴圈做了什麼，不會過期、不會被清掉：

- `broad_scan`：找到幾檔候選股
- `tight_scan`／`long_term_review`：緊盯/長期檢視的結論
- `stop_loss_triggered`／`condition_triggered`：機械式檢查為什麼觸發（含當下數值跟門檻）
- `buy`／`sell`：實際成交價、股數、理由
- `condition_set`／`condition_cancelled`：條件單設定/取消
- `budget_exceeded`：花費上限警告

寫入是 best-effort（`log_event()` 失敗只會記警告，不會讓交易本身失敗），而且直接建在 `buy()`/`sell()`/`set_condition()` 這些既有函式的成功路徑上，不是另外一套邏輯——只要交易/條件真的發生，就一定有紀錄。

**查詢**：`uv run python -m src.cli` 進去打 `/log`（預設最近 20 筆，`/log 50` 查更多），會列出時間、事件類型、股票、詳情。

## 每日花費上限

背景決策現在是單次無工具 LLM 呼叫，但仍屬無人值守流程，因此保留多層每日上限。

`.env` 的 `PAPER_TRADING_DAILY_BUDGET_USD`（預設 5.0）設定每個交易日的花費上限。累積花費（來自 `claude -p` 自己回報的 `total_cost_usd`，不是估算）一旦達到上限：

- 決策相關的 `run_research()` 呼叫（緊盯、長期持倉檢視）**暫停**，直到下個交易時段
- 廣掃裡「抓新聞找候選股」那步（便宜、非 agentic）**照常繼續**，觀察名單還是會更新
- 第一次超過上限時，發一則 Discord 通知告訴你；同一天不會重複通知
- 隔天（新的交易日）自動歸零重新計算

Codex CLI 不回報 `total_cost_usd`，所以另有兩個後端無關的硬限制：

- `PAPER_TRADING_MAX_LLM_CALLS_PER_DAY`：每日最多背景決策呼叫數（預設 10）
- `PAPER_TRADING_MAX_TIMEOUTS_PER_DAY`：每日最多 timeout 次數（預設 3）

任一限制觸發後，當日停止新的 LLM 決策；廣掃、機械停損與條件單仍繼續運作。

## 機械式停損——不靠 agent 判斷的最後防線

`react` 每輪判斷都看得到你的策略筆記（均線、KD、型態學都有明確的出場邏輯，見上面「個人策略筆記也會納入判斷」），但這些規則需要 agent 正確解讀當下數據才會觸發——如果某一輪判斷錯，或剛好花費上限被打到、決策呼叫暫停了，部位可能繼續往下滑而沒人介入。

`_check_mechanical_stop_loss()`（`paper_trading_loop.py`）補這個洞：**每個 tick（60 秒）都檢查一次**，透過 `MarketDataProvider.get_quote()` 取得行情並計算浮動損益，跌破門檻就直接呼叫 `sell()` 強制賣出，完全不經過 agent、不呼叫 LLM，所以就算花費上限已經打到、決策全部暫停，這個檢查照樣繼續運作。目前 Yahoo 台股報價有延遲，因此「每分鐘檢查」不代表價格本身是即時的；之後可換成唯讀 Shioaji provider 而不改停損邏輯。

門檻依 horizon 分開設定（`.env`）：

- `PAPER_TRADING_SHORT_TERM_STOP_LOSS_PCT`（預設 15）——短線部位
- `PAPER_TRADING_LONG_TERM_STOP_LOSS_PCT`（預設 20）——長期部位容忍更大波動，符合「長抱本來就要扛得住震盪」的邏輯

只做停損，不做停利——強制在獲利時出場會截斷筆記裡「移動停利點、讓利潤奔跑」的邏輯，停利時機還是留給 agent 判斷。觸發時 `exit_reason` 會記成 `stop_loss`，理由寫「機械式停損保險觸發（非 agent 判斷）」，`/performance` 看得出這筆不是 agent 自己決定的。

## 條件單——agent 決定策略，機械檢查決定時機

原始設計每次緊盯（當時每 5 分鐘，現在已調整為每 30 分鐘）都會重新問一次 react「這支股票現在要不要買/賣」。如果 agent 已經分析過、判斷「等跌破某個價位就買」，重複詢問仍會浪費成本，因此條件單讓中間的等待改由機械檢查處理。

現在 agent 可以呼叫 `paper_trade_set_condition` 設一筆條件單（指標、運算子、門檻、動作），之後改成 `paper_trading_loop.py` 的 `_check_conditions()` 每個 tick（60 秒，跟機械式停損同一個節奏）用真實數據機械式比對，不呼叫 LLM——條件成立的瞬間直接呼叫 `buy()`/`sell()`，不會再問 agent 一次。**跟機械式停損同一個設計語言：agent 決定策略參數，機械檢查負責重複盯著數字看。**

- `indicator` 可以是：
  - `close`（目前行情，來自 `MarketDataProvider.get_quote()`；Yahoo 模式為延遲報價）
  - `sma_5`／`sma_10`／`sma_20`／`sma_60`（五日/十日/月/季均線）、`rsi_14`、`macd`／`macd_signal`／`macd_hist`、`bb_upper`／`bb_lower`、`ema_12`、`kd_k`／`kd_d`（KD 指標）、`volume_ratio`（今日量 ÷ 前 5 日均量，對應筆記裡的「帶量/爆量」）、`bias_20`／`bias_60`（乖離率）——全部來自 `technical_analysis`
  - `trust_streak_days`／`foreign_streak_days`（投信/外資連續買超天數，來自 `chip_analysis`）
  
  五日/十日均線、KD、量比、投信連續買超這幾個，是專門為了對應個人策略筆記（`data/knowledge_base/`）裡大量用到這些概念的判斷邏輯才加的（原本只有算 MA20/MA60，沒有 KD 也沒有量能）
- `operator` 是 `lt`/`gt`/`lte`/`gte`（小於/大於/小於等於/大於等於）
- `action` 是 `buy` 或 `sell`，**進場出場都能用同一套條件機制**：觀察名單的股票可以設「跌破 600 就買」（進場），已持有的部位也可以設「跌破十日線就賣」（出場，比對照筆記第 2 條的均線出場邏輯）。`buy` 用 `horizon`/`allocation_pct` 決定怎麼買，`sell` 用 `exit_reason` 決定平倉理由
- 條件**只會觸發一次**——觸發後不管背後的 `buy()`/`sell()` 有沒有真的成功（例如現金不夠被拒絕），這筆條件都會標記為已觸發、不會每個 tick 重複嘗試，避免無限重試同一個已經失敗的交易
- 觸發前 agent 還沒下單，這支股票理論上還在觀察名單或已經是持倉，跟平常 `paper_trade_buy`/`sell` 的前置條件一樣，只是決策時機提前設定好而已
- 想取消還沒觸發的條件單，用 `paper_trade_cancel_condition`；`paper_trade_status` 看得到目前所有有效的條件單

**條件單不是設完就沒人管了**：Decision Packet 會直接附上該標的所有有效條件單。LLM 可以回傳 `cancel_condition` 或新的 `set_condition`；executor 只允許取消該資料包中真的存在的條件 ID，避免越權修改其他標的。

## 目前沒做的

- 只支援做多（買進→賣出），沒有放空
