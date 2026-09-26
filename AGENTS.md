# Podcast Scribe

用户要求使用 `podcast-scribe` 处理视频链接或本地素材时，读取并遵循 [Skill](skills/podcast-scribe/SKILL.md)，由 Agent 解析运行入口的绝对路径并从任务目录调用。仅提供 Skill 名称和输入已足够；默认参数、环境选择、分批整理和导出由 Skill 与程序处理。

项目开发任务不自动触发媒体下载或付费转写。测试素材保存在临时目录，不进入公共内容库。
