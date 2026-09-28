# 听稿 · Podcast Scribe

将播客与访谈整理为完整文稿的 Agent Skill。支持 Bilibili 单集链接、本地音视频和 JSON/SRT/VTT 转写，保留说话人与时间戳，生成摘要、章节、Markdown 和中文 PDF。

[社区阅读站](https://aweng126.github.io/podcast-scribe/)

[![听稿社区阅读站首页](docs/images/community-reader.jpg)](https://aweng126.github.io/podcast-scribe/)

## 安装

适用于 macOS / Linux 上可执行本地脚本的 Codex CLI / IDE。需要 Node.js/npm 和 Python 3.10+，在任务工作区执行：

```sh
npx skills@latest add aweng126/podcast-scribe --skill podcast-scribe -a codex
```

首次运行由 Agent 检查并按需安装运行依赖。使用合格字幕或导入已有转写无需转写 API；需要音频转写时才需配置 `OPENAI_API_KEY`，发送音频到 OpenAI API 并产生接口费用。

## 使用

在同一工作区启动 Codex，只需提供 Skill 名称和链接：

```text
使用 $podcast-scribe https://www.bilibili.com/video/BV1XNtJ6UEmm
```

也可将链接换成本地文件路径，如 `./episode.mp3` 或 `./input.srt`。

B站默认优先使用字幕，少量缺口局部补齐，否则转写音频；自动整理后交付本地 Markdown/PDF，输出位于 `output/<id>/`。转写与分批整理均支持中断续接，无需逐条手动核对。

需要逐项核验时加上“使用精准模式”；需要离线页面时加上“生成本地阅读页”。

## 分享（可选）

交付后会邀请分享。愿意分享时提供公开署名，Skill 生成投稿 JSON 和预填链接，由你在 GitHub 网页上传提交，维护者审核后收录到社区阅读站；不会自动上传。

投稿内容公开，完整流程见[分享、修改与撤稿](skills/podcast-scribe/references/sharing.md)。

## 文档

- [Skill 指令](skills/podcast-scribe/SKILL.md)
- [安装与运行](skills/podcast-scribe/references/workflow.md)
- [社区投稿与部署](docs/community.md)
- [开发与验收](docs/development.md)
