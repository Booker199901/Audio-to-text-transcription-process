from __future__ import annotations

import numpy as np  # 建立不含個資的靜音測試音訊，實際觸發 CUDA 編碼器與 cuDNN。
from faster_whisper import WhisperModel  # 載入與正式轉錄相同的 faster-whisper GPU 後端。

SAMPLE_RATE = 16_000  # Whisper 使用 16 kHz 單聲道音訊。
TEST_SECONDS = 1  # 一秒靜音足以驗證 DLL 與 GPU 推論路徑，且執行時間短。


def main() -> None:
    """以 tiny 模型執行一次真實 CUDA 推論，避免只載入模型造成誤判。"""
    waveform = np.zeros(SAMPLE_RATE * TEST_SECONDS, dtype=np.float32)  # 產生固定長度的靜音波形。
    model = WhisperModel("tiny", device="cuda", compute_type="float16")  # 使用官方建議的 GPU FP16。
    segments, _ = model.transcribe(waveform, language="zh", beam_size=1)  # 建立延遲產生器。
    list(segments)  # 實際迭代結果，強制執行 CUDA encoder 與 decoder。
    print("GPU inference smoke test passed: cuda / float16")  # 回報完整推論成功。


if __name__ == "__main__":
    main()  # 只在直接執行此診斷腳本時啟動測試。
