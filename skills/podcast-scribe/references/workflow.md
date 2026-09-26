# 听稿安装与运行

首批目标环境为支持本地脚本执行的 Codex CLI / IDE，使用 Python 3.10+ 和 macOS / Linux 的 POSIX shell。Windows 可在 WSL 中采用同样的命令；原生 PowerShell 流程尚未验证。其他 Agent 可读取同一份 SKILL.md，但需自行确认其 Skill 发现机制和脚本执行权限。

## 安装与首次调用

推荐在任务工作区通过 [Skills CLI](https://github.com/vercel-labs/skills) 安装，需要 Node.js/npm：

```sh
npx skills@latest add aweng126/podcast-scribe --skill podcast-scribe -a codex
PS_SKILL_ROOT="$PWD/.agents/skills/podcast-scribe"
```

该命令从 GitHub 安装 Skill 及配套文件，无需本项目发布 npm 包。`npx` 不安装 Python 依赖。需要音频转写或 PDF 时继续初始化：

```sh
python3 -m venv "$PS_SKILL_ROOT/.venv"
PS_PYTHON="$PS_SKILL_ROOT/.venv/bin/python"
"$PS_PYTHON" -m pip install -e "$PS_SKILL_ROOT"
```

仅使用已有转写、Markdown 和阅读站时，可跳过环境初始化，设置 `PS_PYTHON=python3`，使用同一绝对脚本入口。

需要跨项目使用时，可给安装命令加 `-g`，并将 `PS_SKILL_ROOT` 设为安装器输出的 Skill 路径。更新或重新安装可能替换 Skill 目录，届时重新创建 Python 环境；文稿和缓存应始终保留在任务工作区。

也可不使用 npm，克隆仓库后将完整的 `skills/podcast-scribe/` 目录复制到 Codex 的用户 Skill 目录，不能只复制 SKILL.md。以下命令在目标不存在时复制文件，目标已存在时复用原安装：

```sh
PS_SKILL_ROOT="$HOME/.agents/skills/podcast-scribe"
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/aweng126/podcast-scribe.git podcast-scribe-source
if [ ! -e "$PS_SKILL_ROOT" ]; then
  cp -R podcast-scribe-source/skills/podcast-scribe "$PS_SKILL_ROOT"
fi
python3 -m venv "$PS_SKILL_ROOT/.venv"
PS_PYTHON="$PS_SKILL_ROOT/.venv/bin/python"
"$PS_PYTHON" -m pip install -e "$PS_SKILL_ROOT"
```

安装后在 Codex 的 Skill 列表中检查 `podcast-scribe`，没有显示时重启宿主。发现位置与显式调用方式见 [Codex 官方 Skill 文档](https://learn.chatgpt.com/docs/build-skills)。

**安装目录与任务目录分开。** 安装目录保存工具、参考文档及虚拟环境；任务目录保存音频、转写、历史和产物。项目安装后继续在当前工作区使用；全局安装时，也可将 `PS_WORKSPACE` 设为其他任务目录：

```sh
PS_WORKSPACE="$PWD"
mkdir -p "$PS_WORKSPACE"
cd "$PS_WORKSPACE"
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require import markdown site
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/demo.py" --formats markdown
```

打开当前工作区的 `output/demo/preview/index.html`，即可阅读自制演示并下载 Markdown。该演示不需要 API 或中文字体；首次用它验证安装和输出路径。要验证 PDF，按下节配置后运行 `demo.py --formats markdown pdf`。演示产物写入当前工作区的 `output/demo/`，也可通过 `--output-dir` 指定目录。

随后在以该工作区为当前目录的 Codex 会话中输入：

> 使用 $podcast-scribe，把本工作区的 input.json 整理为完整中文文稿，保留匿名说话人与时间戳，生成摘要、章节、Markdown 和本地草稿阅读站。输出保存在当前工作区。

处理真实音视频时将输入改为实际链接或本地路径，并说明所需输出。Agent 负责阅读完整转写、整理和校对，CLI 本身不另行调用文本模型润色。

新终端需要重新设置 `PS_SKILL_ROOT`、`PS_PYTHON`，并进入本次 `PS_WORKSPACE`。下面的命令均在**任务工作区**执行，工具入口使用绝对路径；所有相对输入、缓存、`data/`、`output/` 都基于当前目录。不要为了运行工具切换到 Skill 安装目录。移动安装目录后重建 `.venv` 并重新安装，避免可编辑安装及命令入口保留旧路径。

## 按需自检

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require import markdown site
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require pdf
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require transcribe
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require ingest markdown pdf
```

`doctor` 输出 JSON：`required` 为所选能力，`capabilities` 给出各项 `ready` 和 `issues`（含原因、修复建议），顶层 `ready` 表示所选本地前置条件是否全部满足。全部满足时退出码为 `0`，否则为 `2`；不带 `--require` 则检查全部能力。根据本次任务选择检查项，不要要求离线文稿流程具备云端密钥。

| 能力 | 本地前置条件 |
| --- | --- |
| `import` / `markdown` / `site` | Python 标准库；无需 API、字体或第三方包。 |
| `pdf` | `reportlab` 与可嵌入的中文 TrueType 字体。 |
| `inspect` | `yt-dlp`；自检不访问 B站。 |
| `transcribe` | `openai` SDK、`OPENAI_API_KEY` 与可执行的 ffmpeg。 |
| `ingest` | `transcribe` 的条件与 `yt-dlp`。 |

仅使用前三项时，可以用已有 Python 3.10+ 解释器直接运行绝对脚本入口，跳过依赖安装。完整安装包含 `reportlab`、`openai`、`yt-dlp` 和提供内置 ffmpeg 的 `imageio-ffmpeg`。自检会尝试运行 ffmpeg 的版本命令，并对 PDF 实际检查字体能否加载。

PDF 自动探测常见系统字体。若报告缺少中文字体，提供有相应字形的中文 `.ttf` 或 TrueType `.ttc` 文件：

```sh
export PODCAST_SCRIBE_FONT='/absolute/path/to/chinese-font.ttf'
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" doctor --require pdf
```

按实际字体路径替换示例；不是所有 `.ttc` / OpenType 字体都可供 ReportLab 嵌入。`doctor` 成功后仍须检查最终 PDF 的分页、中文和段落完整性。

网络转写需通过运行环境或宿主的密钥设置提供 `OPENAI_API_KEY`，不要把密钥粘贴到聊天、文稿、日志或 Git。`doctor` 不联网、不发 API 请求、不验证账户额度和模型权限；`transcribe` / `ingest` 的本地检查成功不保证远端服务可用。

## 单集生产

B站单集：

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" inspect 'https://www.bilibili.com/video/BV1GZbT6UE7o' --output output/source.json
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" ingest 'https://www.bilibili.com/video/BV1GZbT6UE7o' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
```

本地音视频或已有转写（二选一）：

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" transcribe '/path/to/episode.mp4' --id my-episode --title '本期标题' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" import '/path/to/transcript.json' --id my-episode --title '本期标题' --series-id my-series --series-title '系列名称' --output data/my-episode/episode.json
```

新导入不会覆盖已存在的单集；已有稿件使用 `edit`，重新转写则指定新路径。默认音频缓存位于任务工作区的 `data/cache/`，可用 `--cache` 明确指定。

`ingest/transcribe` 会将音频发送到配置的 OpenAI endpoint，并产生接口费用。现用 `gpt-4o-transcribe-diarize`、`diarized_json`、`chunking_strategy=auto`。音频转换为单声道 16 kHz、32 kbps MP3；长音频自动分片，每片单独检查上传大小，成功结果缓存到本地。中断后重跑相同命令与缓存目录可复用成功分片。跨片人物通过匿名参考声源辅助对应，无法确认的标签需继续校对；具体边界、缓存规则与限制见 [长音视频](long-audio.md)。

B站普通网页提取失败时，程序尝试正常公开元数据与播放 API，核验所选分 P、权限/预览标记和时长；音频主地址失败后最多使用该音轨响应提供的两个备用地址。平台仍可能限制公开 API，因此不能保证每次都可获取。已下载音频和成功转写应复用。

`import` 只解析已有文件，不需要 API，不会猜人物姓名。CLI 生成原始草稿后，由 Skill 所在 Agent 按 [分批整理](editing.md) 读取必要文本，按 [数据约定](schema.md) 生成增量 edits JSON；不需要把完整 JSON、原始响应和历史反复放入模型上下文。

## 校对、导出与阅读站

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" edit data/my-episode/episode.json --edits output/editorial.json
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" validate data/my-episode/episode.json
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" export data/my-episode/episode.json --formats markdown pdf --output-dir output/exports
```

需要离线阅读页面时，再运行：

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" site data --preview --output-dir output/preview
```

仅要 Markdown 时传 `--formats markdown`。直接打开 `output/preview/index.html`，即可离线阅读、搜索、按章节定位并下载文件。第一次只有草稿时，正式站点为空是预期行为。

段落整理完成记为 `edited`，疑点记为 `needs_review`，实际核对后才设为 `reviewed`。全部段落为 `reviewed` 后，才能按 schema 更新整集 `review` 字段；若仍有未知说话人，则不能确认整集人物归属。修改正文或归属会重置对应段落状态，普通编辑也会清空整集复核标记。旧稿件出现全局与逐段状态冲突时，通过 `edit` 退回草稿并保留历史，见 [校对状态与兼容修复](schema.md#校对状态)。

社区分享采用独立的 [投稿流程](sharing.md)，不需要先运行 `publish`。只有准备自行部署完整站点时，完成校对且用户有明确公开意图后运行：

```sh
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" publish data/my-episode/episode.json
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" export data/my-episode/episode.json --formats markdown pdf --output-dir output/exports
"$PS_PYTHON" "$PS_SKILL_ROOT/scripts/podcast_scribe.py" site data --output-dir output/site
```

只有 `output/site/` 是准备部署的静态目录。不要上传 `data/`、缓存、转写原始响应、诊断、编辑历史或 `.venv`。草稿预览与正式构建使用不同输出目录。`publish` 不执行部署、购域名或更改外部账户。

第一版管理入口是 Skill + CLI；尚无网页编辑后台、自动订阅、多用户账户。时间戳链接返回原视频，未实现内嵌同步播放器。画面 OCR、人物人脸识别、截图提取不在第一版范围。

## 官方接口依据

- [yt-dlp 支持站点及兼容性说明](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)
- [yt-dlp Bilibili 提取器](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/bilibili.py)
- [OpenAI 文件转写与说话人分离](https://developers.openai.com/api/docs/guides/speech-to-text)
