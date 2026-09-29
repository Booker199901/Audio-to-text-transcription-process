# 本機繁體中文會議逐字稿工具

這個工具會在本機將長篇會議錄音轉成含時間碼的 Markdown。錄音不會上傳到 Gemini、Codex、Antigravity 或其他雲端服務，因此沒有線上檔案大小與請求逾時限制；首次安裝套件與首次下載模型需要網路，完成後可離線轉錄。

## 採用方案與精準度分析

本專案採用「Python 工作流程 + 本機語音 AI 模型」，核心是 `faster-whisper` 執行 Whisper `large-v3`，而不是在執行時呼叫 Codex 或 Antigravity。

| 方案 | 逐字精準度 | 長錄音穩定性 | 部署與隱私 | 結論 |
|---|---:|---:|---:|---|
| 純 Python 規則／傳統音訊處理 | 無法可靠辨識自然語音 | 高 | 簡單 | 不適用語音轉文字 |
| Python + Codex／Antigravity 代理直接處理音訊 | 不屬於穩定的專用 ASR 流程 | 受上傳、上下文與服務限制 | 依賴帳號與雲端 | 不作為執行架構 |
| Python + 本機 Whisper `large-v3` | 三者中最高且可重現 | 高，可分段與續跑 | 錄音留在本機 | **本專案採用** |
| 再用 LLM 潤飾 Whisper 結果 | 可讀性較高，但可能改字或補出不存在內容 | 需額外分段與 API | 內容可能上雲 | 逐字稿預設禁用 |

OpenAI 說明 `turbo` 是 `large-v3` 的加速版本，會有些微精準度下降；本需求明確以精準度優先，因此預設使用 `large-v3`。`faster-whisper` 以 CTranslate2 執行同系列模型，支援生成器、VAD 與 CPU/GPU，不需把整份錄音送到線上服務。

為了進一步提高繁中會議品質，程式會：

- 固定語言為中文 `zh`，減少短片段語言誤判。
- 提供臺灣繁體提示，再以離線 OpenCC `s2twp` 統一字形。
- 使用 beam size 5 與 Silero VAD，降低靜音片段的幻覺文字。
- 可在 `hotwords` 加入人名、公司名、產品名與專案名。
- 每 30 分鐘分段解碼，避免把數小時音訊一次載入記憶體。
- 每完成一段即寫入 `.partial.jsonl` 檢查點；中斷後再次執行會接續。
- 不用 LLM 改寫逐字內容；姓名、數字、金額與決議仍應人工回聽確認。

> 說話者分離（Speaker 1、Speaker 2）和逐字辨識是不同問題。WhisperX 可另加 diarization，但需要更多 GPU／模型設定，而且官方也註明多人重疊語音與 speaker diarization 並不完美。本版先以逐字內容精準、離線與容易部署為優先。

## Windows 快速安裝

需求：Windows 10/11、64 位元 Python 3.11–3.13、至少 8 GB RAM。CPU 可以執行但會較慢；若有相容的 NVIDIA GPU，程式會自動嘗試使用。

在 Codex 或 Antigravity 開啟本資料夾後，於 PowerShell 執行：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

安裝腳本會建立專案專用 `.venv`、安裝相依套件，並建立 `input` 與 `output`。不需要在 Codex 或 Antigravity 安裝額外外掛，也不需要 API key。

第一次使用會下載數 GB 的 `large-v3` 模型。若部署時想先完成下載與驗證：

```powershell
.\run.ps1 --download-model-only
```

模型下載完成後，會議內容的轉錄可在離線環境進行。

## 使用方式

1. 將 `.mp3`、`.m4a`、`.wav`、`.mp4`、`.flac` 等錄音放入 `input`。
2. 執行：

```powershell
.\run.ps1
```

3. 在 `output` 取得同名 `.md` 逐字稿。

也可以直接指定一或多個檔案：

```powershell
.\run.ps1 "D:\會議\專案會議.m4a"
.\run.ps1 "D:\會議\上午.mp3" "D:\會議\下午.mp3"
```

常用選項：

```powershell
# 已有 Markdown 時重新辨識；預設會略過以保護人工校對內容
.\run.ps1 --overwrite

# 強制使用 CPU，適合沒有 CUDA 或 GPU DLL 不相容的電腦
.\run.ps1 --device cpu --compute-type int8

# 臨時指定輸入與輸出資料夾
.\run.ps1 --input-dir "D:\錄音" --output-dir "D:\逐字稿"

# 忽略先前檢查點並從頭重跑
.\run.ps1 --no-resume --overwrite
```

完整參數：

```powershell
.\run.ps1 --help
```

## 提高公司內部詞彙精準度

編輯 `config.toml` 的 `hotwords`，加入實際會議常見詞彙：

```toml
hotwords = "王小明, 星河專案, Acme Cloud, Q3, ERP, PostgreSQL"
```

請用短詞並以逗號分隔。這比轉錄後交給通用 LLM 猜測專有名詞更可追溯；仍建議用一段有標準答案的 5–10 分鐘錄音，逐字比對後調整詞表。

## 可調整設定

一般使用者只需修改 [`config.toml`](config.toml)：

- `input_dir`、`output_dir`：輸入與輸出資料夾。
- `model`：預設 `large-v3`。電腦太慢可改 `turbo`，但這是速度優先取捨。
- `device`、`compute_type`：預設 `auto`；CPU 建議 `cpu` / `int8`。
- `chunk_minutes`：記憶體有限可降低到 10 或 15；只會影響分段邊界與續跑粒度。
- `vad_min_silence_ms`：若非常短的停頓被漏掉，可提高此值；若靜音幻覺多，可降低。
- `initial_prompt`、`hotwords`：放入會議語境與專有詞，不要寫成要求摘要或改寫的指令。

也可直接修改 [`transcribe.py`](transcribe.py) 頂端的 `INPUT_DIR` 與 `OUTPUT_DIR` 預設值；若 `config.toml` 有設定，設定檔會優先。

## 中斷續跑與檔案安全

- 轉錄中按 `Ctrl+C`，已完成的 30 分鐘分段會保存在 `output/*.md.partial.jsonl`。
- 用相同錄音、模型與設定再次執行，會略過已完成模型推論的分段。
- 錄音大小、修改時間或重要設定改變時，舊檢查點不會被誤用。
- 正式 Markdown 會先寫到暫存檔，再一次替換，避免留下半份結果。
- 已存在的 Markdown 預設不覆寫；只有明確加上 `--overwrite` 才會重做。

## 效能與疑難排解

### CPU 很慢

`large-v3` 以精準度為優先，CPU 轉錄數小時錄音可能需要很久。可選擇：

1. 保持 `large-v3`，在有 NVIDIA GPU 的電腦集中處理。
2. 將 `model` 改為 `turbo`，接受些微精準度損失以換取速度。
3. 將 `model` 改為 `medium`，適合資源較少的 CPU，但中文精準度通常也會降低。

### CUDA 載入失敗

目前 `faster-whisper`／CTranslate2 的新版 GPU 執行環境需要匹配的 CUDA 12 與 cuDNN 9。先用以下方式確認其他功能正常：

```powershell
.\run.ps1 --device cpu --compute-type int8
```

若要啟用 GPU，依 [faster-whisper 官方安裝說明](https://github.com/SYSTRAN/faster-whisper#gpu) 配置對應版本。不同部署電腦的 GPU 驅動與 DLL 不一致，因此本專案保留 CPU 作為最可靠的通用部署路徑。

### 輸出有錯字

- 先將人名與專案詞加入 `hotwords`。
- 確認原始錄音不是過度壓縮、音量太低或多人同時說話。
- 重要內容人工回聽；語音辨識模型無法保證 100% 正確。
- 不建議直接用 LLM 全文「修正」，因為它可能把口語改寫或虛構缺漏內容。

## 開發驗證

不下載模型也能執行基礎單元測試：

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

有實際錄音時，再執行小檔 smoke test：

```powershell
.\run.ps1 ".\input\短測試.wav" --device cpu --compute-type int8
```

## 主要技術來源

- [OpenAI Whisper 官方專案與模型比較](https://github.com/openai/whisper#available-models-and-languages)
- [SYSTRAN faster-whisper 官方專案](https://github.com/SYSTRAN/faster-whisper)
- [WhisperX 官方專案（時間對齊與說話者分離的延伸方案）](https://github.com/m-bain/whisperX)
- [OpenCC 官方專案（簡繁字形與臺灣詞彙轉換）](https://github.com/BYVoid/OpenCC)


## 開發者資訊

都市規劃研究者booker開發之程序，歡迎分享但請載名出處。
