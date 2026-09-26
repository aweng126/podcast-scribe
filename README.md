# 听稿 · Podcast Scribe

将播客与访谈整理成可阅读、可检索、可导出的完整文稿。

支持 B站单集或本地音视频，保留说话人、时间戳、摘要与章节；从同一份 JSON 导出 Markdown、中文 PDF 和静态播客系列阅读站。仓库与 Skill 名称为 `podcast-scribe`，Python 包为 `podcast_scribe`。

## 当前版本：v0.1 预览版

- Skill 入口：[SKILL.md](SKILL.md)。整个仓库就是 skill 目录。
- 内容生产：Skill + Python CLI；人物重命名、逐段修订、编辑历史、草稿/发布状态。
- 输入：B站单集链接、本地音视频、JSON/SRT/VTT 转写。
- 转写：可选 OpenAI 说话人分离接口；本地转写文件导入不需要 API。
- 输出：Markdown、PDF、可离线打开的系列首页/系列详情/单集阅读页，含全文搜索与文件下载。

第一版没有网页编辑后台、自动订阅、多用户账户或自动公网部署。CLI 导入/转写生成原始草稿，由 Skill 的 agent 阅读全文、整理内容并生成摘要章节。结构验证不能替代语义校对。

## 安装为 Skill

首批目标为支持本地脚本执行的 Codex CLI / IDE，使用 Python 3.10+ 和 macOS / Linux shell。将**整个仓库**放入用户的 `~/.agents/skills/podcast-scribe/`，或某个项目的 `.agents/skills/podcast-scribe/`，即可供宿主发现。不能只复制 SKILL.md；其他 Agent 的发现方式需另行确认。

完整克隆、安装、首次调用及字体配置命令见 [安装与运行](references/workflow.md#安装与首次调用)。安装后可在任务工作区向 Agent 输入：

> 使用 $podcast-scribe，把 input.json 整理为完整中文文稿，保留匿名说话人与时间戳，生成摘要、章节、Markdown 和本地草稿阅读站。输出保存在当前工作区。

## 在当前克隆中快速体验

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/podcast-scribe doctor --require import markdown site
.venv/bin/python scripts/demo.py --formats markdown
```

打开当前工作区的 `output/demo/preview/index.html`，阅读自制功能演示并下载 Markdown。演示不是任何真实节目的转写，没有对应音视频；它不会覆盖真实节目的 `output/preview/` 或正式站点。需要 PDF 时先运行 `doctor --require pdf`，再运行 `scripts/demo.py --formats markdown pdf`。

安装为全局 Skill 后，应从**任务工作区**调用安装目录中的绝对脚本入口，音频、缓存和文稿均留在任务工作区；演示脚本也遵守该约定，并支持 `--output-dir`。已有转写的导入、Markdown 与阅读站只需 Python 标准库，可以直接使用 `python3 scripts/podcast_scribe.py`；音频转写与 PDF 需要相应依赖。

详细安装、真实输入、校对与导出命令见 [工作流](references/workflow.md)；编辑数据格式见 [数据约定](references/schema.md)。

## 本次示例验证

2026-09-26 已实际处理 `BV1GZbT6UE7o`《每个压力大的人都值得反复阅读何超琼的这段话》：普通网页受限，但公开播放 API 及其备用音频地址成功提供完整音轨（144.637 秒）。已完成两位说话人的 20 个转写切片，按连续发言合并展示为 7 段对话，并用独立文字转写结果交叉核对，生成摘要、章节及整理稿。

依据《小天章》官方播客主页与 EP5 单集说明核实：采访者为章泽天，受访者为何超琼。本稿只覆盖用户提供的约 2 分 25 秒剪辑片段。少量词句与交叠发言标为待核，保持草稿状态。

- 真实节目预览：`output/preview/index.html`（项目更名后，从新目录重新打开）。
- 可编辑数据与原始音轨：`data/BV1GZbT6UE7o/`。
- 真实 Markdown/PDF：`output/exports/BV1GZbT6UE7o.md` 与 `.pdf`。
- 成功获取诊断：`output/diagnostics/public-playback-check.json`；先前受限诊断继续保留，作为历史记录。

公开接口仍可能间歇性限流；已经取得的音频与转写保存在本地，不依赖再次获取。网页原型不是最终校对稿，尚未部署到公网。

## Git 保存范围

仓库保存 Skill、Python 源码、页面资源、测试、文档和 `examples/demo/` 中的自制演示数据。`.gitignore` 排除虚拟环境、密钥配置、缓存、构建产物，以及 `data/`、`output/`、`tmp/`。

上面的真实节目数据和预览路径属于本机生成结果，不随 Git 提交或 GitHub 上传保存；新克隆的目录需要按工作流生成。若需备份这些真实素材与文稿，应另外备份上述目录。

## 开发检查

移动项目目录时，建议在新目录重新创建虚拟环境，再运行 `.venv/bin/python -m pip install -e '.[test]'`，以更新可编辑安装路径和命令入口。仅修改包名时，重新安装即可。当前版本尚未发布，不保留旧命令或旧包名别名。

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
.venv/bin/podcast-scribe doctor --require import markdown site pdf
node --check podcast_scribe/assets/app.js
```

`doctor --require ...` 只检查本次需要的能力，缺少所需前置条件返回非零并提供修复建议；不带参数检查全部能力。它不联网，云端转写检查仅表示本地前置满足，不保证账户或模型可用。

PDF 使用可嵌入中文 TrueType 字体，可用 `PODCAST_SCRIBE_FONT` 配置。完整测试需要中文字体和 Node.js；测试依赖已包含 PDF 校验使用的 `pypdf`。API 密钥仅从运行环境读取，不应保存到仓库、文稿或站点。公开部署只使用 `output/site/`；音频缓存、原始转写、草稿预览与编辑历史保留在本地。

整集内容校对完成要求每个段落均为 `reviewed`；修改正文或说话人归属后需要重新核对。旧稿若存在全局/逐段状态冲突，可通过 `edit` 保留历史并退回草稿，详见 [校对状态约定](references/schema.md#校对状态)。
