# AstrBot 豆包声音复刻语音合成插件

基于火山引擎 **豆包声音复刻大模型2.0 (seed-icl-2.0)** 的语音合成插件，自动将 LLM 回复转为语音，并附带原文。

> **说明**：本插件由 AI 生成。

## 功能特性

- 🎤 自动将机器人回复文本转为语音（豆包声音复刻音色）
- 🎵 **声音复刻训练**：上传音频样本训练自定义音色
- 🔧 **本地文本过滤**（纯本地处理，零延迟）
- 🤖 **大模型过滤**（调用 LLM API 智能过滤文本）
- 🌐 **翻译**（调用 LLM API 翻译为目标语种）
- 🎛️ **还原模式** (tone_fidelity)：尽可能还原训练音频的音色和说话风格
- 🎭 **指令控制**（QA/Cot 双模式，大模型判断情感语气）
- 🔗 **多轮上下文**（section_id 会话映射表，按会话保持语境）
- 🎵 支持多种音频格式（mp3, ogg_opus, pcm, wav）
- 🧹 支持按分钟自动清理历史音频文件
- 👥 支持白名单模式控制触发范围
- 🔄 非阻塞异步处理，不影响正常文本回复

## 文件结构

```text
astrbot_plugin_doubao_voice_clone/
├── metadata.yaml           # 插件元数据
├── _conf_schema.json       # WebUI 配置页面
├── requirements.txt        # 依赖
├── filter.py               # 本地文本过滤器
├── llm_filter.py           # 大模型过滤器
├── translator.py           # 翻译模块
├── tts_client.py           # 豆包 TTS 合成模块（HTTP Chunked）
├── voice_clone.py          # 声音复刻训练 & 查询模块
├── main.py                 # 插件主入口
└── README.md               # 使用文档
```

## 快速开始

### 前置准备

1. 在火山引擎控制台开通语音合成服务：https://console.volcengine.com/speech/new
2. 获取 API Key：控制台 → API Key 管理
3. 训练或获取声音复刻音色 ID（speaker_id）

### 安装步骤

```bash
# 1. 进入 AstrBot 插件目录
cd /path/to/AstrBot/data/plugins/

# 2. 解压插件包
unzip astrbot_plugin_doubao_voice_clone.zip

# 3. 进入插件目录
cd astrbot_plugin_doubao_voice_clone

# 4. 安装依赖
pip install -r requirements.txt

# 5. 重载 AstrBot 或在 WebUI 中启用插件
```

### 最小配置

在 WebUI 插件配置页面中填写以下必填项：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `volcengine_api_key` | 火山引擎 API Key | 从控制台获取 |
| `speaker_id` | 声音复刻音色 ID | `S_xxxx` 或 `icl_xxxx` |
| `resource_id` | 模型版本（推荐 seed-icl-2.0） | `seed-icl-2.0` |

## 配置项说明

### TTS 核心配置

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `volcengine_api_key` | string | ✅ | - | 火山引擎 API Key |
| `speaker_id` | string | ✅ | - | 声音复刻音色 ID |
| `resource_id` | string | ❌ | `seed-icl-2.0` | 模型版本 |
| `tts_model` | string | ❌ | - | 具体模型版本（仅声音复刻2.0）|
| `audio_format` | string | ❌ | `mp3` | 音频格式 (mp3/ogg_opus/pcm/wav) |
| `sample_rate` | int | ❌ | `24000` | 采样率 |
| `speech_rate` | int | ❌ | `0` | 语速 [-50, 100] |
| `loudness_rate` | int | ❌ | `0` | 音量 [-50, 100] |
| `explicit_language` | string | ❌ | - | 明确语种，留空=中英混读 |
| `tone_fidelity` | bool | ❌ | `false` | 还原模式，尽可能还原训练音频的音色和风格（仅 ICL 2.0） |

### 多轮上下文配置

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `section_id_map` | list | ❌ | `[]` | 会话-上下文映射表，每项格式「会话标识\|section_id」 |

**映射表写法**：会话标识用 AstrBot 的 `session_id`（如 `default:GroupMessage:123456789`），用 `|` 与 section_id 分隔。

```json
["default:GroupMessage:123456789|bf5b5771-31cd-4f7a-b30c-f4ddcbf2f9da", "default:FriendMessage:123456|my-ctx-001"]
```

- 命中映射 → 传对应 section_id，服务端保存对话历史实现多轮语义保持
- 未命中 → 不传 section_id（无上下文）
- ICL 2.0 和 TTS 2.0 都支持

### 指令控制配置（QA / Cot 双模式）

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `instruction_mode` | string | ❌ | `none` | 指令控制模式：none/qa/cot |
| `instruction_api_base_url` | string | ❌ | - | 大模型 API 地址（OpenAI 兼容） |
| `instruction_model_name` | string | ❌ | - | 大模型名称 |
| `instruction_api_key` | string | ❌ | - | 大模型 API Key |
| `instruction_text_source` | string | ❌ | `filtered` | QA 模式文本来源：full/filtered |
| `cot_splitters` | string | ❌ | `。-！-？` | Cot 分词规则（用 - 分隔） |

**模式说明**：

| 模式 | 机制 | 粒度 | 大模型输入 | 大模型输出 | 填入位置 |
|------|------|------|-----------|-----------|---------|
| `qa` | context_texts | 全局（整次合成） | 完整/过滤后文本 | 一句指令（如「请用开心语气说话」） | `req_params.context_texts` |
| `cot` | use_tag_parser | 句子级 | 切分后的片段列表 | 情感词列表（如「开心,难过」） | 文本内嵌 `<cot text=词>` 标签 |

**QA 模式工作流**：
```
选文本(full/filtered) → 大模型判断情感 → 返回完整指令句 → 填入 context_texts
```

**Cot 模式工作流**：
```
过滤后文本 → 按 cot_splitters 切分 → 大模型逐片返回情感词(硬约束逗号分隔)
→ 填入 <cot text=词N>片段</cot> → 带标签文本 + use_tag_parser=true
```

**Cot 边界处理**：
- 单片段 > 64 字符 → 跳过打标签（原样）
- 大模型返回词数 < 片段数 → 多余片段不打标签
- 大模型返回词数 > 片段数 → 多余词忽略

**前置依赖**：指令控制需要 `resource_id=seed-tts-2.0` 或 `tts_model=seed-tts-2.0-expressive`（复刻表现力增强版）。复刻标准版会自动跳过指令控制并警告。

### ICL vs TTS 能力差异

| 能力 | 复刻 standard (seed-icl-2.0) | 复刻 expressive | 合成音色 (seed-tts-2.0) |
|------|:---:|:---:|:---:|
| 语音指令 context_texts | ❌ | ✅（有抽卡风险） | ✅ |
| 语音标签 use_tag_parser | ❌ | ✅ | ❌ |
| 多轮上下文 section_id | ✅ | ✅ | ✅ |
| 还原模式 tone_fidelity | ✅ | ✅ | ❌ |

> **计费说明**：豆包语音合成2.0 / 声音复刻2.0 按**字符数**计费（预付费 28元/十万字符 后付费 3 元/万字符）。`context_texts` 语音指令文本**明确不参与计费**；`section_id` 本身只是关联 ID 不计费，但开启上下文后服务端会保存并携带对话历史参与推理——历史文本是否额外计费官方未明确，以账单为准。

### 文本处理配置

| 配置项 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `remove_brackets` | bool | ❌ | `false` | 启用括号过滤 |
| `filter_chars` | string | ❌ | - | 过滤字符列表 |
| `filter_replacement` | string | ❌ | - | 过滤替换为 |
| `llm_filter_api_base_url` | string | ❌ | - | 大模型过滤 API 地址 |
| `llm_filter_model_name` | string | ❌ | - | 大模型过滤模型名 |
| `llm_filter_api_key` | string | ❌ | - | 大模型过滤 API Key |
| `llm_filter_system_prompt` | string | ❌ | 内置默认 | 大模型过滤 System Prompt |
| `translate_api_base_url` | string | ❌ | - | 翻译 API 地址 |
| `translate_api_key` | string | ❌ | - | 翻译 API Key |
| `translate_model_name` | string | ❌ | - | 翻译模型名称 |
| `translate_prompt` | string | ❌ | - | 翻译提示词 |
| `audio_retention_minutes` | int | ❌ | `-1` | 音频保留分钟数 |
| `whitelist` | list | ❌ | `[]` | 白名单用户/群组 |

## 声音复刻训练流程

### 1. 准备音频样本

- 格式：wav、mp3、ogg、m4a、aac、pcm（pcm 仅支持 24k, 单通道）
- 大小：最大 10MB
- 时长：建议 10~60 秒
- 内容：清晰的人声，背景安静

### 2. 在控制台创建 speaker_id

访问火山引擎控制台 → 语音技术 → 音色库，创建音色获取 speaker_id。

### 3. 上传训练

```
# language 参数支持两种写法，效果相同：
#   数字: 0=中文 1=英文 2=日语 3=西班牙语 ...
#   代码: cn en ja es ...
/doubao_train /path/to/audio.wav S_your_speaker_id my_custom_voice 0    # 数字写法
/doubao_train /path/to/audio.wav S_your_speaker_id my_custom_voice cn   # 代码写法
```

> **语种参数注意**：豆包**训练接口**（voice_clone）的 `language` 官方用**数字**（0=中文, 1=英文, 2=日语...），本插件已做兼容，数字和字符串代码两种写法都可以填。但 **TTS 合成接口**（unidirectional）的 `explicit_language` 配置项**只支持字符串**（如 `zh-cn`、`en`、`ja`），不能填数字——两个接口的语种格式不同，详见下方对照表。

### 训练/合成语种对照表

| 语种 | 训练接口 `language`（数字/代码） | 合成接口 `explicit_language`（仅字符串） |
|------|--------------------------------|-----------------------------------------|
| 中文 | `0` 或 `cn` | `zh-cn`（中英混读） |
| 英文 | `1` 或 `en` | `en` |
| 日语 | `2` 或 `ja` | `ja` |
| 西班牙语 | `3` 或 `es` | `es-mx` |
| 印尼语 | `4` 或 `id` | `id` |
| 葡萄牙语 | `5` 或 `pt` | `pt-br` |
| 德语 | `6` 或 `de` | `de` |
| 法语 | `7` 或 `fr` | `fr` |
| 韩语 | `8` 或 `ko` | `ko` |
| 意大利语 | `9` 或 `it` | `it` |
| 泰语 | `10` 或 `th` | `th` |
| 越南语 | `11` 或 `vi` | `vi` |
| 俄语 | `12` 或 `ru` | `ru` |
| 菲律宾语 | `13` 或 `fil` | `fil` |
| 马来语 | `14` 或 `ms` | `ms` |
| 阿拉伯语 | `15` 或 `ar` | `ar` |
| 波兰语 | `19` 或 `pl` | `pl` |
| 土耳其语 | `20` 或 `tr` | `tr` |
| 瑞典语 | `21` 或 `sv` | `sv` |

## 完整工作流

```text
LLM 回复文本（full_text）
    │
    ▼
① 本地过滤器 ─── 纯本地 replace，零延迟
    │
    ▼
② 大模型过滤（可选）─── 调用 LLM API 智能过滤
    │
    ▼
③ 翻译（可选）─── 调用 LLM API 翻译为目标语种
    │  （以上步骤产出 filtered_text）
    ▼
④ 指令控制（可选，QA/Cot 二选一）
    │  · QA 模式：用 full_text 或 filtered_text 调大模型 → 返回指令句 → context_texts
    │  · Cot 模式：filtered_text 按分词切分 → 大模型逐片返回情感词 → 填入 <cot> 标签
    │
    ▼
⑤ 查 section_id_map ─── 按会话标识查上下文 ID（命中则传，未命中不传）
    │
    ▼
⑥ 豆包声音复刻 TTS ─── HTTP Chunked 流式合成（带 context_texts/use_tag_parser/section_id）
    │
    ▼
⑦ 保存音频 → 清理过期音频（可选）→ 发送语音 🔊
```

## 音频自动清理

| 配置值 | 行为 |
|--------|------|
| `-1` | 不自动清理（默认） |
| `60` | 仅保留最近 60 分钟的音频 |
| `1440` | 仅保留最近 24 小时的音频 |

### 合成响应

HTTP Chunked 流式返回，每个 chunk 为 JSON：
```json
{"code": 0, "data": "base64音频数据..."}
{"code": 20000000, "message": "ok"}  // 合成结束
```

## 注意事项

1. **音色训练**：首次调用合成接口即视为"转正"并收取音色槽位费，请先确认试听满意
2. **还原模式** (tone_fidelity)：仅支持合成与训练同语种的文本，不支持跨语种
3. **语种指定**：使用非中英文语种时，必须指定 `explicit_language`，且合成文本/训练音频需为对应语种
4. **API Key**：新版控制台只需 X-Api-Key，旧版控制台需 X-Api-App-Id + X-Api-Access-Key
5. **文本限制**：单次合成文本最大 3000 字符
6. **音频文件**：保存在 `data/doubao_tts/` 目录下
7. **依赖**：仅依赖 `httpx`（异步 HTTP 客户端）

## 参考文档

- [豆包声音复刻 API](https://docs.volcengine.com/docs/6561/2534906)
- [HTTP Chunked 单向流式合成](https://docs.volcengine.com/docs/6561/2528925)
- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
