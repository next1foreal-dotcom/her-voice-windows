# HER Voice：首段文字與 Drama 預填充的後續驗證

日期：2026-10-22。基點 main `88505c9ae6a00b9d1e9b34e1ae02870753d7ca97`，保留開始時既有未提交修改。範圍限於既有 Drama 3＋DeepSeek 路徑；完整 Samantha 參考、Prompt、模型及 Nuroum 選擇不變。

## 結論

**這輪沒有可採納的 500ms 加速結果。** 預填充診斷已把「狀態保存錯誤」與「拆分計算的數值差異」分開；首句提早輸出的候選缺少真實對話改善證據，已撤回。保留的正式程式變更只有首段計時，不改分句規則。

上一輪暖機本機 TTS 首 PCM 中位數 1563.7ms（n=6）及 backbone 預填充約 1429ms，仍是目前的量測依據。本輪沒有新增完整模型合成、DeepSeek 呼叫、服務重啟或冷啟動量測，也沒有新的麥克風到聲學輸出樣本（n=0）。不能把小型診斷耗時或模擬片段時間當成新的實際延遲分布。

同輪關鍵路徑、既有分布及缺失證據見 [原始延遲報告](latency-budget-2026-10-22.md) 和 [時間線](latency-critical-path-2026-10-22.svg)。該歷史回合約942ms才有可朗讀片段，約2551ms才有後端首音；兩者都不是實際揚聲器量測。

## 預填充：快照能重播，不等於拆分等價

在目前 native 原始碼上建立私有 GPU 診斷，不重建或替換服務執行檔。使用兩層、256維合成輸入，三種前綴長度7／129／513，每個後綴8步；分別驗證 F32 及與現用權重家族相同的 Q8 主幹／Q4 專家格式。這不是完整 Drama 模型或音色品質測試。

對每個固定輸入比較：

1. 完整批次預填充 F(P+S)，重跑一次驗證本身可精確重現。
2. 前綴預填充 I(P)，保存完整 KV 容量、GDN convolution／recurrent state、有效步數和 decoder owner，再逐步計算 S。
3. 重置並執行無關輸入後，在同一 decoder 恢復前綴快照，重播 S。

比較後綴 hidden、最終全部狀態，以及再推進一步的 hidden。每組完整基線重跑都精確一致；恢復後重播也都精確一致，但完整批次與拆分計算均不精確一致。

| 格式 | 前綴步數 | 後綴不同元素／2048 | 後綴最大絕對差 | 下一步不同元素／256 | 最終狀態精確相同 |
| --- | ---: | ---: | ---: | ---: | --- |
| F32 | 7 | 2042 | 0.000103951 | 256 | 否 |
| F32 | 129 | 2039 | 0.0000415146 | 253 | 否 |
| F32 | 513 | 2043 | 0.0000358820 | 245 | 否 |
| Q8／Q4 | 7 | 2048 | 0.000245333 | 256 | 否 |
| Q8／Q4 | 129 | 2045 | 0.000317290 | 254 | 否 |
| Q8／Q4 | 513 | 2044 | 0.000344768 | 253 | 否 |

Q8／Q4 的7步前綴本身也有1791個 hidden 元素不同；其他5組前綴 hidden 相同。狀態差異涉及 KV、convolution 及 recurrent buffers。這證明這條拆分執行路徑改變了數值結果，不能把差異直接歸咎於快照漏存；也尚未定位到單一 kernel。微小浮點差異本身不等於可聽品質退步，更不能證明所有前綴快取都不可行。

採納門檻仍未通過：先前完整模型前綴候選已產生不同且較短的 PCM，本輪小型快照驗證不足以取代完整文字、音色、tower、取樣狀態和音訊連續性驗證。因此不啟用快取，也不把先前約0.52秒的候選首 PCM 當成功結果。

初次診斷連結失敗來自舊 NMake archive 的 constructor ABI。強制重編當前 disk-store 實作後，兩組診斷完成；保留初次失敗日誌。臨時替換的測試原始碼已逐位元恢復；僅本機測試產物與建置 archive 更新，正式 server 未重建。私有測試執行檔目前是診斷版，不應誤當原有 fixture 的產物。

## 文字提早：候選撤回，補上可區分等待的計時

檢查的實際呼叫鏈為 `engine` 安裝 worker-local wrapper → 固定上游 LLM consumer → tokenizer → LLM output processor → TTS queue。現有中文句號及長中文首分句已可提早輸出。英文問號／驚嘆號後只有空白時，上游會保留最後一句等待下一個片段或結束。

私有候選對上述明確分隔提早送出。使用實際固定上游 consumer、相同合成 delta 的10組重播中，英文問句由預設1000ms提早至200ms，驚嘆句由800ms至150ms；這些都是人為指定的到達時間，不是 DeepSeek 實測收益。中文 ASCII 問號範例還使下一個 TTS 片段多保留一個前導空白，未通過逐段字串一致性。不是文字遺失，也沒有因此證明音訊變差，但沒有依據將它視為已驗證改善。

候選完整撤回。最後版本與本輪前版本的10組具名重播及125個切片位置，完整文字、逐段 TTS 字串和輸出時點均一致；沒有加速主張。上游原有引號正規化同樣保留，不以正規化後文字必須等於原始輸入作錯誤驗收。

保留的 `HER_FIRST_SEGMENT` 日誌每個語音 response 只記一次：

- response key、turn、revision。
- `first_text_observed_ns`：第一個非空白 TextDelta 送入 consumer 的 `perf_counter_ns`，不是原始網路封包抵達。
- `at_ns`：首個可朗讀 chunk 離開 consumer 的同一計時基準。
- `text_to_segment_ms`：以上兩點差，可識別觀測到首字後至首段的間隔；其中仍包含供應商後續文字等待，不能全算成本地 tokenizer。
- `consume_ms` 和原有 `provider_ttft_s` 保持各自定義，不把不同起點的 duration 相減。預取存在時，consumer 觀測時間也不等同上游請求開始。

不記文字、聲音或憑據；純文字回合不新增語音計時。固定上游 SHA guard、Markdown、取消、history 與 ContextVar 隔離保留。15項相關測試通過，涵蓋同一單調時鐘、只記一次、文字隱私、關閉及既有分句／取消路徑。

**計時尚未載入現有 worker。** 為遵守不新增付費呼叫，本輪沒有重啟會自動執行 DeepSeek warmup 的 managed worker；下次正常啟動才會載入。沒有把原服務健康當成新程式已上線。

## 達標所需的最小改變

約1.43秒是整個 backbone 預填充的量測，不是已單獨量出的「純參考音色成本」，不可假定全數可省。下一個有根據的 native 驗證必須保持正確的執行路徑，驗證參考邊界的全部狀態、位置、tower／取樣依賴及快取失效；不能只保存 KV 後改成逐 token 計算便宣稱等價。參考、Prompt、歷史、模型或計算設定變更時，快取需正確失效；取消與交錯回合不可共用失效狀態。

即使安全移除大量預填充，首個可朗讀文字仍必須早於500ms並給後續合成及裝置輸出留下時間。需要既有使用者對話的新分段證據確認可避免等待，或驗證在停語前可正確取消的提前工作。本輪未擴大實作推測回答、模型選型或硬體更換。

目前保留「文字就緒後完整預填充」的路徑沒有500ms證據；尚不能斷言固定模型在所有其他正確排程下都不可能達標。

## 原始證據、還原與交付狀態

私有證據在 `work/first-segment-prefix-20261022/`，由既有忽略規則排除：

| 檔案 | 用途 |
| --- | --- |
| `native-diagnostic-rebuilt.log`、`native-diagnostic-q8-q4.log` | 原始 GPU 診斷結果 |
| `native-comparison.json` | 上述6組逐欄摘錄 |
| `backbone-diagnostic.cpp`、`backbone_allocation_diagnostic.cpp`、`build-diagnostic.ps1` | 私有診斷與恢復流程；重跑會使用 GPU，不是純讀取 |
| `sentence-rejected.json`、`sentence-stream-rejected.py`、`sentence-tests-rejected.py` | 撤回候選及模擬對照 |
| `sentence-replay.json`、`sentence-retained-tests.log` | 最終文字／片段等價及15項測試 |
| `her_voice__sentence_stream.py.before`、`tests__test_sentence_stream.py.before` | 本輪前回退副本 |
| `verification.json`、`evidence-sha256.json` | 即時狀態、還原與證據雜湊 |

最終檢查：native PID57024、speech PID41812 均與先前相同，就緒且 pool idle。正式 native binary、完整參考、native config、Prompt config 的 SHA256 均與先前 provenance 相同；原 native 測試檔已還原。不重新掃讀整個24.6GB模型。未改麥克風設定，未新增聽感驗收。

本輪只有計時源碼、相關測試與報告；兩條加速候選均未啟用。沒有 commit、push 或部署。
