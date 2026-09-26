# 听稿 · Podcast Scribe

用于整理播客与访谈的 LLM Skill，保留完整对话、说话人和时间戳，生成摘要与章节。

[社区阅读站](https://aweng126.github.io/podcast-scribe/)

## 功能

- 支持 Bilibili 单集链接、本地音视频及 JSON/SRT/VTT 转写。
- 整理完整文稿，保留原始转写与编辑历史。
- 导出本地 Markdown 与中文 PDF，可选生成离线阅读页面。
- 完成后邀请分享，用户同意后准备投稿材料，审核后收录到公共阅读站。

## 安装

需要 Node.js/npm 和 Python 3.10+，以下命令适用于 macOS / Linux 上可执行本地脚本的 Codex CLI / IDE。

在任务工作区使用 [Skills CLI](https://github.com/vercel-labs/skills) 安装：

```sh
npx skills@latest add aweng126/podcast-scribe --skill podcast-scribe -a codex
```

Skill 安装到当前项目的 `.agents/skills/podcast-scribe/`。首次使用音频转写或 PDF 时，初始化 Python 运行环境：

```sh
PS_SKILL_ROOT="$PWD/.agents/skills/podcast-scribe"
python3 -m venv "$PS_SKILL_ROOT/.venv"
"$PS_SKILL_ROOT/.venv/bin/python" -m pip install -e "$PS_SKILL_ROOT"
```

仅处理已有转写、导出 Markdown、生成离线页面和分享文件时，无需第三方 Python 依赖。全局安装与手动安装见[安装与运行](skills/podcast-scribe/references/workflow.md#安装与首次调用)。

- **音视频转写**：需要在运行环境中配置 `OPENAI_API_KEY`，音频会发送到 OpenAI API，并产生接口费用。导入已有转写无需 API。
- **中文 PDF**：需要可嵌入的中文 TrueType 字体，可通过 `PODCAST_SCRIBE_FONT` 指定字体路径。

## 使用

在保存文稿的任务工作区中启动 Codex，通过 `$podcast-scribe` 调用：

```text
使用 $podcast-scribe 将这个 B站视频整理为完整文稿，生成摘要和章节，导出 Markdown 和 PDF：<视频链接>
```

或使用已有转写：

```text
使用 $podcast-scribe 整理 ./input.json，原节目链接为 <来源链接>。
保留完整对话、说话人与时间戳，生成摘要、章节和 Markdown。
```

音频缓存、文稿与导出文件保存在任务工作区的 `data/` 和 `output/`，也可指定输出路径。需要离线阅读页面时，另外提出“生成本地阅读页预览”。

## 分享（可选）

每次转录或文稿整理完成并交付本地文件后，Agent 会在对话中邀请分享一次：

> 是否愿意将这篇文稿分享到 Podcast Scribe 社区阅读站？可以仅保存在本地；如果愿意，请告诉我公开署名。

1. 回复“仅保存在本地”，或“愿意分享，署名为「我的名字」”。不回复时保持本地；已明确不分享时不重复邀请。
2. 同意分享并完成校对后，Skill 生成公开 JSON 和预填 Issue 链接。
3. 用户检查文件，登录 GitHub 上传并提交 Issue；机器人校验并创建内容 PR，维护者审核合并后由 GitHub Pages 自动部署。

尚未校对的稿件需先完成校对。生成分享文件不会自动上传；公开仓库的 Issue 和附件在收录审核前就可被他人访问，附件上传前需检查内容。

也可主动要求分享。完整步骤见[分享、修改与撤稿指引](skills/podcast-scribe/references/sharing.md)。需要撤稿时在原 Issue 联系维护者，移除内容并部署成功后下线。

## 文档

- [Skill 指令](skills/podcast-scribe/SKILL.md)
- [安装与运行](skills/podcast-scribe/references/workflow.md)
- [数据格式与校对约定](skills/podcast-scribe/references/schema.md)
- [社区投稿与部署](docs/community.md)
- [开发与验收](docs/development.md)
