# 系列归属

`series` 表示节目或栏目名称，不是原作者账号，也不是“AI”“商业”等主题标签。Agent 负责在生成文稿时核实归属；投稿人可补充信息；维护者审核最终归属和统一命名。无法确认时使用 `inbox` / “未分类”，不阻止交付或投稿。

## Agent 处理

1. 查看已有 `series`、输入元数据和节目说明。已有明确归属时保留；用户要求修改或发现明确冲突时再处理。核实这期是否属于该节目，不能仅凭 UP 主同名、嘉宾或正文提到某节目来归类。
2. 用官方单集页、官方节目页或原节目说明作为依据。优先使用已取得的信息；需要查询时只读取节目元数据，不重新下载音视频、不把全文交给额外模型。
3. 运行 `series-list` 查询目录中的标准 ID、标题、别名和来源。分享前可从[社区目录](https://aweng126.github.io/podcast-scribe/series-catalog.json)获取新版本，保存到任务工作区并使用 `--catalog`；无法访问时使用内置目录继续。目录是命名参考，匹配到名称不等于已经证明本期归属。
4. 精确名称或别名匹配时复用标准 ID；已核实的新节目可按名称生成稳定 ID，供维护者审核后补入目录。无法核实时保留未分类，并在交付中简短说明，不要求用户先查资料。
5. 在导出和 `share` 前用 `classify` 保存归属及证据。此命令只维护系列与系列来源，保留正文、时间戳、说话人、完成依据和本地发布状态；有变更时保存历史、增加修订号并清空过期导出。之后重新导出，已生成的投稿文件需另存新版。命令校验字段格式，不代替 Agent 阅读来源。

从任务工作区调用：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" series-list
bash "$PS_SKILL_ROOT/scripts/run.sh" classify data/my-episode/episode.json \
  --series-title '已核实的节目名称' --evidence-url 'https://example.com/official-episode'
```

用实际节目名称、文稿路径与官方证据替换示例；命中目录时也可仅传 `--series-id`。使用下载的目录时给 `series-list` 和 `classify` 加 `--catalog <文件路径>`。初次导入支持 `--series-title` / `--series-id` / `--series-catalog`，但核实来源仍由 Agent 负责。

明确无法确定或原分类有误又无可靠替代时：

```sh
bash "$PS_SKILL_ROOT/scripts/run.sh" classify data/my-episode/episode.json --unclassified
```

只为清理旧占位词无需逐个改文件：新的 Markdown/PDF 与页面会将旧的 `inbox` / “待归类”显示为“未分类”，保留原始记录。

## 投稿与维护

系列信息放在公开 JSON 中，Issue 仍使用原来的五个字段。Agent 交付投稿材料时说明当前系列；投稿人发现有误，可在投稿前要求 Skill 修正并重新生成文件。用户无需填写新的分类表单或维护目录。

目录由维护者在 `podcast_scribe/assets/series-catalog.json` 中统一维护；安装后的 Skill 随包携带，公共站构建会发布同结构的 `series-catalog.json`。维护者只纠正例外，核对别名和来源；一次改标准名称即可更新站点中使用同一 ID 的文稿。

已投稿内容是固定快照，维护者通过独立的 Issue 分类覆盖记录修正展示，不直接修改原稿或其 SHA-256。具体操作见[社区维护：系列归属维护](https://github.com/aweng126/podcast-scribe/blob/main/docs/community.md#系列归属维护)。
