# 按需语音回复 `tts_on_demand`

一个 AstrBot 插件：**平时只发文字，只有你明确要求时才回复语音。**

## 解决什么问题

AstrBot 的 TTS 是全局开关。一旦打开，机器人**每条回复都带语音**——刷屏、耗算力、也不方便快速浏览文字。

这个插件把语音变成「按需」：

- 默认状态：纯文字
- 消息里出现触发词（如「语音」「读出来」）：这一条回复才带语音
- 下一条不带触发词的消息：自动回到纯文字

## 工作原理

AstrBot 判定是否合成语音时会调用 `SessionServiceManager.should_process_tts_request()`，它读取本会话的 `tts_enabled`。

本插件通过 `@filter.on_llm_request()` 钩子，在**每次 LLM 请求前**根据当前消息内容把 `tts_enabled` 写成 `True` / `False`：

```python
@filter.on_llm_request()
async def mark_tts(self, event, *_args, **_kwargs):
    hit, reason = self.match(event.message_str or "")
    await self.apply(event, hit)
```

所以是**逐条生效**的，不需要手动开关，也不会污染下一条消息。

## 安装

### 方式一：插件市场

AstrBot WebUI → 插件市场 → 搜索「按需语音」→ 安装。

### 方式二：手动

```bash
cd AstrBot/data/plugins
git clone https://github.com/lumia-li/astrbot_plugin_tts_on_demand astrbot_plugin_tts_on_demand
```

然后在 WebUI 里重载插件。

## 配置

WebUI → 插件管理 → **按需语音回复** → 配置。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| 启用按需语音 | 开关 | 开 | 总开关。关闭后所有回复只发文字 |
| 触发词列表 | 列表 | 语音 / 发语音 / 说给我听 / 读出来 / 念出来 / 朗读 / 用声音 | 出现任意一个词即触发；留空用默认词表 |
| 正则匹配（可选） | 文本 | 空 | 额外支持正则，如 `语音\|读一下\|念给.{0,4}听` |
| 区分大小写 | 开关 | 关 | 仅影响英文触发词和正则 |
| 命中时输出日志 | 开关 | 开 | 便于排查某条消息为何没发语音 |

配置文件位置：`data/config/astrbot_plugin_tts_on_demand_config.json`

## 使用示例

```
你：在干嘛
Bot：文字回复                      ← 没触发词，纯文字

你：用语音夸夸我
Bot：文字 + 语音                    ← 命中「语音」

你：晚安
Bot：文字回复                      ← 自动回到纯文字
```

自定义触发词后：

```yaml
trigger_words:
  - 读一下
  - 念念
  - 语音条
```

## 依赖说明

- AstrBot `>= 4.0.0`
- 使用到核心模块 `astrbot.core.star.session_llm_manager.SessionServiceManager`（AstrBot 4.x 内置，无需额外安装）
- 与具体的 TTS 提供商无关：Edge TTS、GPT-SoVITS、Fish Audio 等都可用
- 需要 TTS 的 `dual_output` 处于开启状态，才会「文字 + 语音」一起发；关闭则只发语音

## 常见问题

**Q：命中触发词了但没语音？**
A：按顺序检查：① WebUI 里 TTS 是否启用且 provider 正常；② 插件配置里「启用按需语音」是否开着；③ 看日志有没有 `TTS request` / `TTS failed`。

**Q：能不能改成命令开关（如 `/语音`）？**
A：可以，把 `mark_tts` 里的判定换成命令监听即可，欢迎提 PR。

**Q：会不会影响其他会话？**
A：不会，`tts_enabled` 是按会话（umo）隔离的。

## 许可证

MIT
