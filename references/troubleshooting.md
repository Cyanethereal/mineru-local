# MinerU 鏈湴 skill 鏁呴殰鎺掓煡

## 1. WSL 涓嶅彲鐢ㄦ垨 conda env 鎵句笉鍒?
鐥囩姸锛歚doctor` 杈撳嚭 `wsl.available=false`锛宍wsl.executable` 鎸囧悜 `/home/.../bin/mineru` 浣?`MISSING`銆?
鎺掓煡锛?
```powershell
wsl -d Ubuntu-24.04 -- bash -lc "test -x /home/azuresky/miniconda3/envs/mineru/bin/mineru && echo OK"
```

淇锛?
- 閲嶆柊鍚姩 WSL锛歚wsl --shutdown` 鐒跺悗 `wsl -d Ubuntu-24.04` 閲嶆柊杩涘叆
- 妫€鏌?`miniconda3` 瀹夎璺緞鏄惁涓€鑷?- 閲嶆柊婵€娲?conda env锛?
```bash
source /home/azuresky/miniconda3/etc/profile.d/conda.sh
conda activate mineru
which mineru
```

## 2. Windows conda 鐜娌℃湁 vllm

鐥囩姸锛歚doctor` 杈撳嚭 `windows.vllm=false`锛宍backends_usable=["pipeline"]`锛屾兂瑕佽窇 hybrid-engine 浣嗘姤閿欍€?
娉ㄦ剰锛氭湰 skill **涓?*浼氶噸鏂板畨瑁?vllm 鍒?Windows conda 鐜銆備袱鏉¤В鍐虫柟妗堬細

- 鏀圭敤 WSL conda锛堟帹鑽愶紝宸茶嚜甯?vllm 0.20.2锛?- 鍦?Windows conda 閲屽崟鐙畨瑁咃細

```powershell
conda activate mineru
pip install vllm
```

娉ㄦ剰 vllm 鍦?Windows + RTX 5060 涓婂 CUDA 鐗堟湰鏁忔劅锛屽畨瑁呭墠璇烽槄璇?[MinerU 鎵╁睍妯″潡瀹夎鏂囨。](https://github.com/opendatalab/MinerU/blob/master/docs/zh/quick_start/extension_modules.md)銆?
## 3. hybrid-engine 鍦?Windows conda 鎶?CUDA kernel 閿?
鐥囩姸锛堝凡鍦ㄦ湰鏈哄鐜帮級锛?
```
[TM][FATAL] kernels\gemm\tuner\measurer.cu(83):
Check failed: status == cudaSuccess no kernel image is available for execution on the device
```

鍘熷洜锛氬綋鍓?mineru 3.4.4 鍦?Windows 涓嬮粯璁ょ粰 hybrid-engine 閫?`lmdeploy + turbomind`锛屼絾鏈満 RTX 5060 缂哄皯瀵瑰簲 kernel銆?
瑙ｅ喅鏂规锛?
- 鏀圭敤 `--env windows --backend pipeline`锛堝凡楠岃瘉鍙敤锛?- 鏀圭敤 `--env wsl --backend hybrid-engine`锛圵SL 涓?vllm 璺緞绋冲畾锛?- 涓嶈璁?skill 闅愬紡鍒囨崲鍒颁簯绔?API

## 4. 妯″瀷涓嬭浇澶辫触鎴栧崱浣?
鐥囩姸锛氶娆¤繍琛?vllm/lmdeploy 鏃堕暱鏃堕棿鏃犲搷搴斻€?
- 鍒囧埌 modelscope锛?
  ```bash
  # WSL锛?  wsl -d Ubuntu-24.04 -- bash -lc "export MINERU_MODEL_SOURCE=modelscope && /home/azuresky/miniconda3/envs/mineru/bin/mineru -p test.pdf -o /tmp/out -b hybrid-engine"
  ```

- 妯″瀷缂撳瓨浣嶇疆锛?  - HuggingFace锛歚C:\Users\azuresky\.cache\huggingface` 鎴?WSL 涓?`~/.cache/huggingface`
  - ModelScope锛歚C:\Users\azuresky\.cache\modelscope` 鎴?WSL 涓?`~/.cache/modelscope`

- 缃戠粶闂锛歁inerU 榛樿璧?HuggingFace锛涘鍦ㄥ鍐呭彲鐢?modelscope锛屾垨鍦?HF_ENDPOINT 閰嶇疆闀滃儚銆?
## 5. WSL 涓?GPU 涓嶅彲瑙?
鐥囩姸锛歐SL 涓?`nvidia-smi` 鎵句笉鍒?GPU銆?
淇锛?
- 纭 Windows 宸插畨瑁?NVIDIA 椹卞姩锛?= 510锛?- 鍗囩骇 WSL锛歚wsl --update`
- 閲嶅惎 WSL锛歚wsl --shutdown`

## 6. 璺緞杞崲闂

鐥囩姸锛氳В鏋愭椂鎶ラ敊 `input not found` 鎴栬緭鍑虹洰褰曞啓鍒颁簡閿欒浣嶇疆銆?
- 鐢ㄦ埛蹇呴』浼?Windows 缁濆璺緞锛坄C:\...`锛?- 鍖呰鍣ㄥ唴閮ㄦ妸 Windows 璺緞杞负 `/mnt/c/...` 浼犵粰 WSL
- 鎶ュ憡閲岀殑 `host_path` 宸茬粡鏄?Windows 璺緞锛屽彲鐩存帴鎵撳紑
- 濡傛灉鐢?UNC 鎴栫鍙烽摼鎺ワ紝鍖呰鍣ㄤ笉浼氬皾璇曡В鏋愶紱璇锋敼鐢ㄦ櫘閫氱鐩樿矾寰?
## 7. 杩滅 URL 琚嫆

鐥囩姸锛歚--api-url https://api.example.com` 鎶?`api-url must point to a loopback address`銆?
鏈?skill **鏄庣‘**绂佹杩滅 URL锛屽寘鎷細

- 瀹樻柟 `mineru.net` 浠讳綍绔偣
- 浠讳綍 `0.0.0.0`銆乣192.168.x.x`銆乣10.x.x.x` 绛?- 浠讳綍鑳借В鏋愬埌鍏綉 IP 鐨勫煙鍚?
濡傞渶 vLLM 澶栭儴鏈嶅姟锛岃鎶婃湇鍔＄粦瀹氬埌 `127.0.0.1` 鍚庣敱 skill 閫氳繃鏈湴鍦板潃璁块棶锛涚洰鍓?skill 涓嶅唴缃鑳藉姏锛岀暀寰呮湭鏉ョ増鏈€?
## 8. OCR 鍚?VLM 浜屾鏍￠獙

鏈?skill **涓?*鍋?VLM 浜屾鏍￠獙銆傚闇€锛?
- 鍙栧嚭 `artifacts.images` 閲屾瘡寮犲浘鐗?- 鐢?vLLM锛坄http://127.0.0.1:30000`锛夋垨 Ollama 瑙嗚妯″瀷鍗曠嫭鍙戣捣璇锋眰
- 鑷鍐冲畾鏄惁鎶婇噸鍐欑粨鏋滃啓鍥?Markdown

鍚庣画 skill 鐗堟湰濡傞渶瑕侊紝鍙姞 `verify` 瀛愬懡浠ゃ€?
## 9. 閫€鍑虹爜璇存槑

| Code | 鍚箟 | 澶勭悊寤鸿 |
|------|------|----------|
| 0 | 鎴愬姛 | 鏃?|
| 1 | parse-dir 閮ㄥ垎澶辫触 | 鐪?`failures` 瀛楁 |
| 2 | 鍙傛暟閿欒 | 妫€鏌?doctor 杈撳嚭涓庡弬鏁拌〃 |
| 3 | 鐜涓嶅彲鐢?| 淇 conda 瀹夎 |
| 4 | 鍚庣鍦ㄨ鐜涓嶅彲鐢?| 鏀?backend 鎴?env |
| 5 | 鏃?Markdown 浜х墿 | 妫€鏌ユā鍨嬨€佽緭鍏ユ枃浠躲€佹槸鍚﹂渶瑕?OCR |
| 124/127 | 鍛戒护鏈壘鍒?瓒呮椂 | 妫€鏌?WSL/PATH |
| 鍏朵粬 | 閫忎紶 `mineru` 閫€鍑虹爜 | 鐪?stderr_tail |


### 我请求 hybrid-engine 但被降级到 pipeline

报告里会有 `fallback.used: true` + `fallback.reason` + `fallback.smoke_attempts` 数组。

诊断步骤：
1. 看 `smoke_attempts[0]` (WSL) 和 `smoke_attempts[1]` (Windows) 的 `ok` 字段
2. 如果 WSL smoke 失败 → WSL 内装 vLLM：
   ```bash
   wsl -d Ubuntu-24.04
   pip install "mineru[core,vllm]"
   ```
3. 如果 Windows smoke 失败 → Windows 重装 mineru 或装 CUDA
4. 两个都失败 → 看 stderr，按 MinerU 官方 FAQ 排查（references 里有官方文档锚点）

**v1.2.0.1 之前** 的版本会**静默**降级，不会报告 fallback 字段。升级到 v1.2.0.1 就能看到诊断信息。
