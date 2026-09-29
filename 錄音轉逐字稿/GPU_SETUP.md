# Windows GPU 部署

本專案使用 `faster-whisper 1.2.1`、`ctranslate2 4.8.1`、CUDA 12 cuBLAS 與 cuDNN 9。GPU 套件安裝在專案 `.venv`，`run.ps1` 會自動把 NVIDIA DLL 目錄加入目前程序的 `PATH`，不會永久改寫系統環境變數。

## 前置條件

1. 電腦需有 NVIDIA CUDA GPU。
2. 安裝可正常執行 `nvidia-smi` 的 NVIDIA 顯示卡驅動。
3. 準備至少約 2 GB 磁碟空間給 GPU runtime；Whisper 模型另計。

## 安裝

先建立一般 CPU 環境，再安裝固定版本的 GPU 套件：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
.\.venv\Scripts\python.exe -m pip install -r .\requirements-gpu.txt
```

`requirements-gpu.txt` 會安裝：

- `nvidia-cublas-cu12==12.9.2.10`
- `nvidia-cuda-nvrtc-cu12==12.9.86`
- `nvidia-cudnn-cu12==9.25.0.15`

## 驗證

執行完整 GPU smoke test：

```powershell
powershell -ExecutionPolicy Bypass -File .\gpu_check.ps1 -LoadModel
```

此測試不只檢查 GPU 是否可見，也會使用 `tiny` 模型對一秒靜音執行實際 CUDA 推論，以驗證 cuBLAS、cuDNN 與 CTranslate2 均可載入。

## 執行轉錄

自動選擇 GPU 或 CPU：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1
```

強制使用 GPU FP16：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 --device cuda --compute-type float16
```

若目標電腦的 GPU 驅動或 DLL 不相容，使用可靠的 CPU 路徑：

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 --device cpu --compute-type int8
```

官方參考：

- [faster-whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu)
- [NVIDIA cuDNN Windows installation](https://docs.nvidia.com/deeplearning/cudnn/installation/latest/windows.html)
