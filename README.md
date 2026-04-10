# Meeting Copilot MVP

一个面向 Windows 11 的会议辅助工具原型：

- 实时抓取系统播放音频，也就是 Teams / Google Meet / Zoom 里你耳机正在听到的内容
- 用 OpenAI Realtime API 做英文实时转写
- 用文本模型把会议内容增量翻译成中文
- 用桌面悬浮窗显示英文和中文
- 会议结束后生成中文摘要、行动项和建议追问

这个版本优先做成一个可运行的 MVP，而不是一次性把所有功能都堆满。

## 当前能力

- 默认采集 Windows 默认输出设备的 WASAPI loopback 音频
- 使用 `gpt-4o-mini-transcribe` 做低延迟转写
- 使用 `gpt-4.1-nano` 做更快的中文翻译，`gpt-5.4-mini` 做会议总结
- `Live Chinese Preview` 会先给出可被修正的实时预览，`Stable Chinese Timeline` 再记录更稳定的正式字幕
- 始终置顶窗口，适合开会时放在旁边
- 支持清空、停止、生成摘要

## 运行方式

### 方案 A：`.venv`

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org --index-url https://pypi.org/simple -r requirements.txt
Copy-Item .env.example .env
python main.py
```

### 方案 B：`conda`

```powershell
conda env create -f environment.yml
conda activate meeting-copilot
Copy-Item .env.example .env
python main.py
```

如果你的公司网络会拦截证书，`pip` 可能需要额外加 `--trusted-host`。

## 配置

先复制配置模板：

```powershell
Copy-Item .env.example .env
```

然后至少填写：

```env
OPENAI_API_KEY=your_key_here
```

可选项：

- `TRANSCRIPTION_MODEL`：默认 `gpt-4o-mini-transcribe`，更省钱更快
- `TRANSLATION_MODEL`：默认 `gpt-4.1-nano`，优先低延迟
- `SUMMARY_MODEL`：默认 `gpt-5.4-mini`
- `LOOPBACK_DEVICE_INDEX`：如果默认输出设备不是你开会的设备，可以手动指定

如果你想先看看当前机器有哪些 loopback 设备：

```powershell
python main.py --list-devices
```

## 项目结构

```text
main.py
meeting_copilot/
  audio.py
  config.py
  controller.py
  llm.py
  models.py
  realtime.py
  ui.py
```

## 这版的取舍

- 先做“稳定按句翻译”，而不是逐词翻译，减少字幕抖动
- 先默认只采集系统播放音频，不把你的麦克风发言混进来
- 先使用 PySide6 做桌面窗体，后面如果要做系统托盘、全局热键、自动导出，再往上加
- 当前按 Realtime 回来的完成事件顺序展示，后续可以补更严格的 turn 排序

## 下一步建议

1. 增加麦克风通道，把“别人说的话”和“我说的话”分成双轨。
2. 做术语表和公司名词词典，降低缩写误识别。
3. 增加“帮我想追问”面板。
4. 做会议结束后的 Markdown 导出。
5. 做系统托盘、全局快捷键和自动复制最近 5 分钟内容。

## 官方参考

- OpenAI Realtime transcription: <https://developers.openai.com/api/docs/guides/realtime-transcription>
- OpenAI Realtime WebSocket: <https://developers.openai.com/api/docs/guides/realtime-websocket>
- OpenAI Models: <https://developers.openai.com/api/docs/models>
- Microsoft WASAPI loopback recording: <https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording>
