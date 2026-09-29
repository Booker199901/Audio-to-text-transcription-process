from __future__ import annotations  # 延後解析型別註記，讓程式可在較多 Python 版本穩定執行。

import argparse  # 解析命令列參數，方便 Codex、Antigravity 與一般使用者以相同方式執行。
import hashlib  # 產生設定指紋，避免用錯誤設定接續舊的轉錄進度。
import json  # 以 JSON Lines 儲存長錄音的中斷續跑檢查點。
import os  # 讀取 CPU 執行緒數等作業系統資訊。
import re  # 整理模型輸出的多餘空白。
import sys  # 回傳正確的程式結束碼並顯示執行環境資訊。
import tomllib  # 讀取 Python 3.11 內建支援的 TOML 設定檔。
from dataclasses import asdict, dataclass  # 以明確資料結構保存設定與逐字稿片段。
from datetime import datetime  # 在 Markdown 中記錄產生時間。
from pathlib import Path  # 安全處理 Windows 與跨平台檔案路徑。
from typing import Any, Iterable, Iterator, Sequence  # 補上可維護的函式型別註記。

INPUT_DIR = Path("input")  # 預設錄音放置資料夾；一般使用者可直接修改此行。
OUTPUT_DIR = Path("output")  # 預設逐字稿輸出資料夾；程式會自動建立。

CONFIG_PATH = Path("config.toml")  # 集中保存模型與轉錄參數，部署時只需交付同一份資料夾。
DEFAULT_MODEL = "large-v3"  # 採用多語言高精準模型，不以 turbo 的速度換取些微精準度損失。
SAMPLE_RATE = 16_000  # Whisper 使用 16 kHz 單聲道波形進行辨識。
SUPPORTED_EXTENSIONS = {  # 列出常見的會議錄音與含音訊影片格式。
    ".aac",
    ".flac",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
    ".wma",
}


@dataclass(frozen=True)
class Settings:
    """保存一次轉錄工作所需的完整設定。"""

    input_dir: Path = INPUT_DIR  # 指定要掃描的錄音資料夾。
    output_dir: Path = OUTPUT_DIR  # 指定 Markdown 逐字稿資料夾。
    model: str = DEFAULT_MODEL  # 模型可為名稱，也可改成本機模型資料夾。
    language: str = "zh"  # 鎖定中文，避免短片段被誤判成其他語言。
    device: str = "auto"  # 自動偵測可用的 NVIDIA GPU，否則安全回退 CPU。
    compute_type: str = "auto"  # GPU 使用 float16，CPU 使用 int8。
    beam_size: int = 5  # 使用多候選搜尋提高辨識穩定度。
    chunk_minutes: int = 30  # 分段解碼長錄音，控制記憶體用量並支援中斷續跑。
    vad_min_silence_ms: int = 700  # 過濾較長靜音，降低靜音處產生幻覺文字的機率。
    initial_prompt: str = (
        "以下是使用臺灣繁體中文的會議逐字稿，包含人名、公司名、產品名、數字與專有名詞。"
    )  # 提示模型優先輸出臺灣繁體中文書寫形式。
    hotwords: str = ""  # 可填入公司名、人名與專案名，提升專有詞命中機會。
    convert_to_traditional: bool = True  # 使用 OpenCC 將可能出現的簡體字統一成臺灣繁體。
    condition_on_previous_text: bool = True  # 保留片段上下文，改善會議長句的一致性。


@dataclass(frozen=True)
class TranscriptSegment:
    """保存一段已辨識文字與其絕對時間。"""

    start: float  # 片段在整份錄音中的開始秒數。
    end: float  # 片段在整份錄音中的結束秒數。
    text: str  # 清理並轉成繁體後的逐字內容。


@dataclass(frozen=True)
class ResumeState:
    """保存可以安全接續的轉錄狀態。"""

    completed_chunks: int  # 已完整寫入檢查點的音訊分段數量。
    segments: tuple[TranscriptSegment, ...]  # 已完成分段所包含的逐字稿片段。


def load_settings(config_path: Path) -> Settings:
    """讀取 TOML 設定；若檔案不存在則使用內建預設值。"""
    raw: dict[str, Any] = {}  # 先建立空設定，讓沒有設定檔時仍可直接執行。
    if config_path.exists():  # 僅在檔案存在時讀取，避免首次使用必須先建立設定。
        with config_path.open("rb") as config_file:  # TOML 解析器要求以二進位模式讀取。
            loaded = tomllib.load(config_file)  # 將 TOML 內容解析成 Python 字典。
        raw = loaded.get("transcription", {})  # 只取 transcription 區段，忽略其他未來擴充設定。
        if not isinstance(raw, dict):  # 防止使用者誤把設定區段寫成字串或陣列。
            raise ValueError("config.toml 的 [transcription] 必須是鍵值設定。")

    settings = Settings(  # 將設定值轉成固定型別，避免深層函式散落字典索引。
        input_dir=Path(raw.get("input_dir", INPUT_DIR)),
        output_dir=Path(raw.get("output_dir", OUTPUT_DIR)),
        model=str(raw.get("model", DEFAULT_MODEL)),
        language=str(raw.get("language", "zh")),
        device=str(raw.get("device", "auto")),
        compute_type=str(raw.get("compute_type", "auto")),
        beam_size=int(raw.get("beam_size", 5)),
        chunk_minutes=int(raw.get("chunk_minutes", 30)),
        vad_min_silence_ms=int(raw.get("vad_min_silence_ms", 700)),
        initial_prompt=str(raw.get("initial_prompt", Settings.initial_prompt)),
        hotwords=str(raw.get("hotwords", "")),
        convert_to_traditional=bool(raw.get("convert_to_traditional", True)),
        condition_on_previous_text=bool(raw.get("condition_on_previous_text", True)),
    )
    validate_settings(settings)  # 在載入模型前先回報容易修正的設定錯誤。
    return settings  # 回傳已驗證且可直接使用的設定物件。


def validate_settings(settings: Settings) -> None:
    """檢查會造成執行失敗或不合理資源使用的設定。"""
    if settings.beam_size < 1:  # Beam 至少要有一個候選。
        raise ValueError("beam_size 必須大於或等於 1。")
    if not 1 <= settings.chunk_minutes <= 180:  # 限制單段在 1 分鐘到 3 小時內。
        raise ValueError("chunk_minutes 必須介於 1 到 180。")
    if settings.vad_min_silence_ms < 0:  # 靜音毫秒數不能是負值。
        raise ValueError("vad_min_silence_ms 不可小於 0。")
    if settings.device not in {"auto", "cpu", "cuda"}:  # 僅接受 CTranslate2 支援的常用裝置。
        raise ValueError("device 僅可使用 auto、cpu 或 cuda。")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """建立簡潔命令列介面，並允許臨時覆寫常用設定。"""
    parser = argparse.ArgumentParser(description="將長篇繁體中文會議錄音轉成 Markdown 逐字稿。")
    parser.add_argument("paths", nargs="*", type=Path, help="指定一或多個錄音；省略時掃描 input_dir。")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="TOML 設定檔路徑。")
    parser.add_argument("--input-dir", type=Path, help="臨時覆寫錄音輸入資料夾。")
    parser.add_argument("--output-dir", type=Path, help="臨時覆寫 Markdown 輸出資料夾。")
    parser.add_argument("--model", help="模型名稱或已下載的本機模型路徑。")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), help="指定運算裝置。")
    parser.add_argument("--compute-type", help="例如 auto、int8、float16 或 int8_float16。")
    parser.add_argument("--overwrite", action="store_true", help="覆寫已存在的完整 Markdown。")
    parser.add_argument("--no-resume", action="store_true", help="忽略並重建既有的部分進度。")
    parser.add_argument("--download-model-only", action="store_true", help="只下載並驗證模型，不處理錄音。")
    return parser.parse_args(argv)  # 將解析結果交給主流程套用。


def apply_cli_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    """以命令列參數覆寫設定檔，同時保留其餘設定。"""
    values = asdict(settings)  # 先複製所有設定，避免逐欄重建時遺漏新欄位。
    for argument_name in ("input_dir", "output_dir", "model", "device", "compute_type"):
        argument_value = getattr(args, argument_name)  # 讀取對應命令列參數。
        if argument_value is not None:  # 只有使用者明確提供時才覆寫。
            values[argument_name] = argument_value
    updated = Settings(**values)  # 將合併後的內容重新建立成不可變設定。
    validate_settings(updated)  # 避免命令列帶入無效值。
    return updated  # 回傳本次實際採用的設定。


def discover_audio_files(explicit_paths: Sequence[Path], input_dir: Path) -> list[Path]:
    """依固定順序取得支援的錄音檔案。"""
    candidates = list(explicit_paths) if explicit_paths else list(input_dir.iterdir()) if input_dir.exists() else []
    files = [  # 過濾資料夾、捷徑與不支援的副檔名。
        path.resolve()
        for path in candidates
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return sorted(files, key=lambda path: str(path).casefold())  # 使用可重現順序方便批次核對。


def choose_runtime(device_setting: str, compute_setting: str) -> tuple[str, str]:
    """偵測 GPU，並為 GPU 或 CPU 選擇可靠的計算精度。"""
    device = device_setting  # 保留使用者明確指定的裝置。
    if device == "auto":  # 自動模式先檢查 CTranslate2 可見的 CUDA 裝置數量。
        try:
            import ctranslate2  # 延後載入，讓缺少套件時能顯示清楚安裝訊息。

            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except (ImportError, RuntimeError, OSError):  # DLL 或驅動不完整時使用 CPU，避免整批直接失敗。
            device = "cpu"
    compute_type = compute_setting  # 保留使用者明確指定的量化方式。
    if compute_type == "auto":  # 依裝置套用兼顧精準度與相容性的預設值。
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type  # 交由模型載入與 Markdown 中記錄。


def load_model(model_name: str, device: str, compute_type: str) -> Any:
    """載入本機 Whisper 模型；模型首次使用時會自動下載。"""
    try:
        from faster_whisper import WhisperModel  # 只有真正轉錄時才載入大型相依套件。
    except ImportError as error:  # 提供比 Python traceback 更容易處理的安裝指引。
        raise RuntimeError("缺少 faster-whisper，請先執行 setup.ps1 或 pip install -r requirements.txt。") from error

    cpu_threads = max(1, (os.cpu_count() or 2) - 1)  # CPU 模式保留一個執行緒給作業系統。
    return WhisperModel(  # 建立可在多個檔案與分段間重複使用的單一模型實例。
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        num_workers=1,
    )


def probe_audio_duration(file_path: Path) -> float:
    """讀取媒體容器的總秒數，供進度列與 Markdown 顯示。"""
    try:
        import av  # faster-whisper 已依賴 PyAV，不需要另外安裝 ffmpeg 執行檔。
    except ImportError as error:
        raise RuntimeError("缺少 PyAV；請重新安裝 requirements.txt。") from error

    with av.open(str(file_path), mode="r", metadata_errors="ignore") as container:  # 僅開啟標頭，不解碼全檔。
        if container.duration is not None:  # 容器時間以 AV_TIME_BASE 的微秒單位表示。
            return max(0.0, float(container.duration) / 1_000_000.0)
        audio_streams = list(container.streams.audio)  # 部分格式只在音訊串流提供時長。
        if not audio_streams:  # 沒有音訊軌就無法產生逐字稿。
            raise ValueError(f"找不到音訊軌：{file_path.name}")
        stream = audio_streams[0]  # 會議檔通常使用第一條音訊軌。
        if stream.duration is not None and stream.time_base is not None:  # 轉換串流時間基準為秒。
            return max(0.0, float(stream.duration * stream.time_base))
    return 0.0  # 少數直播容器沒有時長，仍允許以未知總長度執行。


def iter_audio_chunks(file_path: Path, chunk_seconds: int) -> Iterator[tuple[int, float, Any]]:
    """將媒體串流解碼成固定長度的 16 kHz 單聲道波形，避免一次載入長錄音。"""
    try:
        import av  # 使用 PyAV 直接解碼 faster-whisper 支援的媒體格式。
        import numpy as np  # 將多個音框高效率合併成模型需要的浮點波形。
    except ImportError as error:
        raise RuntimeError("缺少 PyAV 或 NumPy；請重新安裝 requirements.txt。") from error

    target_samples = chunk_seconds * SAMPLE_RATE  # 將每段分鐘數換算成取樣點數。
    pending_frames: list[Any] = []  # 暫存尚未湊滿一段的單聲道音框。
    pending_samples = 0  # 追蹤緩衝取樣數，避免每次都合併大型陣列。
    chunk_index = 0  # 以零起始編號建立可重現的檢查點。
    chunk_start = 0.0  # 記錄該分段在完整錄音中的絕對秒數。

    with av.open(str(file_path), mode="r", metadata_errors="ignore") as container:  # 逐框解碼，不讀入整份檔案。
        audio_streams = list(container.streams.audio)  # 找出容器內所有音訊軌。
        if not audio_streams:  # 明確拒絕只有畫面的影片。
            raise ValueError(f"找不到音訊軌：{file_path.name}")
        stream = audio_streams[0]  # 採用第一條音訊軌作為會議聲音來源。
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

        def consume_frame(resampled_frame: Any) -> Iterator[tuple[int, float, Any]]:
            """把重採樣音框放進緩衝，湊滿後輸出固定長度分段。"""
            nonlocal pending_frames, pending_samples, chunk_index, chunk_start
            frame_array = resampled_frame.to_ndarray().reshape(-1)  # 單聲道仍可能保留一個平面維度。
            if frame_array.size == 0:  # 忽略解碼器可能產生的空音框。
                return
            pending_frames.append(frame_array)  # 收集 int16 音訊，較 float32 節省一半暫存記憶體。
            pending_samples += int(frame_array.size)  # 更新尚未輸出的取樣數量。
            if pending_samples < target_samples:  # 未達分段長度時繼續從媒體串流讀取。
                return
            combined = np.concatenate(pending_frames)  # 只在湊滿一段時執行一次大型合併。
            while combined.size >= target_samples:  # 解碼音框若跨越邊界，依序輸出完整分段。
                chunk_int16 = combined[:target_samples]  # 切出固定長度的本次音訊。
                waveform = chunk_int16.astype(np.float32) / 32768.0  # 正規化成 Whisper 接受的 -1 到 1 波形。
                yield chunk_index, chunk_start, waveform  # 將波形交給轉錄器，不寫暫存 WAV。
                chunk_index += 1  # 下一段使用新的檢查點編號。
                chunk_start += target_samples / SAMPLE_RATE  # 精確累加分段起點。
                combined = combined[target_samples:]  # 保留跨界剩餘音訊給下一段。
            pending_frames = [combined] if combined.size else []  # 保存不足一段的尾端內容。
            pending_samples = int(combined.size)  # 同步更新緩衝取樣數。

        for decoded_frame in container.decode(stream):  # 串流解碼第一條音訊軌。
            for converted_frame in resampler.resample(decoded_frame):  # 統一採樣率、聲道與取樣格式。
                yield from consume_frame(converted_frame)  # 可能湊滿並輸出一或多段波形。
        for converted_frame in resampler.resample(None):  # 排出重採樣器內部最後殘留的音訊。
            yield from consume_frame(converted_frame)

    if pending_samples > 0:  # 檔案尾端通常不足完整 chunk，仍需轉錄。
        tail = np.concatenate(pending_frames)  # 合併最後少量音框。
        waveform = tail.astype(np.float32) / 32768.0  # 轉成模型使用的浮點波形。
        yield chunk_index, chunk_start, waveform  # 輸出最後一段並結束產生器。


def build_converter(enabled: bool) -> Any:
    """建立簡體轉臺灣繁體轉換器；停用時回傳原文函式。"""
    if not enabled:  # 設定停用時完全保留模型字形。
        return lambda text: text
    try:
        from opencc import OpenCC  # 使用官方離線 OpenCC，不把會議內容送往外部服務。
    except ImportError as error:
        raise RuntimeError("缺少 OpenCC，請先執行 setup.ps1 或 pip install -r requirements.txt。") from error
    converter = OpenCC("s2twp.json")  # 簡體轉臺灣繁體，並套用常見臺灣用語與片語。
    return converter.convert  # 只暴露文字轉換函式，方便測試替換。


def clean_text(text: str, convert: Any) -> str:
    """移除不必要空白並統一繁體字形，不進行可能改意的 AI 潤飾。"""
    compact = re.sub(r"\s+", " ", text).strip()  # 將換行與連續空白整理成單一半形空白。
    return convert(compact).strip()  # 套用確定性的字形轉換並清掉邊界空白。


def settings_fingerprint(file_path: Path, settings: Settings) -> str:
    """建立來源檔與辨識參數指紋，判斷檢查點是否仍可安全使用。"""
    stat = file_path.stat()  # 取得大小與修改時間，偵測同名錄音是否已被替換。
    relevant = {  # 僅納入會影響逐字結果或分段邊界的設定。
        "source": str(file_path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "model": settings.model,
        "language": settings.language,
        "beam_size": settings.beam_size,
        "chunk_minutes": settings.chunk_minutes,
        "vad_min_silence_ms": settings.vad_min_silence_ms,
        "initial_prompt": settings.initial_prompt,
        "hotwords": settings.hotwords,
        "convert_to_traditional": settings.convert_to_traditional,
        "condition_on_previous_text": settings.condition_on_previous_text,
    }
    serialized = json.dumps(relevant, ensure_ascii=False, sort_keys=True).encode("utf-8")  # 穩定序列化設定。
    return hashlib.sha256(serialized).hexdigest()  # 以固定長度雜湊值寫入檢查點。


def load_resume_state(checkpoint_path: Path, fingerprint: str) -> ResumeState:
    """只載入最後一個完整分段以前的檢查點內容。"""
    if not checkpoint_path.exists():  # 沒有檢查點代表從頭開始。
        return ResumeState(0, ())
    records: list[dict[str, Any]] = []  # 暫存有效 JSON Lines 記錄。
    try:
        with checkpoint_path.open("r", encoding="utf-8") as checkpoint_file:  # 逐行讀取避免單行損壞整檔。
            for line in checkpoint_file:
                if line.strip():  # 跳過人工作業可能加入的空白行。
                    records.append(json.loads(line))
    except (OSError, json.JSONDecodeError, UnicodeError):  # 當機造成尾行不完整時改從頭執行最安全。
        return ResumeState(0, ())
    if not records or records[0].get("type") != "meta" or records[0].get("fingerprint") != fingerprint:
        return ResumeState(0, ())  # 來源或設定已變更時不可沿用舊文字。

    completed_chunks = 0  # 要求完成標記由第零段連續出現。
    segments_by_chunk: dict[int, list[TranscriptSegment]] = {}  # 先依分段保存，排除未完成尾端。
    for record in records[1:]:
        record_type = record.get("type")  # 判斷是文字片段或分段完成標記。
        chunk_index = int(record.get("chunk", -1))  # 無效記錄使用 -1 並略過。
        if record_type == "segment" and chunk_index >= 0:
            segments_by_chunk.setdefault(chunk_index, []).append(  # 還原已序列化的逐字稿片段。
                TranscriptSegment(float(record["start"]), float(record["end"]), str(record["text"]))
            )
        elif record_type == "chunk_complete" and chunk_index == completed_chunks:
            completed_chunks += 1  # 只有連續完成的分段可以接續。

    safe_segments: list[TranscriptSegment] = []  # 依完成分段順序重新組合結果。
    for chunk_index in range(completed_chunks):
        safe_segments.extend(segments_by_chunk.get(chunk_index, []))
    return ResumeState(completed_chunks, tuple(safe_segments))  # 捨棄沒有完成標記的尾端記錄。


def initialize_checkpoint(checkpoint_path: Path, fingerprint: str) -> None:
    """建立新的 UTF-8 檢查點並寫入來源設定指紋。"""
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)  # 確保輸出資料夾已存在。
    with checkpoint_path.open("w", encoding="utf-8", newline="\n") as checkpoint_file:
        checkpoint_file.write(json.dumps({"type": "meta", "fingerprint": fingerprint}) + "\n")


def append_checkpoint_records(checkpoint_path: Path, records: Iterable[dict[str, Any]]) -> None:
    """立即追加並同步檢查點，降低程式中斷時遺失的工作量。"""
    with checkpoint_path.open("a", encoding="utf-8", newline="\n") as checkpoint_file:
        for record in records:  # 每筆記錄獨立一行，便於排除未完整寫入的尾端。
            checkpoint_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        checkpoint_file.flush()  # 將 Python 緩衝內容交給作業系統。
        os.fsync(checkpoint_file.fileno())  # 要求作業系統將重要進度同步到磁碟。


def format_timestamp(seconds: float) -> str:
    """將秒數格式化成適合 Markdown 閱讀的 HH:MM:SS。"""
    rounded = max(0, int(seconds))  # 避免浮點誤差或異常模型時間造成負數。
    hours, remainder = divmod(rounded, 3600)  # 拆分小時與其餘秒數。
    minutes, secs = divmod(remainder, 60)  # 拆分分鐘與秒數。
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"  # 固定寬度便於搜尋與排序。


def render_markdown(
    source_path: Path,
    duration: float,
    segments: Sequence[TranscriptSegment],
    settings: Settings,
    device: str,
    compute_type: str,
) -> str:
    """將逐字內容輸出成保留時間碼的 Markdown 文件。"""
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")  # 記錄本機時區產生時間。
    lines = [  # 先建立固定文件標頭與可追溯的轉錄資訊。
        f"# {source_path.stem} 逐字稿",
        "",
        f"- 來源檔案：`{source_path.name}`",
        f"- 錄音長度：`{format_timestamp(duration)}`",
        f"- 產生時間：`{generated_at}`",
        f"- 辨識模型：`{settings.model}`",
        f"- 執行裝置：`{device} / {compute_type}`",
        f"- 語言：`{settings.language}`（臺灣繁體中文正規化）",
        "",
        "> 本文件由語音辨識模型自動產生。重要姓名、數字、金額與決議請回聽原始錄音確認。",
        "",
        "## 逐字稿",
        "",
    ]
    if not segments:  # 全檔靜音或沒有可辨識語音時留下明確說明。
        lines.append("_未偵測到可辨識的語音內容。_")
    else:
        for segment in segments:  # 每個辨識片段保留可點查的開始時間。
            lines.append(f"- **[{format_timestamp(segment.start)}]** {segment.text}")
    lines.append("")  # 以換行結尾，符合一般 Markdown 與版本控制慣例。
    return "\n".join(lines)  # 組合成單一 UTF-8 文字內容。


def write_text_atomic(output_path: Path, content: str) -> None:
    """先寫入暫存檔再原子替換，避免中斷留下半份 Markdown。"""
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")  # 暫存檔位於相同磁碟分割區。
    temporary_path.write_text(content, encoding="utf-8", newline="\n")  # 使用跨平台一致的 UTF-8 與換行。
    temporary_path.replace(output_path)  # 完整寫入後才讓正式檔案可見。


def build_output_paths(files: Sequence[Path], output_dir: Path) -> dict[Path, Path]:
    """建立不互相覆蓋的輸出名稱；同名不同格式會加上原副檔名。"""
    stem_counts: dict[str, int] = {}  # 統計不分大小寫的檔名主體出現次數。
    for file_path in files:
        key = file_path.stem.casefold()  # Windows 檔名通常不分大小寫。
        stem_counts[key] = stem_counts.get(key, 0) + 1
    outputs: dict[Path, Path] = {}  # 保存每個來源對應的 Markdown 路徑。
    for file_path in files:
        duplicate = stem_counts[file_path.stem.casefold()] > 1  # 判斷是否需要加入來源副檔名。
        suffix_label = file_path.suffix.lower().lstrip(".")  # 將 .mp3 轉成可讀的 mp3。
        name = f"{file_path.stem}_{suffix_label}.md" if duplicate else f"{file_path.stem}.md"
        outputs[file_path] = output_dir / name  # 所有結果集中到指定輸出資料夾。
    return outputs  # 供批次主流程與檢查點命名共用。


def transcribe_file(
    model: Any,
    file_path: Path,
    output_path: Path,
    settings: Settings,
    device: str,
    compute_type: str,
    allow_resume: bool,
) -> int:
    """串流轉錄單一長音訊，回傳產生的文字片段數。"""
    try:
        from tqdm import tqdm  # 長時間推論以秒為單位顯示進度。
    except ImportError as error:
        raise RuntimeError("缺少 tqdm；請重新安裝 requirements.txt。") from error

    duration = probe_audio_duration(file_path)  # 先取得總長度供使用者估算進度。
    converter = build_converter(settings.convert_to_traditional)  # 每個檔案共用同一繁中轉換器。
    fingerprint = settings_fingerprint(file_path, settings)  # 驗證檢查點與來源及參數一致。
    checkpoint_path = output_path.with_suffix(output_path.suffix + ".partial.jsonl")  # 將暫存進度放在輸出旁。
    resume = load_resume_state(checkpoint_path, fingerprint) if allow_resume else ResumeState(0, ())
    if resume.completed_chunks == 0:  # 無安全進度時重建檢查點，清掉舊設定或未完成尾端。
        initialize_checkpoint(checkpoint_path, fingerprint)
    segments: list[TranscriptSegment] = list(resume.segments)  # 從已完成文字繼續追加。
    chunk_seconds = settings.chunk_minutes * 60  # 將使用者友善的分鐘設定轉成秒。
    resumed_seconds = min(duration, resume.completed_chunks * chunk_seconds) if duration else 0.0

    with tqdm(  # 顯示整份檔案音訊進度，而不是無法預估的假百分比。
        total=duration or None,
        initial=resumed_seconds,
        desc=file_path.name,
        unit="sec",
        unit_scale=False,
        dynamic_ncols=True,
    ) as progress:
        last_progress = resumed_seconds  # 追蹤已呈現進度，避免分段切換時倒退。
        for chunk_index, chunk_start, waveform in iter_audio_chunks(file_path, chunk_seconds):
            if chunk_index < resume.completed_chunks:  # 已完成分段只需解碼略過，不重跑模型。
                continue
            raw_segments, _ = model.transcribe(  # 對當前有限記憶體波形執行本機辨識。
                waveform,
                language=settings.language,
                task="transcribe",
                beam_size=settings.beam_size,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": settings.vad_min_silence_ms},
                initial_prompt=settings.initial_prompt,
                hotwords=settings.hotwords or None,
                condition_on_previous_text=settings.condition_on_previous_text,
                word_timestamps=False,
            )
            checkpoint_records: list[dict[str, Any]] = []  # 先收集本段結果，再一次同步寫入磁碟。
            for raw_segment in raw_segments:  # 迭代產生器時模型才真正開始推論。
                text = clean_text(raw_segment.text, converter)  # 確定性清理，不用 LLM 改寫原意。
                absolute_end = chunk_start + float(raw_segment.end)  # 將分段相對時間轉成整檔時間。
                if text:  # 忽略只有空白的模型輸出。
                    segment = TranscriptSegment(
                        start=chunk_start + float(raw_segment.start),
                        end=absolute_end,
                        text=text,
                    )
                    segments.append(segment)  # 追加到最後 Markdown 的片段順序。
                    checkpoint_records.append(  # 保存可在重啟後還原的資料。
                        {"type": "segment", "chunk": chunk_index, **asdict(segment)}
                    )
                new_progress = min(duration, absolute_end) if duration else absolute_end  # 約束容器時長誤差。
                if new_progress > last_progress:  # tqdm 只接受向前增加的秒數。
                    progress.update(new_progress - last_progress)
                    last_progress = new_progress
            checkpoint_records.append({"type": "chunk_complete", "chunk": chunk_index})  # 最後才標記整段安全。
            append_checkpoint_records(checkpoint_path, checkpoint_records)  # 模型完成一段就持久化。
            chunk_end = chunk_start + len(waveform) / SAMPLE_RATE  # 即使全段靜音仍需更新進度。
            new_progress = min(duration, chunk_end) if duration else chunk_end
            if new_progress > last_progress:
                progress.update(new_progress - last_progress)
                last_progress = new_progress

    markdown = render_markdown(file_path, duration, segments, settings, device, compute_type)  # 組合最終文件。
    write_text_atomic(output_path, markdown)  # 確保使用者只會看到完整 Markdown。
    checkpoint_path.unlink(missing_ok=True)  # 正式輸出完成後移除可重建的暫存進度。
    return len(segments)  # 供批次摘要顯示轉錄片段數。


def main(argv: Sequence[str] | None = None) -> int:
    """執行批次會議錄音轉錄，並回傳適合自動化部署判斷的結束碼。"""
    args = parse_args(argv)  # 先解析命令列，才能得知要使用哪份設定檔。
    try:
        settings = apply_cli_overrides(load_settings(args.config), args)  # 合併設定檔與臨時參數。
    except (OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"設定錯誤：{error}", file=sys.stderr)  # 以簡短訊息提示設定檔位置或格式問題。
        return 2

    settings.output_dir.mkdir(parents=True, exist_ok=True)  # 依技能規範自動建立輸出資料夾。
    files: list[Path] = []  # 純下載模型模式不需要掃描錄音，因此先建立空清單。
    output_paths: dict[Path, Path] = {}  # 模型下載模式也不需要建立輸出檔名。
    if not args.download_model_only:  # 一般轉錄應在耗時載入模型前先驗證確實有工作可做。
        files = discover_audio_files(args.paths, settings.input_dir)  # 取得明確指定或資料夾內的錄音。
        if not files:  # 沒有錄音時不下載數 GB 模型，直接提示正確放置位置。
            print(f"找不到支援的錄音檔。請將檔案放入：{settings.input_dir.resolve()}", file=sys.stderr)
            return 2
        output_paths = build_output_paths(files, settings.output_dir)  # 避免同名不同格式互相覆寫。
        if not args.overwrite and all(output_paths[file_path].exists() for file_path in files):
            print("所有錄音皆已有 Markdown 輸出，本次不需載入模型。")  # 保護人工校對內容並節省啟動時間。
            print(f"輸出：{settings.output_dir.resolve()}")
            return 0

    device, compute_type = choose_runtime(settings.device, settings.compute_type)  # 決定實際運算後端。
    print(f"載入模型：{settings.model}（{device} / {compute_type}）")  # 模型下載可能耗時，先告知目前動作。
    try:
        model = load_model(settings.model, device, compute_type)  # 批次只載入模型一次。
    except Exception as error:  # 模型下載、CUDA DLL 與磁碟錯誤需要可讀提示。
        print(f"模型載入失敗：{error}", file=sys.stderr)
        if device == "cuda":  # CUDA 問題可用明確命令切換 CPU 排除。
            print("可先改用：python transcribe.py --device cpu --compute-type int8", file=sys.stderr)
        return 3
    if args.download_model_only:  # 部署端可先下載模型，再於離線環境處理敏感錄音。
        print("模型已下載並成功載入。")
        return 0

    processed = 0  # 統計成功輸出的檔案數。
    skipped = 0  # 統計因結果已存在而略過的檔案數。
    failed: list[tuple[Path, str]] = []  # 保存逐檔錯誤，不讓單一壞檔中止整批。
    for index, file_path in enumerate(files, start=1):  # 以固定順序逐檔處理並顯示批次位置。
        output_path = output_paths[file_path]
        print(f"\n[{index}/{len(files)}] {file_path.name} → {output_path.name}")
        if output_path.exists() and not args.overwrite:  # 預設保護已人工校對的逐字稿。
            print("略過：輸出已存在；如需重做請加 --overwrite。")
            skipped += 1
            continue
        try:
            segment_count = transcribe_file(  # 將單檔風險隔離並保存中斷進度。
                model=model,
                file_path=file_path,
                output_path=output_path,
                settings=settings,
                device=device,
                compute_type=compute_type,
                allow_resume=not args.no_resume,
            )
            print(f"完成：{output_path}（{segment_count} 個片段）")
            processed += 1
        except KeyboardInterrupt:  # 使用者中止時保留檢查點，下次可直接接續。
            print("\n已中止；目前完整分段已保存，下次執行會自動接續。", file=sys.stderr)
            return 130
        except Exception as error:  # 記錄問題並繼續下一個錄音，適合無人值守批次。
            failed.append((file_path, str(error)))
            print(f"失敗：{error}", file=sys.stderr)

    print("\n批次摘要")  # 讓一般使用者不必翻查完整終端輸出。
    print(f"  成功：{processed}，略過：{skipped}，失敗：{len(failed)}")
    print(f"  輸出：{settings.output_dir.resolve()}")
    for file_path, message in failed:  # 集中列出需要回頭處理的檔案。
        print(f"  - {file_path.name}: {message}", file=sys.stderr)
    return 1 if failed else 0  # 自動化環境可依結束碼判斷是否需要處理錯誤。


if __name__ == "__main__":
    raise SystemExit(main())  # 將主流程結果轉成正確的作業系統結束碼。
