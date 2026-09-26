# 听稿 · Podcast Scribe

用于整理播客与访谈的 LLM Skill，保留完整对话、说话人和时间戳，生成摘要与章节。

[社区阅读站](https://aweng126.github.io/podcast-scribe/)

## 功能

- 支持 Bilibili 单集链接、本地音视频及 JSON/SRT/VTT 转写；长音视频自动分片，支持缓存续跑。
- 按批整理完整文稿，只读取所需文本、提交增量修改，保留原始转写与编辑历史。
- 导出本地 Markdown 与中文 PDF，可选生成离线阅读页面。
- 完成后邀请分享，用户同意后准备投稿材料，审核后收录到公共阅读站。

## 安装

需要 Node.js/npm 和 Python 3.10+，以下命令适用于 macOS / Linux 上可执行本地脚本的 Codex CLI / IDE。

在任务工作区使用 [Skills CLI](https://github.com/vercel-labs/skills) 安装：

```sh
npx skills@latest add aweng126/podcast-scribe --skill podcast-scribe -a codex
```

Skill 安装到当前项目的 `.agents/skills/podcast-scribe/`。首次运行时，Agent 会检查并按需初始化 Python 依赖，之后复用已有环境；无需每次手动选择解释器。

仅处理已有转写、导出 Markdown、生成离线页面和分享文件时，无需第三方 Python 依赖。源码仓库中可直接使用；全局安装与手动安装见[安装与运行](skills/podcast-scribe/references/workflow.md#安装与首次调用)。

- **音视频转写**：需要在运行环境中配置 `OPENAI_API_KEY`，音频会发送到 OpenAI API，并产生接口费用。导入已有转写无需 API。
- **中文 PDF**：需要可嵌入的中文 TrueType 字体，可通过 `PODCAST_SCRIBE_FONT` 指定字体路径。

## 使用

在保存文稿的任务工作区中启动 Codex，只需提供 Skill 名称和链接：

```text
使用 $podcast-scribe https://www.bilibili.com/video/BV1XNtJ6UEmm
```

或使用已有转写：

```text
使用 $podcast-scribe ./input.json
```

默认按中文阅读习惯整理并保留原意，保留完整对话、说话人与时间戳，生成摘要、章节、Markdown 和 PDF。文稿保存在 `data/<id>/episode.json`，导出文件保存在 `output/<id>/`；已有同一输入的文稿会继续整理，未完成的校对会如实标注。

无需另外指定格式、路径、分片或解释器。只有需要调整默认行为时才补充要求，例如“只导出 Markdown”或“生成本地阅读页预览”。

四小时等长节目使用相同入口，失败后可复用成功分片继续处理；跨片说话人对应仍须校对。机制与限制见[长音视频](skills/podcast-scribe/references/long-audio.md)，减少上下文开销的流程见[分批整理](skills/podcast-scribe/references/editing.md)。

B站访问受限时，可先在浏览器正常登录，再授权使用该浏览器的登录态重试，具体见[访问受限时的处理](skills/podcast-scribe/references/workflow.md#b站访问受限时)。

## 分享（可选）

每次转录或文稿整理完成并交付本地文件后，Agent 会在对话中邀请分享一次：

> 是否愿意将这篇文稿分享到 Podcast Scribe 社区阅读站？可以仅保存在本地；如果愿意，请告诉我公开署名。

1. 回复“仅保存在本地”，或“愿意分享，署名为「我的名字」”。不回复时保持本地；已明确不分享时不重复邀请。
2. 同意分享并完成校对后，Skill 生成公开 JSON 和预填 Issue 链接。
3. 用户检查文件，登录 GitHub 上传并提交 Issue；机器人校验并创建内容 PR，维护者审核合并后由 GitHub Pages 自动部署。单稿上限为 512 MiB；超过 Issue 附件限制时，使用公开 GitHub Release 文件链接投稿。

尚未校对的稿件需先完成校对。生成分享文件不会自动上传；公开仓库的 Issue 和附件在收录审核前就可被他人访问，附件上传前需检查内容。

也可主动要求分享。完整步骤见[分享、修改与撤稿指引](skills/podcast-scribe/references/sharing.md)。需要撤稿时在原 Issue 联系维护者，移除内容并部署成功后下线。

## 文档

- [Skill 指令](skills/podcast-scribe/SKILL.md)
- [安装与运行](skills/podcast-scribe/references/workflow.md)
- [数据格式与校对约定](skills/podcast-scribe/references/schema.md)
- [社区投稿与部署](docs/community.md)
- [开发与验收](docs/development.md)
