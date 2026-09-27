# 单集与编辑格式

`schema_version: 1`，每集 JSON 是唯一内容来源。完整例子见 `examples/demo/episode.json`（明确的自制演示，不来自 B站视频）。

核心字段：

- `id`：稳定 ASCII 标识；不要用标题变化来改变它。
- `title / description / published_at / duration_seconds`：节目元数据。
- `source: {url, platform, video_id, author}`：原作者与来源。
- `series: {id, title, description}`：产品中的播客系列，独立于 UP 主和平台合集。
- `speakers: [{id, name, role}]`：当前单集内的人物映射。导入标签原样保留在可选 `source_label`，不当作已验证姓名。
- `segments: [{id, start, end, speaker_id, raw_text, text, review_status}]`：秒级时间、匿名人物、不可变原始转写、整理稿；未知人物为 `null`，允许说话时间重叠。
- `summary: [string]` 与 `chapters: [{id, title, start, segment_id}]`：摘要及有真实段落锚点的章节。
- `status: draft | published`、`review: {speakers_confirmed, content_checked}`、`revision`。
- `references: [{title, url, note}]`：人物及原节目核验来源，仅 HTTP/HTTPS 链接，三种输出均展示。姓名可据节目资料核实；`speakers_confirmed` 仍表示段落说话人归属是否完成校对。
- `artifacts: {markdown, pdf}`：生成路径。编辑后清空，防止页面下载过期版本。
- 可选 `is_demo: true`：仅用于明确演示，不能发布。

长文先通过 `status` 和 `batch` 读取所需范围，具体预算、续读及 `edit --batch` 的过期保护见 [分批整理](editing.md)。整理时写只含变化字段的 edits JSON，不直接覆盖原记录：

```json
{
  "speakers": [{"id": "speaker-1", "name": "主持人（姓名待确认）", "role": "主持人"}],
  "segments": [{"id": "seg-00001", "text": "这是一段忠实整理后的完整发言。", "review_status": "edited"}],
  "summary": ["本集讨论的主要议题。"],
  "chapters": [{"id": "chapter-1", "title": "话题名称", "start": 0.0, "segment_id": "seg-00001"}]
}
```

示例的章节时间必须替换成真实段落开始时间。编辑可局部更新；不允许删除段落、改原始转写或时间戳。SRT 无人物标签时，先通过 `speakers` 加入确认过的匿名人物定义，再为段落指定 `speaker_id`。保存会备份前一版本到相邻 `history/`，撤回到草稿并重置整集校对状态。正文或说话人归属实际变更时，该段默认退回 `edited`；只有已核对这次变更，才在同一段 patch 中显式设为 `reviewed`。

合并重复声源时，先分批将相关段落的 `speaker_id` 指向保留的人物，再通过 `{"remove_speakers":["speaker-3","speaker-4"]}` 移除无引用的旧标签。仍被段落引用、重复或不存在的 ID 会被拒绝；原始标签与分配关系保留在编辑历史。该操作不删除正文，也不自动确认人物或内容已校对。

`segments` 是音频对齐切片，不是阅读段落。三种输出都按相邻且相同的已定义 `speaker_id` 合并为话轮，仅显示一次姓名和起始时间；未知说话人不自动合并。原始切片和章节点仍保留，章节跳转到话轮内的对应文字，不在一句话中间强插章节标题。合并只处理展示；去除重复词或多余断句符号仍需通过 `edit` 修改整理稿，并保留原始文本。明确的自然段可用 `\n\n` 表示。

## 校对状态

逐段 `review_status` 使用以下值：

| 状态 | 含义 |
| --- | --- |
| `unreviewed` | 原始导入，尚未整理或核对；旧记录缺失此字段时按此状态处理。 |
| `edited` | 已整理，尚未完成核对。 |
| `needs_review` | 存在听不清、交叠发言、归属不明等疑点，仍待核对。 |
| `reviewed` | 已依据来源完成该段核对。 |

旧值 `pending`、`uncertain` 仍可读取，均按待核对显示；新稿使用上表状态。其他值会被拒绝。除 `reviewed` 外，阅读输出均显示待核对；润色完成不等于校对完成。

实际逐段核对后，通过 `edit` 更新已核对段落，例如：

```json
{"segments":[{"id":"seg-00001","review_status":"reviewed"}]}
```

所有段落均为 `reviewed` 后，才可另行设置 `{"review":{"speakers_confirmed":true,"content_checked":true}}`。该操作表示**已完成**对应校对，不能为通过发布验证而随意设置。`content_checked: true` 与尚未核对的段落不能同时保存；`speakers_confirmed: true` 时不得存在归属为 `null` 的段落。未知真名不影响确认匿名标签，无法判断谁在说话则仍保持 `null`。

旧版本若留下了“整集已校对、段落仍待核对”的矛盾记录，普通校验和导出会指出问题。可执行 `edit` 应用 `{"review":{"speakers_confirmed":false,"content_checked":false}}`，保留历史并退回草稿，再逐段核对；不要直接修改原文件或批量假定为已核对。`edit` 的兼容读取只放宽旧校对状态冲突，其他结构错误仍需处理。

## 外部转写导入

外部转写 JSON 支持 `{"segments":[{"start":0,"end":3,"speaker":"A","text":"内容"}]}`、直接段落数组及 B站 `body` 格式。SRT/VTT 支持时间戳；VTT 的 `<v Name>` 标签可用作匿名人物分组线索。

JSON 正文使用 `text` 字段，缺失时读取 B站格式的 `content` 字段。正文必须是字符串；显式 `null`、数字、布尔值、数组或对象会使整次导入报错，不生成部分文稿。缺失正文或仅含空白的段落会跳过。

导入自制测试对话时使用 `import --demo`，明确区分功能演示和真实节目。
