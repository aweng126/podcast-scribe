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

整理时写 edits JSON，不直接覆盖原记录：

```json
{
  "speakers": [{"id": "speaker-1", "name": "主持人（姓名待确认）", "role": "主持人"}],
  "segments": [{"id": "seg-00001", "text": "这是一段忠实整理后的完整发言。"}],
  "summary": ["本集讨论的主要议题。"],
  "chapters": [{"id": "chapter-1", "title": "话题名称", "start": 0.0, "segment_id": "seg-00001"}]
}
```

示例的章节时间必须替换成真实段落开始时间。编辑可局部更新；不允许删除段落、改原始转写或时间戳。SRT 无人物标签时，先通过 `speakers` 加入确认过的匿名人物定义，再为段落指定 `speaker_id`。保存会备份前一版本到相邻 `history/`，撤回到草稿并重置校对状态。

`segments` 是音频对齐切片，不是阅读段落。三种输出都按相邻且相同的已定义 `speaker_id` 合并为话轮，仅显示一次姓名和起始时间；未知说话人不自动合并。原始切片和章节点仍保留，章节跳转到话轮内的对应文字，不在一句话中间强插章节标题。合并只处理展示；去除重复词或多余断句符号仍需通过 `edit` 修改整理稿，并保留原始文本。明确的自然段可用 `\n\n` 表示。

最后单独写 `{"review":{"speakers_confirmed":true,"content_checked":true}}` 表示**已完成**对应校对，不能为通过发布验证而随意设置。未知真名不影响确认匿名标签；无法判断哪位说话人的段落仍保持 `null`。

外部转写 JSON 支持 `{"segments":[{"start":0,"end":3,"speaker":"A","text":"内容"}]}`、直接段落数组及 B站 `body` 格式。SRT/VTT 支持时间戳；VTT 的 `<v Name>` 标签可用作匿名人物分组线索。

JSON 正文使用 `text` 字段，缺失时读取 B站格式的 `content` 字段。正文必须是字符串；显式 `null`、数字、布尔值、数组或对象会使整次导入报错，不生成部分文稿。缺失正文或仅含空白的段落会跳过。

导入自制测试对话时使用 `import --demo`，明确区分功能演示和真实节目。
