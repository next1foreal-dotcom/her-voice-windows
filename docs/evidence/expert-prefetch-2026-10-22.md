# Drama 專家傳輸重疊：本機完整模型驗證（2026-10-22）

狀態：優化版本已在本機啟用，native 與 speech pool ready/idle，實際 worker 的兩次固定種子 PCM 複驗一致。這份結果只衡量本地 native HTTP 首個 PCM，不代表麥克風至實際揚聲器的完整延遲。

保留 Drama 3 Q4 專家／Q8 主幹、cache size 8、32-row 專家圖、完整 27.8727 秒 Samantha 參考、DeepSeek、精確 Prompt、Nuroum 和 UI。沒有新增付費測試呼叫、模型副本、模型選型或遠端部署。

## 保留的唯一優化

`patches/audio.cpp/cuda-expert-prefetch-overlap.patch` 在現有 cold-read、write-combined 和 transfer-timing patches 之後使用。

將下一組專家的原格式權重提前傳入獨立 CUDA stream 的 13.5 MiB 顯存 scratch，與本組專家計算重疊。scratch 預先按預測的實體位置排列，使相鄰位置可以合併複製；正式 stage 仍只執行原來一次 LRU 決策，逐個核對 ID／slot，等傳輸和 device copy 完成才提交快取 ID。原權重 tensor、圖尺寸、padding、路由、歸約及計算順序均保留。

失配回原有 host 路徑；失效及解構會先等待未完成傳輸。CPU、非 CUDA、非 pinned 路徑保留。沒有任何參考前綴結果或 KV 快取重用。私有環境變數 `HER_DRAMA_PREFETCH_CONTROL_FILE` 僅供同程序 A/B：檔案首字元 1 開、其餘關；一般啟動未設此變數，預取開啟。回退保留原 executable、source、runtime/build 收據。

## 相同程序內的測量

固定輸入 A：「你好，我在这里。今天我们可以慢慢聊，你想先说些什么？」；seed 0。B 在 A 後加「也可以说说你今天的心情。」。交叉次序和每次輸入／模式均保存在原始 JSONL。以下暖機分布排除載入後第一次請求、首次配置 scratch 和從 B 切回 A 的圖重建。

| 範圍 | 原路徑 | 優化路徑 |
| --- | ---: | ---: |
| A 暖機首 PCM 樣本數 | 5 | 5 |
| 首 PCM 中位數 | 1472.860 ms | 1210.036 ms |
| 最小／最大 | 1459.193／1514.187 ms | 1192.067／1232.104 ms |
| 完整生成中位數 | 6518.411 ms | 6216.899 ms |
| 首包後供給 RTF 中位數 | 0.8707 | 0.8671 |
| 暖機 A 35 ms lead 供給模擬出現缺料的回合 | 0/5 | 0/5 |

暖機 A 首 PCM 中位數減少 262.824 ms（17.84%），完整生成減少 301.512 ms（4.63%）。n=5 的樣本 P95 等於最大值，不能代表可靠母體 P95。桌面及 OS/GPU 背景負載未完全受控，結果限於本機這次交叉測試。

另外分開記錄：原程序載入後首個本地 A 請求 1546.813 ms；第一次啟用預取含 scratch 配置 1236.5 ms。兩者都不是電腦重啟冷啟動或首次麥克風對話。B 原路徑兩次 1498.7／1438.7 ms，候選 1375.2／1169.4 ms；切回 A 的圖重建候選 1220.9 ms。這些沒有混入上表暖機 A。

## 關鍵路徑和時間含義

| 同輪本地 TTS 工作 | 原路徑暖機中位數（5） | 候選暖機中位數（5） |
| --- | ---: | ---: |
| backbone 完整 prefill | 1373.208 ms | 1102.331 ms |
| 其中 stage 的同步等待及複製開銷 | 775.199 ms | 249.715 ms |
| 原 expert compute 調用區間 | 405.411 ms | 482.871 ms |
| attention 調用區間 | 65.146 ms | 66.513 ms |

這些是 CPU 區間／既有同步邊界的計時，stage 已包含在 prefill；異步傳輸與 compute 重疊，不能把它們相加，也不能把 stage 當純 PCIe DMA 時間。compute 區間增加，但總 prefill 與首 PCM 都下降。沒有 Nsight/WPR 的硬體時序，沒有把節省的全部時間聲稱為純 GPU 計算收益。

原關鍵路徑：本組傳輸完成 → 本組計算 → 下一組傳輸完成 → 下一組計算。
候選：本組輸入就緒 → 下一組傳輸與本組計算重疊 → 等待剩餘傳輸／合併 device copy → 下一組計算。沒有提前使用未驗證的文字或改變模型工作量。

## 正確性與限制

- 原路徑 8 次、候選完成 10 次（含取消後恢復），共 18 次完整合成；各自輸入的 PCM 與原版逐字節相同。A 512000 bytes／SHA256 `52e7389fc277bef7f7c823cdeb23b3ad2fa5a928f746cedc62b7de5c115129eb`；B 593920 bytes／`7f7b1134c77b0a59a6a04336b02c2abd9e9f43c3223239085aa82f90209fd593`。
- 原始到達序列已保留。18 次完成請求中的候選 10 次供給模擬均無缺料；原版載入後首次一輪有約 37 ms 缺料，原版暖機 5/5 無。這是 native 到達重播，不是實際揚聲器聽感。
- 額外一個請求在 prefill 開始後約 100 ms 關閉 HTTP；後續請求完整 PCM 一致。只證明取消後恢復，沒有宣稱取消響應變快或聲學取消驗收。
- 六種既有 storage 模式通過；每種新增 64 次路由／byte／slot 比較，以及失配、失效、未完成傳輸解構。forward patch check 通過。完整模型結果優先於小 fixture。
- 第一版逐塊 device copy 已保存比較（A 1432→1342 ms，中位約 6%）；正式選擇合併 copy 版本。實驗 expert-output memo 仍停用。
- 本次真實麥克風→聲學首音樣本 0；不聲稱 500 ms 達標。本地暖機 TTS 首音仍約 1.21 s，另有辨識、DeepSeek 可朗讀片段及實際輸出時間。模型冷讀取及 Python 啟動沒有本輪控制性改善證據。

## 原始證據與回退

私有 `work/expert-prefetch-20261022/`：`decision.json` 是人工複核後的保留決策；`v2/full-trials.jsonl` 包含逐塊到達；`v2/native-full.log`、`v2/native-trial-map.json`、`v2/component-summary.json` 為對應 native 時段；`v2/full-summary.json` 的 accepted=false 是測試控制器固定的「不自動啟用」標記，最後決策以 decision.json 與 activation-result.json 為準。

`build-coalesced.log`、四份 `*.before`／`*.candidate`、`server.before.exe`、`runtime.before.json`、`build.before.json` 保留構建與回退材料。`activate.ps1 -Rollback` 在驗證本應用 ready/idle、binary hash 及 worker 歸屬後恢復原 executable/source/receipts，再走本應用正常啟動；不涉及其他程序。測試及啟用時，僅暫時略過此 worker 的供應商 constructor warmup，避免付費測試；原始 sitecustomize 檔案每次在 finally 還原，生成方法未改。新供應商真實請求沒有本輪驗證。

沒有 commit、push 或部署。實際啟用完成狀態另見 activation-result.json，不能以測試 probe 代替。
本機啟用後兩次本地首 PCM：1587.335／1308.509 ms；與隔離交叉測試的分布分開。實際 ASR/VAD worker 已駐留，不代表本次有完整使用者對話。啟用 raw receipt 的 managed_start_s 包含後置合成驗證，不能當純冷啟動耗時。活躍 native SHA256 為上述候選997ee663…，最終狀態見 final-verification.json。
