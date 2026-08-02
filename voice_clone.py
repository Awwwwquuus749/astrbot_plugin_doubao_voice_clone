"""
豆包声音复刻模块 - 上传音频训练自定义音色 & 查询训练状态
使用火山引擎豆包语音 API v3
"""

import base64
import json
import uuid
from typing import Optional
from pathlib import Path

import httpx
from astrbot.api import logger

# 默认 API 基础地址（与 tts_client.py 保持一致）
DEFAULT_BASE_URL = "https://openspeech.bytedance.com"

# 语种映射（字符串代码 → 数字编码，与官方 API 一致）
LANGUAGE_MAP = {
    "cn": 0,
    "en": 1,
    "ja": 2,
    "es": 3,
    "id": 4,
    "pt": 5,
    "de": 6,
    "fr": 7,
    "ko": 8,
    "it": 9,
    "th": 10,
    "vi": 11,
    "ru": 12,
    "fil": 13,
    "ms": 14,
    "ar": 15,
    "mx": 16,
    "pt-br": 17,
    "pl": 19,
    "tr": 20,
    "sv": 21,
}

# 数字编码 → 字符串代码（反向映射）
LANGUAGE_CODE_MAP = {v: k for k, v in LANGUAGE_MAP.items()}

# 数字编码 → 中文语种名（用于状态显示）
LANGUAGE_NAME_MAP = {
    0: "中文",
    1: "英文",
    2: "日语",
    3: "西班牙语",
    4: "印尼语",
    5: "葡萄牙语",
    6: "德语",
    7: "法语",
    8: "韩语",
    9: "意大利语",
    10: "泰语",
    11: "越南语",
    12: "俄语",
    13: "菲律宾语",
    14: "马来语",
    15: "阿拉伯语",
    16: "墨西哥西班牙语",
    17: "巴西葡萄牙语",
    19: "波兰语",
    20: "土耳其语",
    21: "瑞典语",
}


def normalize_language(language) -> int:
    """
    将语种参数归一化为官方 API 的数字编码。

    同时支持两种写法：
    - 数字：0（中文）、1（英文）、2（日语）...
    - 字符串代码：cn、en、ja...

    无法识别时返回默认值 0（中文）。
    """
    if language is None:
        return 0

    # 数字直接返回（在合法范围内）
    if isinstance(language, (int, float)) and not isinstance(language, bool):
        num = int(language)
        if num in LANGUAGE_NAME_MAP:
            return num
        logger.warning(
            f"[豆包语音] 无法识别的语种数字编码: {language}，已回退默认 0（中文）"
        )
        return 0

    # 字符串：支持 "cn" / "en" / "ja" 以及 "0" / "1" / "2"
    text = str(language).strip().lower()
    if not text:
        return 0

    # 纯数字字符串 → 数字
    if text.isdigit():
        num = int(text)
        if num in LANGUAGE_NAME_MAP:
            return num
        logger.warning(
            f"[豆包语音] 无法识别的语种数字编码: {language}，已回退默认 0（中文）"
        )
        return 0

    # 字符串代码
    if text in LANGUAGE_MAP:
        return LANGUAGE_MAP[text]

    logger.warning(
        f"[豆包语音] 无法识别的语种代码: {language}，已回退默认 0（中文）"
    )
    return 0

# 训练状态描述
STATUS_MAP = {
    0: "未找到",
    1: "训练中",
    2: "训练成功",
    3: "训练失败",
    4: "已激活",
}

# 支持的音频格式
SUPPORTED_AUDIO_FORMATS = {"wav", "mp3", "ogg", "m4a", "aac", "pcm"}


class VoiceCloneClient:
    """豆包声音复刻客户端"""

    def __init__(self, api_key: str, api_base_url: str = ""):
        self.api_key = api_key
        # API 基础地址：留空使用内置默认，填写后替换（如代理地址）
        self.api_base_url = (api_base_url or DEFAULT_BASE_URL).rstrip("/")
        self.voice_clone_api_url = f"{self.api_base_url}/api/v3/tts/voice_clone"
        self.get_voice_api_url = f"{self.api_base_url}/api/v3/tts/get_voice"

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "X-Api-Key": self.api_key,
            "X-Api-Request-Id": str(uuid.uuid4()),
        }

    @staticmethod
    def _get_audio_format(file_path: str) -> str:
        """从文件路径推断音频格式"""
        suffix = Path(file_path).suffix.lower().lstrip(".")
        if suffix in SUPPORTED_AUDIO_FORMATS:
            return suffix
        # 默认返回 wav
        return "wav"

    @staticmethod
    def _read_audio_base64(file_path: str) -> str:
        """读取音频文件并转为 base64"""
        with open(file_path, "rb") as f:
            audio_bytes = f.read()

        file_size_mb = len(audio_bytes) / (1024 * 1024)
        if file_size_mb > 10:
            raise ValueError(f"音频文件过大: {file_size_mb:.1f}MB（最大 10MB）")

        return base64.b64encode(audio_bytes).decode("utf-8")

    async def train_voice(
        self,
        audio_file_path: str,
        speaker_id: str,
        custom_speaker_id: str = "",
        text: str = "",
        language: str = "cn",
        demo_text: str = "",
        enable_audio_denoise: bool = False,
        disable_volume_normalization: bool = False,
    ) -> dict:
        """
        上传音频训练自定义音色

        Args:
            audio_file_path: 音频文件路径 (.wav / .mp3 / .ogg / .m4a / .aac / .pcm)
            speaker_id: 音色 speaker_id（从控制台获取或首次训练时创建）
            custom_speaker_id: 自定义音色代号（后付费，首次训练后用于合成）
            text: 参考文本（可选），用户念诵的文本
            language: 语种代码，如 cn/en/ja
            demo_text: 试听文本，4-300字
            enable_audio_denoise: 是否开启降噪
            disable_volume_normalization: 是否关闭音量归一化

        Returns:
            API 响应 dict，包含训练状态信息
        """
        if not self.api_key:
            return {"code": -1, "message": "API Key 未配置"}

        try:
            audio_format = self._get_audio_format(audio_file_path)
            audio_base64 = self._read_audio_base64(audio_file_path)
            lang_code = normalize_language(language)

            body = {
                "speaker_id": speaker_id,
                "audio": {
                    "data": audio_base64,
                    "format": audio_format,
                },
                "language": lang_code,
            }

            if custom_speaker_id:
                body["custom_speaker_id"] = custom_speaker_id

            if text:
                body["text"] = text

            extra_params = {}
            if demo_text:
                extra_params["demo_text"] = demo_text
            if enable_audio_denoise:
                extra_params["enable_audio_denoise"] = True
            if disable_volume_normalization:
                extra_params["disable_volume_normalization"] = True

            if extra_params:
                body["extra_params"] = extra_params

            logger.info(
                f"[豆包语音] 开始声音复刻训练: speaker_id={speaker_id}, "
                f"file={Path(audio_file_path).name}, "
                f"format={audio_format}, language={language}"
            )

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    self.voice_clone_api_url,
                    headers=self._build_headers(),
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()

                code = result.get("code", -1)
                message = result.get("message", "")
                status = result.get("status", -1)
                status_text = STATUS_MAP.get(status, f"未知状态({status})")

                logger.info(
                    f"[豆包语音] 训练请求完成: code={code}, "
                    f"status={status}({status_text}), message={message}"
                )

                return result

        except FileNotFoundError:
            error_msg = f"音频文件未找到: {audio_file_path}"
            logger.error(f"[豆包语音] {error_msg}")
            return {"code": -1, "message": error_msg}
        except ValueError as e:
            logger.error(f"[豆包语音] {e}")
            return {"code": -1, "message": str(e)}
        except Exception as e:
            logger.error(f"[豆包语音] 声音复刻训练失败: {e}", exc_info=True)
            return {"code": -1, "message": str(e)}

    async def query_voice(
        self,
        speaker_id: str,
        custom_speaker_id: str = "",
    ) -> dict:
        """
        查询音色训练状态

        Args:
            speaker_id: 音色 speaker_id
            custom_speaker_id: 自定义音色代号（后付费音色需传入）

        Returns:
            音色状态信息 dict
        """
        if not self.api_key:
            return {"code": -1, "message": "API Key 未配置"}

        try:
            body = {"speaker_id": speaker_id}
            if custom_speaker_id:
                body["custom_speaker_id"] = custom_speaker_id

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.get_voice_api_url,
                    headers=self._build_headers(),
                    json=body,
                )
                resp.raise_for_status()
                result = resp.json()

                status = result.get("status", -1)
                status_text = STATUS_MAP.get(status, f"未知状态({status})")
                speaker_status = result.get("speaker_status", [])

                logger.info(
                    f"[豆包语音] 音色查询: speaker_id={speaker_id}, "
                    f"status={status}({status_text}), "
                    f"model_types={[s.get('model_type') for s in speaker_status]}"
                )

                return result

        except Exception as e:
            logger.error(f"[豆包语音] 音色查询失败: {e}", exc_info=True)
            return {"code": -1, "message": str(e)}

    @staticmethod
    def is_ready(status: int) -> bool:
        """判断音色是否可用于 TTS 合成（status 为 2 或 4）"""
        return status in (2, 4)

    @staticmethod
    def format_query_result(result: dict) -> str:
        """格式化音色查询结果为可读文本"""
        if result.get("code", -1) != 0:
            return f"查询失败: {result.get('message', '未知错误')}"

        status = result.get("status", -1)
        status_text = STATUS_MAP.get(status, f"未知({status})")
        speaker_id = result.get("speaker_id", "N/A")

        # 语种：API 返回数字编码，转成中文名显示
        language_raw = result.get("language", "N/A")
        try:
            language_num = int(language_raw)
            language = LANGUAGE_NAME_MAP.get(language_num, f"未知({language_raw})")
        except (TypeError, ValueError):
            language = str(language_raw)

        available = result.get("available_training_times", "N/A")

        speaker_status = result.get("speaker_status", [])
        model_types = []
        demo_audios = []
        for s in speaker_status:
            mt = s.get("model_type", "?")
            model_types.append(str(mt))
            if s.get("demo_audio"):
                demo_audios.append(s["demo_audio"])

        lines = [
            f"音色ID: {speaker_id}",
            f"状态: {status_text}",
            f"语种: {language}",
            f"剩余训练次数: {available}",
            f"模型类型: {', '.join(model_types) if model_types else 'N/A'}",
            f"是否可用: {'是' if VoiceCloneClient.is_ready(status) else '否'}",
        ]

        if demo_audios:
            lines.append(f"试听链接(1小时有效): {demo_audios[0]}")

        return "\n".join(lines)
