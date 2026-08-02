"""
指令控制模块 - 调用外部 LLM API 生成语音指令
兼容 OpenAI Chat Completions API 格式

两种模式（互斥）：
- QA 模式（context_texts）：大模型返回完整指令句，作用于整次合成（全局语气控制）
- Cot 模式（use_tag_parser）：切分文本后大模型逐片返回情感词，填入 <cot text=> 标签（句子级控制）

启用条件：请求地址、模型名、API Key 三项缺一不可。
"""

import re
import httpx
from astrbot.api import logger

# Cot 单句最大字符数（官方文档建议 < 64，含 cot 标签）
COT_SENTENCE_MAX_LEN = 64

# Cot 大模型硬约束后缀：强制输出格式为 "词1,词2,词3..."
_COT_HARD_CONSTRAINT = (
    "\n\n【输出约束 - 必须严格遵守】\n"
    "1. 你只能输出情感/语气词，用英文逗号分隔，例如：开心,难过,激动\n"
    "2. 词的数量必须与输入的片段数量完全一致，按片段顺序一一对应\n"
    "3. 每个词控制在 2-8 个汉字，描述该片段应使用的语气/情感\n"
    "4. 不得输出任何解释、说明、编号、引号或其他多余内容\n"
    '5. 如果某个片段无法判断情感，输出"平静"\n'
    "6. 禁止输出逗号分隔的词列表以外的任何字符"
)

# QA 大模型硬约束后缀：强制只输出一句指令
_QA_HARD_CONSTRAINT = (
    "\n\n【输出约束 - 必须严格遵守】\n"
    "1. 你只能输出一句话的语音指令，用于指导语音合成的语气/情感\n"
    "2. 指令格式示例：「请用开心活泼的语气说话」「请用低沉悲伤的语气朗读」\n"
    "3. 不得输出任何解释、说明、前后缀或多余内容\n"
    "4. 指令长度控制在 30 字以内\n"
    "5. 禁止输出指令句以外的任何字符"
)


class InstructionController:
    """指令控制器，通过外部 LLM API 生成语音指令"""

    def __init__(
        self,
        api_base_url: str,
        api_key: str,
        model_name: str,
    ):
        self.api_base_url = (api_base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model_name = model_name or ""

    @property
    def enabled(self) -> bool:
        """指令控制是否启用（请求地址、模型名、API Key 三项缺一不可）"""
        return bool(self.api_base_url and self.model_name and self.api_key)

    def _chat_completions_url(self) -> str:
        """兼容 base_url 填 /v1 或完整 /chat/completions 的情况"""
        if self.api_base_url.endswith("/chat/completions"):
            return self.api_base_url
        return f"{self.api_base_url}/chat/completions"

    @staticmethod
    def parse_splitters(splitters: str) -> list:
        """
        解析 Cot 分词规则配置字符串。
        配置格式：用 - 分隔的标点，如 "。-！-？"
        返回：["。", "！", "？"]
        """
        if not splitters or not splitters.strip():
            return ["。", "！", "？"]
        parts = [s.strip() for s in splitters.split("-") if s.strip()]
        return parts if parts else ["。", "！", "？"]

    @staticmethod
    def split_text_by_splitters(text: str, splitters: list) -> list:
        """
        按分词标点切分文本，标点保留在片段末尾。
        返回切分后的片段列表（保留标点）。
        """
        if not text or not splitters:
            return [text] if text else []
        # 构建正则：在任一分隔符处切分，并保留分隔符
        pattern = "({})".format("|".join(re.escape(s) for s in splitters))
        parts = re.split(pattern, text)
        # re.split 带捕获组会返回 [片段, 标点, 片段, 标点, ...]
        # 把标点合并回前一个片段
        segments = []
        current = ""
        for part in parts:
            if not part:
                continue
            if part in splitters:
                current += part
                segments.append(current)
                current = ""
            else:
                current += part
        if current:
            segments.append(current)
        return [s for s in segments if s.strip()]

    async def _call_llm(self, system_prompt: str, user_content: str) -> str:
        """调用 LLM，返回纯文本响应；失败返回空字符串"""
        if not self.enabled:
            return ""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                payload = {
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                }
                resp = await client.post(
                    self._chat_completions_url(), headers=headers, json=payload
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.error(f"[豆包语音插件] [指令控制] LLM 调用失败: {e}")
            return ""

    async def generate_qa_instruction(self, text: str) -> str:
        """
        QA 模式：调用大模型，返回完整的 context_texts 指令句。
        大模型直接输出一句指令（如「请用开心活泼的语气说话」），失败返回空字符串。
        """
        if not self.enabled or not text or len(text.strip()) < 2:
            return ""
        system_prompt = (
            "你是一个语音合成语气指导专家。"
            "请根据用户提供的文本，判断其情感色彩，并输出一句用于指导语音合成语气的指令。"
            + _QA_HARD_CONSTRAINT
        )
        result = await self._call_llm(system_prompt, text[:2048])
        if result:
            logger.info(f"[豆包语音插件] [指令控制-QA] 生成指令: {result[:50]}")
        return result

    async def generate_cot_text(self, text: str, splitters: str) -> tuple:
        """
        Cot 模式：切分文本 → 大模型逐片返回情感词 → 填入 <cot text=> 标签。

        Args:
            text: 待处理的文本（通常是过滤后的文本）
            splitters: 分词规则配置字符串（如 "。-！-？"）

        Returns:
            (cot_text, ok):
              - cot_text: 带标签的文本（若处理失败则返回原文）
              - ok: 是否成功打标签（False 时 cot_text 为原文，调用方不应启用 use_tag_parser）
        """
        if not self.enabled or not text or len(text.strip()) < 2:
            return text, False

        splitter_list = self.parse_splitters(splitters)
        segments = self.split_text_by_splitters(text, splitter_list)

        if not segments:
            return text, False

        # 标记每个片段是否可打标签（长度 <= COT_SENTENCE_MAX_LEN）
        taggable_flags = [len(s) <= COT_SENTENCE_MAX_LEN for s in segments]
        taggable_indices = [i for i, ok in enumerate(taggable_flags) if ok]

        if not taggable_indices:
            logger.warning("[豆包语音插件] [指令控制-Cot] 所有片段均超过 64 字符，跳过打标签")
            return text, False

        # 只把可打标签的片段交给大模型
        taggable_segments = [segments[i] for i in taggable_indices]

        # 构造给大模型的输入：带编号的片段列表
        numbered = "\n".join(
            f"{i+1}. {seg}" for i, seg in enumerate(taggable_segments)
        )
        system_prompt = (
            "你是一个语音合成语气标注专家。"
            "下面会给你若干个文本片段（带编号），请为每个片段判断情感色彩，"
            "输出对应的情感/语气词，用英文逗号分隔，按片段顺序一一对应。"
            f"\n\n片段列表：\n{numbered}"
            + _COT_HARD_CONSTRAINT
        )

        raw_response = await self._call_llm(system_prompt, numbered)
        if not raw_response:
            logger.warning("[豆包语音插件] [指令控制-Cot] 大模型返回空，跳过打标签")
            return text, False

        # 解析大模型返回的词列表
        words = [w.strip() for w in raw_response.replace("，", ",").split(",") if w.strip()]

        # 词数不匹配处理：
        # - 词少了：多余的片段不打标签（原样）
        # - 词多了：多余的词忽略
        if len(words) < len(taggable_segments):
            logger.warning(
                f"[豆包语音插件] [指令控制-Cot] 词数({len(words)}) < 片段数({len(taggable_segments)})，"
                f"多余片段不打标签"
            )
        elif len(words) > len(taggable_segments):
            logger.warning(
                f"[豆包语音插件] [指令控制-Cot] 词数({len(words)}) > 片段数({len(taggable_segments)})，"
                f"多余词忽略"
            )
            words = words[: len(taggable_segments)]

        # 填入标签：词数与可打标签片段一一对应
        result_segments = list(segments)  # 拷贝
        for idx, seg_idx in enumerate(taggable_indices):
            if idx < len(words):
                word = words[idx]
                result_segments[seg_idx] = f"<cot text={word}>{segments[seg_idx]}</cot>"
            # 词不够时，原样保留不打标签

        cot_text = "".join(result_segments)
        logger.info(
            f"[豆包语音插件] [指令控制-Cot] 打标签完成: "
            f"{len(words)}/{len(taggable_indices)} 片段已标注"
        )
        return cot_text, True
