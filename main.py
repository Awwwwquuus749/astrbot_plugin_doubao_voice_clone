"""
豆包声音复刻语音合成插件 for AstrBot
基于火山引擎豆包声音复刻大模型2.0，自动将 LLM 回复转为语音

工作流：
LLM 回复 → ① 本地过滤 → ② 大模型过滤(可选) → ③ 翻译(可选) → ④ TTS 合成 → ⑤ 发送语音
"""

import asyncio
import json
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .filter import TextFilter
from .llm_filter import LLMFilter
from .translator import Translator
from .tts_client import DoubaoTTSClient
from .voice_clone import VoiceCloneClient
from .instruction import InstructionController


@register(
    "astrbot_plugin_doubao_voice_clone",
    "Awwwwquuus749",
    "基于火山引擎豆包声音复刻大模型2.0的语音合成插件，支持声音复刻训练 + TTS 合成",
    "0.1.0",
    "https://github.com/Awwwwquuus749/astrbot_plugin_doubao_voice_clone",
)
class DoubaoVoiceClonePlugin(Star):
    """
    豆包声音复刻语音合成插件

    功能：
    1. 本地文本过滤（删除/替换指定字符，纯本地，零延迟）
    2. 大模型过滤（调用 LLM API，按 System Prompt 智能过滤文本）
    3. 翻译为目标语种（调用 LLM API）
    4. 调用豆包声音复刻 TTS API 合成语音
    5. 返回语音消息给用户
    6. 声音复刻训练 & 查询（通过 API 管理自定义音色）
    """

    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.config = config

        # 分组配置读取辅助：config 按 object 分组后是嵌套 dict
        def cfg(group: str, key: str, default=None):
            return config.get(group, {}).get(key, default)

        # ---- ① 文本过滤器（纯本地处理） ----
        self.text_filter = TextFilter(
            filter_chars=cfg("text_filter_settings", "filter_chars", ""),
            filter_replacement=cfg("text_filter_settings", "filter_replacement", ""),
            remove_brackets=cfg("text_filter_settings", "remove_brackets", False),
        )

        # ---- ② 大模型过滤器 ----
        self.llm_filter = LLMFilter(
            api_base_url=cfg("llm_filter_settings", "llm_filter_api_base_url", ""),
            api_key=cfg("llm_filter_settings", "llm_filter_api_key", ""),
            model_name=cfg("llm_filter_settings", "llm_filter_model_name", ""),
            system_prompt=cfg("llm_filter_settings", "llm_filter_system_prompt")
            or "请把下面的文本中的动作描写全部删掉，不要说多余的话",
        )

        # ---- ③ 翻译器 ----
        self.translator = Translator(
            api_base_url=cfg("translate_settings", "translate_api_base_url", ""),
            api_key=cfg("translate_settings", "translate_api_key", ""),
            model_name=cfg("translate_settings", "translate_model_name", ""),
            prompt=cfg("translate_settings", "translate_prompt", ""),
        )

        # ---- ④ TTS 客户端 ----
        tts_api_key = cfg("tts_settings", "volcengine_api_key", "")
        api_base_url = cfg("tts_settings", "api_base_url", "")
        self.tts_client = DoubaoTTSClient(
            api_key=tts_api_key,
            speaker_id=cfg("tts_settings", "speaker_id", ""),
            resource_id=cfg("tts_settings", "resource_id", "seed-icl-2.0"),
            tts_model=cfg("tts_settings", "tts_model", ""),
            audio_format=cfg("tts_settings", "audio_format", "mp3"),
            sample_rate=self._parse_int(cfg("tts_settings", "sample_rate", 24000), 24000, "sample_rate"),
            speech_rate=self._parse_int(cfg("tts_settings", "speech_rate", 0), 0, "speech_rate"),
            loudness_rate=self._parse_int(cfg("tts_settings", "loudness_rate", 0), 0, "loudness_rate"),
            explicit_language=cfg("tts_settings", "explicit_language", ""),
            tone_fidelity=cfg("tts_settings", "tone_fidelity", False),
            api_base_url=api_base_url,
        )

        # ---- ⑤ 指令控制器（QA / Cot 模式，调 LLM 生成语音指令） ----
        self.instruction_controller = InstructionController(
            api_base_url=cfg("instruction_settings", "instruction_api_base_url", ""),
            api_key=cfg("instruction_settings", "instruction_api_key", ""),
            model_name=cfg("instruction_settings", "instruction_model_name", ""),
        )
        self.instruction_mode = (cfg("instruction_settings", "instruction_mode", "none") or "none").lower()
        self.instruction_text_source = (cfg("instruction_settings", "instruction_text_source", "filtered") or "filtered").lower()
        self.cot_splitters = cfg("instruction_settings", "cot_splitters", "。-！-？") or "。-！-？"

        # ---- ⑥ section_id 会话映射表 ----
        # 格式："会话标识|section_id"，会话标识用 AstrBot 的 session_id
        # 如 "default:GroupMessage:123456789|bf5b5771-31cd-4f7a-b30c-f4ddcbf2f9da"
        self.enable_context = cfg("context_settings", "enable_context", False)
        self.section_id_map = self._parse_section_id_map(
            cfg("context_settings", "section_id_map", [])
        )

        # ---- ⑦ 声音复刻客户端（训练用） ----
        self.voice_clone_client = VoiceCloneClient(
            api_key=tts_api_key,
            api_base_url=api_base_url,
        )

        # ---- 音频存储目录 ----
        self.data_dir = Path("data") / "doubao_tts"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # ---- 音频文件自动清理配置 ----
        try:
            self.audio_retention_minutes = int(
                cfg("audio_management", "audio_retention_minutes", -1)
            )
        except (TypeError, ValueError):
            self.audio_retention_minutes = -1

        # ---- 根据音频格式确定文件后缀 ----
        fmt = self.tts_client.audio_format
        if fmt == "pcm":
            self.file_ext = ".pcm"
        elif fmt == "ogg_opus":
            self.file_ext = ".ogg"
        elif fmt == "wav":
            self.file_ext = ".wav"
        else:
            self.file_ext = ".mp3"

        # ---- 打印初始化状态 ----
        filter_parts = []
        if self.text_filter.brackets_enabled:
            filter_parts.append("括号过滤")
        if self.text_filter.enabled:
            filter_parts.append(f"过滤词已启用")
        filter_status = " + ".join(filter_parts) if filter_parts else "未启用"

        retention_status = (
            "不自动清理"
            if self.audio_retention_minutes < 0
            else f"{self.audio_retention_minutes} 分钟"
        )

        logger.info(
            f"[豆包语音插件] 初始化完成 | "
            f"资源: {self.tts_client.resource_id} | "
            f"音色: {self.tts_client.speaker_id} | "
            f"格式: {self.tts_client.audio_format}/{self.tts_client.sample_rate}Hz | "
            f"语速: {self.tts_client.speech_rate} | "
            f"音量: {self.tts_client.loudness_rate} | "
            f"语种: {self.tts_client.explicit_language or '自动'} | "
            f"还原模式: {'启用' if self.tts_client.tone_fidelity else '关闭'} | "
            f"指令控制: {self._instruction_status()} | "
            f"上下文: {'启用(' + str(len(self.section_id_map)) + '条映射)' if self.enable_context else '关闭'} | "
            f"本地过滤: {filter_status} | "
            f"大模型过滤: {'启用' if self.llm_filter.enabled else '未启用'} | "
            f"翻译: {'启用' if self.translator.enabled else '未启用'} | "
            f"音频保留: {retention_status} | "
            f"音频目录: {self.data_dir}"
        )

    # ──────────────────── 工具方法 ────────────────────

    @staticmethod
    def _parse_section_id_map(raw_list) -> dict:
        """
        解析 section_id 映射表配置。
        每项格式："会话标识|section_id"，会话标识用 AstrBot 的 session_id
        如 "default:GroupMessage:123456789|bf5b5771-31cd-4f7a-b30c-f4ddcbf2f9da"

        返回：{会话标识: section_id}
        """
        result = {}
        if not raw_list:
            return result
        for item in raw_list:
            if not isinstance(item, str) or "|" not in item:
                logger.warning(f"[豆包语音插件] section_id_map 项格式错误（缺少 |）: {item}，已跳过")
                continue
            parts = item.split("|", 1)
            session_id, section_id = parts[0].strip(), parts[1].strip()
            if session_id and section_id:
                result[session_id] = section_id
        return result

    def _instruction_status(self) -> str:
        """返回指令控制配置状态描述"""
        if self.instruction_mode == "none":
            return "关闭"
        if not self.instruction_controller.enabled:
            return f"{self.instruction_mode}(未配置API)"
        if self.instruction_mode == "qa":
            return f"QA(文本源:{self.instruction_text_source})"
        if self.instruction_mode == "cot":
            return f"Cot(分词:{self.cot_splitters})"
        return self.instruction_mode

    def _get_section_id(self, event: AstrMessageEvent) -> str:
        """从映射表查当前会话的 section_id，未命中或总开关关闭返回空字符串"""
        if not self.enable_context:
            return ""
        try:
            session_id = event.session_id  # 如 "default:GroupMessage:123456789"
        except Exception:
            session_id = ""
        if not session_id:
            return ""
        section_id = self.section_id_map.get(session_id, "")
        if not section_id:
            logger.debug(f"[豆包语音插件] 会话 {session_id} 未命中 section_id 映射，不传上下文")
        return section_id

    @staticmethod
    def _parse_int(value, default: int, name: str) -> int:
        """安全解析整数配置项"""
        try:
            return int(value)
        except (TypeError, ValueError):
            logger.warning(f"[豆包语音插件] {name} 配置无效({value})，已回退默认值 {default}")
            return default

    def _cleanup_old_audio_files(self):
        """清理超过保留时长的历史音频文件"""
        if self.audio_retention_minutes < 0:
            return

        now = time.time()
        retention_seconds = self.audio_retention_minutes * 60
        cleaned_count = 0

        try:
            for file_path in self.data_dir.iterdir():
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in {".mp3", ".wav", ".pcm", ".ogg"}:
                    continue
                file_age_seconds = now - file_path.stat().st_mtime
                if file_age_seconds > retention_seconds:
                    file_path.unlink()
                    cleaned_count += 1

            if cleaned_count > 0:
                logger.info(f"[豆包语音插件] 已清理 {cleaned_count} 个过期音频文件")
        except Exception as e:
            logger.warning(f"[豆包语音插件] 清理音频文件失败: {e}", exc_info=True)

    # ──────────────────── 指令注册 ────────────────────

    def _register_commands(self):
        """注册插件指令"""
        # 使用 filter 装饰器注册指令
        pass  # 指令在下面的 handler 方法中实现

    # ──────────────────── LLM 回复监听 ────────────────────

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, resp):
        """
        监听 LLM 回复事件，将文本转为语音

        完整工作流：
        获取文本 → 本地过滤 → 大模型过滤 → 翻译 → 指令控制(QA/Cot) → TTS → 发送语音
        """
        # 白名单检查
        whitelist = self.config.get("whitelist", {}).get("whitelist", [])
        if whitelist:
            sender_id = getattr(event, "sender_id", None) or getattr(
                event, "get_sender_id", lambda: None
            )()
            if sender_id and sender_id not in whitelist:
                logger.info(f"[豆包语音插件] 对象 {sender_id} 不在白名单，跳过语音")
                return

        try:
            # ── ① 获取 LLM 回复文本（保存原始文本供 QA 模式判断用） ──
            full_text = getattr(resp, "completion_text", None) or str(resp)
            full_text = full_text.strip()
            if not full_text:
                return
            logger.info(f"[豆包语音插件] 收到 LLM 回复，长度: {len(full_text)} 字符")
            text = full_text  # text 会随过滤步骤更新

            # ── ② 文本过滤（纯本地） ──
            if self.text_filter.enabled:
                filtered = self.text_filter.filter(text)
                if filtered != text:
                    logger.info(
                        f"[豆包语音插件] 过滤完成: {len(text)}字 -> {len(filtered)}字"
                    )
                text = filtered
                if not text.strip():
                    logger.warning("[豆包语音插件] 过滤后文本为空，跳过")
                    return

            # ── ③ 大模型过滤（可选） ──
            if self.llm_filter.enabled:
                llm_filtered = await self.llm_filter.filter(text)
                if llm_filtered != text:
                    logger.info(
                        f"[豆包语音插件] 大模型过滤完成: {len(text)}字 -> {len(llm_filtered)}字"
                    )
                text = llm_filtered
                if not text.strip():
                    logger.warning("[豆包语音插件] 大模型过滤后文本为空，跳过")
                    return

            # ── ④ 翻译（可选） ──
            if self.translator.enabled:
                translated = await self.translator.translate(text)
                if translated != text:
                    logger.info(
                        f"[豆包语音插件] 翻译完成: {len(text)}字 -> {len(translated)}字"
                    )
                text = translated

            # ── ⑤ 指令控制（QA / Cot，可选） ──
            context_texts = ""
            use_tag_parser = False
            synth_text = text  # 实际送去合成的文本（Cot 模式下会被替换为带标签文本）

            if self.instruction_mode != "none" and self.instruction_controller.enabled:
                # 前置依赖检查：复刻音色 standard 版不支持指令控制
                if (self.tts_client.resource_id == "seed-icl-2.0"
                        and self.tts_client.tts_model != "seed-tts-2.0-expressive"):
                    logger.warning(
                        "[豆包语音插件] 指令控制已启用，但当前为复刻音色标准版(不支持)，跳过指令控制。"
                        "如需启用，请将 tts_model 设为 seed-tts-2.0-expressive 或改用合成音色(seed-tts-2.0)"
                    )
                elif self.instruction_mode == "qa":
                    # QA 模式：根据 instruction_text_source 选文本给大模型判断
                    judge_text = full_text if self.instruction_text_source == "full" else text
                    context_texts = await self.instruction_controller.generate_qa_instruction(judge_text)
                    if not context_texts:
                        logger.warning("[豆包语音插件] QA 指令生成失败，将不带指令合成")
                elif self.instruction_mode == "cot":
                    # Cot 模式：用过滤后的文本切分+打标签
                    cot_text, ok = await self.instruction_controller.generate_cot_text(
                        text, self.cot_splitters
                    )
                    if ok:
                        synth_text = cot_text
                        use_tag_parser = True
                    else:
                        logger.warning("[豆包语音插件] Cot 打标签失败，将使用原文本合成")
            elif self.instruction_mode != "none":
                logger.warning(
                    f"[豆包语音插件] 指令控制模式={self.instruction_mode} 但 LLM API 未完整配置，跳过指令控制"
                )

            # ── ⑥ 查 section_id 映射表 ──
            section_id = self._get_section_id(event)

            # ── ⑦ 语音合成 ──
            audio_data = await self.tts_client.synthesize(
                text=synth_text,
                section_id=section_id,
                context_texts=context_texts,
                use_tag_parser=use_tag_parser,
            )
            if audio_data is None:
                logger.warning("[豆包语音插件] 语音合成失败，跳过发送")
                return

            # ── ⑧ 保存音频文件 ──
            timestamp = int(time.time() * 1000)
            file_path = self.data_dir / f"doubao_{timestamp}{self.file_ext}"
            with open(file_path, "wb") as f:
                f.write(audio_data)
            logger.info(
                f"[豆包语音插件] 音频已保存: {file_path.name} "
                f"({len(audio_data) / 1024:.1f} KB)"
            )

            # ── ⑨ 清理过期音频 ──
            self._cleanup_old_audio_files()

            # ── ⑩ 发送语音消息 ──
            chain = [Comp.Record(file=str(file_path))]
            result = event.make_result()
            result.chain = chain
            await event.send(result)
            logger.info("[豆包语音插件] 语音消息已发送")

        except Exception as e:
            logger.error(f"[豆包语音插件] 处理异常: {e}", exc_info=True)

    # ──────────────────── 指令处理 ────────────────────

    @filter.command("doubao_train")
    async def cmd_train(self, event: AstrMessageEvent, message: str = ""):
        """
        上传音频训练新音色
        用法: /doubao_train <音频文件路径> [speaker_id] [custom_speaker_id] [language]
        示例: /doubao_train /path/to/audio.wav my_speaker_id my_custom_voice cn
        language: 支持数字(0=中文, 1=英文, 2=日语...) 或 代码(cn, en, ja...)
        """
        if not message.strip():
            yield event.plain_result(
                "用法: /doubao_train <音频文件路径> [speaker_id] [custom_speaker_id] [language]\n"
                "示例: /doubao_train /path/to/audio.wav S_xxxx my_voice cn\n"
                "language 两种写法均可:\n"
                "  数字: 0=中文 1=英文 2=日语 3=西班牙语 4=印尼语 5=葡萄牙语\n"
                "  代码: cn en ja es id pt de fr ko it th vi ru fil ms ar mx pt-br pl tr sv\n"
                "支持格式: wav, mp3, ogg, m4a, aac, pcm（最大 10MB）"
            )
            return

        parts = message.strip().split()
        audio_file = parts[0]
        speaker_id = parts[1] if len(parts) > 1 else ""
        custom_speaker_id = parts[2] if len(parts) > 2 else ""
        language = parts[3] if len(parts) > 3 else "cn"

        if not speaker_id:
            yield event.plain_result("请提供 speaker_id")
            return

        yield event.plain_result(f"开始训练音色 {speaker_id}...\n音频文件: {audio_file}")

        result = await self.voice_clone_client.train_voice(
            audio_file_path=audio_file,
            speaker_id=speaker_id,
            custom_speaker_id=custom_speaker_id,
            language=language,
        )

        code = result.get("code", -1)
        if code == 0:
            status = result.get("status", -1)
            status_map = {0: "未找到", 1: "训练中", 2: "训练成功", 3: "训练失败", 4: "已激活"}
            status_text = status_map.get(status, f"未知({status})")
            available = result.get("available_training_times", "N/A")

            yield event.plain_result(
                f"训练请求成功!\n"
                f"speaker_id: {result.get('speaker_id', 'N/A')}\n"
                f"状态: {status_text}\n"
                f"剩余训练次数: {available}\n\n"
                f"训练完成后，可在插件配置中将 speaker_id 设为训练好的音色ID。"
            )
        else:
            yield event.plain_result(
                f"训练失败: {result.get('message', '未知错误')}\n"
                f"code: {code}"
            )

    # ──────────────────── 生命周期 ────────────────────

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """AstrBot 加载完成时输出提示"""
        logger.info(
            "[豆包语音插件] 已就绪 - "
            "LLM 回复 → 过滤 → [大模型过滤] → [翻译] → [指令控制] → 豆包声音复刻 TTS → 语音 🔊\n"
            "可用指令:\n"
            "  /doubao_train <音频路径> <speaker_id> - 声音复刻训练"
        )

    async def terminate(self):
        """插件卸载时的清理工作"""
        logger.info("[豆包语音插件] 已卸载")
