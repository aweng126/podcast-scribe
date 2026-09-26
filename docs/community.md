# 社区分享与维护

本地 Markdown/PDF 是默认产物；离线页面和社区分享均为可选功能。安装与使用见 [README](../README.md)。

## 投稿

1. 完成全文和说话人校对，运行 `share` 生成公开投稿 JSON 和预填 Issue 链接。
2. 检查 JSON，打开链接，上传该文件，确认可以公开分享后提交。
3. 自动校验通过后，机器人创建 `community/issue-<编号>` 分支和内容 PR，并在 Issue 留下链接。维护者审核合并后，Pages 自动更新。

Issue 和附件在收录审核前就已公开。只上传 `share` 生成的文件；不要上传本地 `episode.json`、音频或编辑历史。文件上限为 2 MiB。

一个 Issue 对应一份固定快照。首次有效投稿之后，编辑 Issue 或重跑工作流不会更新 PR 中的正文。需要改稿时新建分享 Issue，说明原投稿；维护者关闭旧 PR，或在替换收录时一并移除旧文件。校验失败且尚未生成快照时，可以直接修改 Issue 重试。

## 首次启用

仓库管理员完成以下设置：

1. **Settings → General → Features**：启用 Issues。
2. **Settings → Actions → General**：允许运行本仓库工作流；在 **Workflow permissions** 中启用 **Allow GitHub Actions to create and approve pull requests**。组织策略若禁止此项，需要组织管理员调整。工作流按任务声明所需 token 权限，不需要添加 PAT。
3. **Settings → Pages → Build and deployment → Source**：选择 **GitHub Actions**。
4. 在 **Actions → Deploy community reader → Run workflow** 中选择 `main`，部署首次空目录页面。后续相关 `main` 提交自动部署。

默认站点地址为 `https://aweng126.github.io/podcast-scribe/`。只有 Pages 工作流成功后站点才可用。

`Community submission` 创建 PR 若被权限设置阻止，会在 Issue 提示修改上述设置。调整后重跑该次工作流即可恢复；已经生成的快照会保留。

## 审核与收录

审核 PR 的公开 JSON、来源、署名及 Issue 中的公开分享确认，查看 `Community validation` 检查结果，再由维护者合并。机器人创建的 PR 检查可能显示等待批准，维护者需要批准运行。不要启用投稿自动合并。

正式数据保存在 `content/episodes/issue-<编号>.json`，包含公开投稿、来源 Issue、投稿账号和内容 SHA-256。生成的页面及下载文件只作为 Actions 部署产物。构建会拒绝重复单集 ID、重复投稿、错误摘要、额外字段和不安全路径。

本地检查与预览：

```sh
python3 scripts/build_public_site.py --content-dir content/episodes --output-dir output/site
python3 -m http.server 8000 --directory output/site
```

打开 `http://localhost:8000/`。公共站点按需加载正文，预览需要 HTTP 服务；本地离线 HTML 功能仍通过 Skill 的 `site` 命令使用。

拒绝投稿时关闭对应 PR 并说明原因。已经收录的改稿通过新的内容 PR 替换旧记录，避免同时保留重复单集 ID。撤下文稿时提交删除对应 JSON 的 PR；合并并部署后，阅读站不再展示。Git 历史及 Issue 附件仍可能保留原内容，如需删除公开附件或涉及隐私，应联系仓库维护者和 GitHub 支持进一步处理。

## 工作流边界

- `Community submission` 仅从 `main` 运行维护中的脚本。附件按 JSON 校验，下载请求不携带仓库 token，只接受 GitHub 附件及指定存储地址；自动创建的提交只新增该 Issue 对应的内容文件。
- `Community validation` 在 PR 中只读运行校验、构建和测试，不部署。
- `Deploy community reader` 只部署 `main` 中已合并的数据。无需数据库、常驻服务或浏览器端 GitHub token。

配置依据：[Issue 表单](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-githubs-form-schema)、[Pages 自定义工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)、[GitHub token 与工作流触发](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)。
