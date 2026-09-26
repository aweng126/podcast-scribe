# 听稿 · Podcast Scribe

用于整理播客与访谈的 LLM Skill，保留完整对话、说话人和时间戳，生成摘要与章节。

## 功能

- 支持 Bilibili 单集链接、本地音视频及 JSON/SRT/VTT 转写。
- 整理完整文稿，保留原始转写与编辑历史。
- 导出 Markdown、中文 PDF 和可离线阅读的静态站点。

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

仅导入已有转写并导出 Markdown / 阅读站时无需第三方 Python 依赖。全局安装与手动安装见[安装与运行](references/workflow.md#安装与首次调用)。

- **音视频转写**：需要在运行环境中配置 `OPENAI_API_KEY`，音频会发送到 OpenAI API，并产生接口费用。导入已有转写无需 API。
- **中文 PDF**：需要可嵌入的中文 TrueType 字体，可通过 `PODCAST_SCRIBE_FONT` 指定字体路径。

## 使用

在保存文稿的任务工作区中启动 Codex，通过 `$podcast-scribe` 调用：

```text
使用 $podcast-scribe 将这个 B站视频整理为完整文稿，生成摘要和章节，导出 Markdown 和 PDF：<视频链接>
```

或使用已有转写：

```text
使用 $podcast-scribe 整理 ./input.json，保留说话人与时间戳，生成摘要、章节、Markdown 和本地阅读站预览。
```

音频缓存、文稿与导出文件保存在任务工作区的 `data/` 和 `output/`，也可指定输出路径。

## 文档

- [Skill 指令](SKILL.md)
- [安装与运行](references/workflow.md)
- [数据格式与校对约定](references/schema.md)
