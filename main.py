"""按需语音回复插件。

功能一：平时只发文字，只有“这条消息”命中触发词或正则时才回复语音。
功能二：语音只读一部分（首句 / 前 N 字），文字仍然是完整内容。

实现：
1. 在每次 LLM 请求前，按当前消息内容把本会话的 tts_enabled 置为 True/False，
   ResultDecorateStage 里的 TTS 判定随后读取这个值。
2. 给当前 TTS provider 的 get_audio 包一层，只截短“送去合成”的文本，
   不改动真正发给用户的消息链，所以文字仍是完整的。
"""

import re
from typing import Any, Mapping

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.session_llm_manager import SessionServiceManager

PLUGIN_NAME = "astrbot_plugin_tts_on_demand"
PLUGIN_VERSION = "0.0.2"
PLUGIN_DESC = "平时纯文字，命中触发词的那条消息才回复语音；语音可只读首句或前 N 字"

DEFAULT_TRIGGER_WORDS = [
    "语音",
    "发语音",
    "说给我听",
    "读出来",
    "念出来",
    "朗读",
    "用声音",
]

MODE_FIRST_SENTENCE = "只读第一句"
MODE_FIRST_CHARS = "只读前 N 字"

SENTENCE_END = "。！？!?…\n"
SOFT_BREAKS = "，、,;；"


def first_sentence(text: str) -> str:
    for i, ch in enumerate(text):
        if ch in SENTENCE_END:
            return text[: i + 1]
    return text


def head_chars(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    # 尽量在标点处收尾，避免读半个词
    best = -1
    for p in SENTENCE_END + SOFT_BREAKS:
        idx = cut.rfind(p)
        if idx > best:
            best = idx
    if best >= max(1, limit // 2):
        return cut[: best + 1]
    return cut


@register(PLUGIN_NAME, "liyueovo", PLUGIN_DESC, PLUGIN_VERSION)
class TTSOnDemand(Star):
    def __init__(self, context: Context, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(context)
        self.config: Mapping[str, Any] = config if config is not None else {}
        self._patched_providers: set[int] = set()
        self._last_dup_signature: str = ""

    # ------------------------------------------------------------ 配置读取

    def _enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _case_sensitive(self) -> bool:
        return bool(self.config.get("case_sensitive", False))

    def _trigger_words(self) -> list[str]:
        words = self.config.get("trigger_words")
        if not isinstance(words, list) or not words:
            words = DEFAULT_TRIGGER_WORDS

        case_sensitive = self._case_sensitive()
        seen: set[str] = set()
        unique: list[str] = []
        duplicates: list[str] = []

        for item in words:
            word = str(item).strip()
            if not word:
                continue
            key = word if case_sensitive else word.lower()
            if key in seen:
                duplicates.append(word)
                continue
            seen.add(key)
            unique.append(word)

        if duplicates:
            signature = "|".join(duplicates)
            if signature != self._last_dup_signature:
                self._last_dup_signature = signature
                logger.warning(
                    f"[{PLUGIN_NAME}] 触发词存在重复（已自动去重，不影响使用）："
                    f"{'、'.join(duplicates)}。建议在 WebUI 里删掉多余项。"
                )

        return unique

    def _regex_pattern(self) -> str:
        return str(self.config.get("regex_pattern") or "").strip()

    # ------------------------------------------------------------ 触发判定

    def match(self, text: str) -> tuple[bool, str]:
        if not text:
            return False, ""
        hay = text if self._case_sensitive() else text.lower()

        for word in self._trigger_words():
            needle = word if self._case_sensitive() else word.lower()
            if needle in hay:
                return True, f"关键词「{word}」"

        pattern = self._regex_pattern()
        if pattern:
            flags = 0 if self._case_sensitive() else re.IGNORECASE
            try:
                if re.search(pattern, text, flags):
                    return True, f"正则 /{pattern}/"
            except re.error as e:
                logger.warning(f"[{PLUGIN_NAME}] 正则无效，已忽略：{pattern}（{e}）")
        return False, ""

    # ------------------------------------------------------------ 语音长度限定

    def trim_for_speech(self, text: str) -> str:
        if not bool(self.config.get("speech_limit_enabled", False)):
            return text

        mode = str(self.config.get("speech_limit_mode") or MODE_FIRST_SENTENCE)
        try:
            limit = int(self.config.get("speech_limit_chars", 60) or 60)
        except (TypeError, ValueError):
            limit = 60

        cleaned = (text or "").strip()
        if not cleaned:
            return text

        if mode == MODE_FIRST_CHARS:
            out = head_chars(cleaned, limit)
        else:
            out = first_sentence(cleaned)
            if limit > 0 and len(out) > limit:
                out = head_chars(out, limit)

        return out or text

    def patch_provider(self, provider) -> None:
        """给 provider 的 get_audio 包一层，只截短送进 TTS 的文本。"""
        if provider is None:
            return
        key = id(provider)
        if key in self._patched_providers:
            return
        original = getattr(provider, "get_audio", None)
        if original is None:
            return

        async def get_audio_limited(text: str) -> str:
            try:
                trimmed = self.trim_for_speech(text)
            except Exception:  # noqa: BLE001
                trimmed = text
            if trimmed != text and bool(self.config.get("speech_limit_log", False)):
                logger.info(
                    f"[{PLUGIN_NAME}] 语音截断：{len(text)} 字 -> {len(trimmed)} 字"
                )
            return await original(trimmed)

        provider.get_audio = get_audio_limited
        self._patched_providers.add(key)

    # ------------------------------------------------------------ 钩子

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, *_args, **_kwargs):
        # 1) 语音长度限定：包一层 provider
        if bool(self.config.get("speech_limit_enabled", False)):
            try:
                provider = await self.context.get_using_tts_provider_async(
                    event.unified_msg_origin
                )
                self.patch_provider(provider)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{PLUGIN_NAME}] 获取 TTS provider 失败：{e}")

        # 2) 按需开关：决定这一条是否带语音
        if not self._enabled():
            await self._apply(event, False)
            return

        text = (event.message_str or "").strip()
        hit, reason = self.match(text)
        await self._apply(event, hit)

        if hit and bool(self.config.get("log_hit", True)):
            logger.info(f"[{PLUGIN_NAME}] 本条消息启用语音（{reason}）：{text[:40]}")

    async def _apply(self, event: AstrMessageEvent, enabled: bool) -> None:
        try:
            await SessionServiceManager.set_tts_status_for_session(
                event.unified_msg_origin, enabled
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{PLUGIN_NAME}] 设置 TTS 状态失败：{e}")
