"""Public-boundary and artifact tests for the dependency-free site builder."""

from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from podcast_scribe.site import build_site


def fixture(identifier="example", status="published"):
    return {
        "schema_version": 1,
        "id": identifier,
        "title": "测试文稿",
        "status": status,
        "source": {"url": "https://www.bilibili.com/video/BVtest", "author": "测试作者"},
        "series": {"id": "series", "title": "测试系列"},
        "speakers": [{"id": "a", "name": "说话人 A"}],
        "segments": [{"id": "s1", "start": 0, "end": 10, "speaker_id": "a", "text": "整理内容", "raw_text": "不会公开的原始文本"}],
        "chapters": [{"id": "c1", "title": "开场", "start": 0, "segment_id": "s1"}],
        "summary": ["测试摘要"],
        "review": {"speakers_confirmed": True, "content_checked": True},
    }


def site_data(path):
    content = path.read_text(encoding="utf-8")
    match = re.search(r'<script id="transcript-data" type="application/json">(.*?)</script>', content, re.DOTALL)
    return json.loads(match.group(1))


def render_reader(path):
    """Exercise the client renderer, without claiming browser layout coverage."""
    script = r'''
const fs = require("fs");
const vm = require("vm");
const data = JSON.parse(fs.readFileSync(0, "utf8"));
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {innerHTML:"",textContent:"",focus(){},addEventListener(){},querySelectorAll(){return[];}});
  return elements.get(id);
}
element("transcript-data").textContent = JSON.stringify(data);
global.document = {getElementById:element,querySelector:element,title:""};
global.location = {hash:"#/episode/" + encodeURIComponent(data.episodes[0].id)};
global.window = {scrollTo(){},addEventListener(){}};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
process.stdout.write(element("content").innerHTML);
'''
    return subprocess.run(["node", "-e", script, str(path.parent / "app.js")], input=json.dumps(site_data(path)), text=True, capture_output=True, check=True).stdout


class SiteTests(unittest.TestCase):
    def test_defaults_to_published_and_does_not_mutate_input(self):
        episodes = [fixture(), fixture("secret-draft", "draft")]
        before = deepcopy(episodes)
        with tempfile.TemporaryDirectory() as directory:
            result = build_site(episodes, Path(directory))
            data = site_data(result)
            self.assertEqual([item["id"] for item in data["episodes"]], ["example"])
            self.assertFalse(data["preview"])
            self.assertNotIn("secret-draft", result.read_text())
            self.assertNotIn("不会公开的原始文本", result.read_text())
            self.assertEqual(episodes, before)
            self.assertTrue((Path(directory) / "app.js").is_file())
            self.assertTrue((Path(directory) / "app.css").is_file())

    def test_markup_cannot_escape_data_script(self):
        episode = fixture()
        episode["title"] = '</script><img src=x onerror="alert(1)"> & <标题>'
        episode["source"]["url"] = "javascript:alert(1)"
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            self.assertNotIn('<img src=x', result.read_text())
            self.assertEqual(site_data(result)["episodes"][0]["title"], episode["title"])
            self.assertEqual(site_data(result)["episodes"][0]["source"]["url"], "")

    def test_demo_is_excluded_even_if_mistakenly_marked_published(self):
        episode = fixture("demo", "published")
        episode["is_demo"] = True
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = build_site([episode], output)
            self.assertEqual(site_data(result)["episodes"], [])
            build_site([episode], output, include_drafts=True)
            data = site_data(result)
            self.assertTrue(data["preview"])
            self.assertTrue(data["episodes"][0]["is_demo"])

    def test_copies_real_downloads_without_exposing_source_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "private original.md"
            source.write_text("# 测试文稿", encoding="utf-8")
            episode = fixture("../../不安全/id")
            episode["artifacts"] = {"markdown": str(source), "pdf": str(root / "missing.pdf")}
            result = build_site([episode], root / "site")
            data = site_data(result)["episodes"][0]
            self.assertNotIn(str(source), result.read_text())
            self.assertEqual(list(data["downloads"]), ["markdown"])
            download = result.parent / data["downloads"]["markdown"]
            self.assertEqual(download.parent, result.parent / "downloads")
            self.assertEqual(download.read_text(encoding="utf-8"), "# 测试文稿")

    def test_rebuilding_public_site_removes_previous_draft_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "draft.md"
            source.write_text("private draft", encoding="utf-8")
            draft = fixture("draft", "draft")
            draft["artifacts"] = {"markdown": str(source)}
            result = build_site([draft], root / "site", include_drafts=True)
            old_file = result.parent / site_data(result)["episodes"][0]["downloads"]["markdown"]
            unrelated = old_file.parent / "keep.txt"
            unrelated.write_text("unrelated", encoding="utf-8")
            self.assertTrue(old_file.is_file())
            build_site([draft], root / "site")
            self.assertFalse(old_file.exists())
            self.assertTrue(unrelated.is_file())
            self.assertTrue(source.is_file())
            self.assertEqual(site_data(result)["episodes"], [])

    def test_manifest_cannot_delete_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "keep.md"
            private.write_text("keep", encoding="utf-8")
            output = root / "site"
            output.mkdir()
            (output / ".site-manifest.json").write_text(json.dumps({"downloads": ["../keep.md"]}), encoding="utf-8")
            build_site([], output)
            self.assertTrue(private.is_file())

    def test_duplicate_episode_ids_fail_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            with self.assertRaisesRegex(ValueError, "unique"):
                build_site([fixture(), fixture()], output)
            self.assertFalse(output.exists())

    def test_unknown_speaker_and_nonfinite_time_are_safe(self):
        episode = fixture()
        episode["segments"][0].update(speaker_id=None, start=float("nan"), end=float("inf"))
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            segment = site_data(result)["episodes"][0]["segments"][0]
            self.assertIsNone(segment["speaker_id"])
            self.assertEqual((segment["start"], segment["end"]), (0, 0))

    def test_public_turns_merge_known_speakers_without_leaking_raw_segments(self):
        episode = fixture()
        episode["segments"] = [
            {"id": "s1", "start": 22, "end": 30, "speaker_id": "a", "text": "我们是面对吧。", "raw_text": "private-first", "review_status": "edited"},
            {"id": "s2", "start": 30, "end": 56, "speaker_id": "a", "text": "有什么问题，一定要面对。", "raw_text": "private-second", "review_status": "needs_review"},
            {"id": "s3", "start": 56, "end": 65, "speaker_id": "a", "text": "最后只能自己去做。", "raw_text": "private-third", "review_status": "edited"},
        ]
        before = deepcopy(episode)
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            public = site_data(result)["episodes"][0]
            self.assertEqual(len(public["segments"]), 3)
            self.assertEqual(public["turns"], [{
                "speaker_id": "a", "start": 22, "end": 65,
                "segment_indices": [0, 1, 2],
                "text": "我们是面对吧。有什么问题，一定要面对。最后只能自己去做。",
                "text_parts": ["我们是面对吧。", "有什么问题，一定要面对。", "最后只能自己去做。"],
                "needs_review": True,
            }])
            self.assertNotIn("private-first", result.read_text())
            self.assertNotIn("private-second", result.read_text())
            self.assertNotIn("private-third", result.read_text())
            self.assertEqual(episode, before)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
    def test_reader_preserves_continuous_speech_and_interior_chapter_anchors(self):
        episode = fixture()
        episode["review"]["mode"] = "precise"
        episode["segments"] = [
            {"id": "s1", "start": 22, "end": 30, "speaker_id": "a", "text": "困难不能躲避——", "review_status": "edited"},
            {"id": "s2", "start": 30, "end": 56, "speaker_id": "a", "text": "需要正面地想好。", "review_status": "needs_review"},
            {"id": "s3", "start": 56, "end": 65, "speaker_id": "a", "text": "最后自己去做。", "review_status": "edited"},
        ]
        episode["chapters"] = [
            {"id": "c1", "title": "面对困难", "start": 22, "segment_id": "s1"},
            {"id": "c2", "title": "正面思考", "start": 40},
            {"id": "c3", "title": "自己承担", "start": 56, "segment_id": "s3"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            rendered = render_reader(result)
            self.assertEqual(rendered.count('class="dialogue-label"'), 1)
            self.assertEqual(rendered.count('class="timestamp"'), 1)
            self.assertIn("1 段对话", rendered)
            self.assertIn('href="https://www.bilibili.com/video/BVtest?t=22"', rendered)
            self.assertNotIn('href="https://www.bilibili.com/video/BVtest?t=30"', rendered)
            self.assertEqual(rendered.count('class="segment-review"'), 1)
            self.assertIn('<h3 class="transcript-chapter">面对困难</h3>', rendered)
            self.assertNotIn('<h3 class="transcript-chapter">正面思考</h3>', rendered)
            self.assertNotIn('<h3 class="transcript-chapter">自己承担</h3>', rendered)
            # Both explicit IDs and a time inside s2 address the original text.
            for index in range(3):
                self.assertIn(f'data-scroll="segment-{index}"', rendered)
                self.assertIn(f'<span id="segment-{index}" class="segment-anchor" tabindex="-1">', rendered)
            self.assertIn('困难不能躲避——</span><span id="segment-1"', rendered)
            self.assertIn('需要正面地想好。</span><span id="segment-2"', rendered)
            paragraph = re.search(r'<p>(<span id="segment-0".*?)</p>', rendered).group(1)
            self.assertEqual(re.sub(r'<[^>]+>', '', paragraph), "困难不能躲避——需要正面地想好。最后自己去做。")

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
    def test_reader_retains_speaker_changes_and_separate_unknown_speech(self):
        episode = fixture()
        episode["speakers"].append({"id": "b", "name": "说话人 B"})
        ids = ["a", "a", "b", "a", None, None, "", "", "absent", "absent"]
        episode["segments"] = [
            {"id": f"s{index}", "start": index * 10, "end": (index + 1) * 10, "speaker_id": speaker, "text": f"句{index}。"}
            for index, speaker in enumerate(ids)
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            turns = site_data(result)["episodes"][0]["turns"]
            self.assertEqual([turn["segment_indices"] for turn in turns], [[0, 1], [2], [3], [4], [5], [6], [7], [8], [9]])
            rendered = render_reader(result)
            self.assertEqual(rendered.count('class="dialogue-label"'), 9)
            self.assertEqual(rendered.count("未识别说话人"), 6)
            self.assertIn("9 段对话", rendered)

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
    def test_inline_turn_fragments_keep_word_spacing_and_escape_markup(self):
        episode = fixture()
        episode["segments"] = [
            {"id": "s1", "start": 0, "end": 5, "speaker_id": "a", "text": "Hello"},
            {"id": "s2", "start": 5, "end": 10, "speaker_id": "a", "text": "world. <script>alert(1)</script>"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            turn = site_data(result)["episodes"][0]["turns"][0]
            self.assertEqual(turn["text_parts"], ["Hello", " world. <script>alert(1)</script>"])
            rendered = render_reader(result)
            self.assertIn('tabindex="-1"> world. &lt;script&gt;alert(1)&lt;/script&gt;</span>', rendered)
            self.assertNotIn("<script>", rendered)

    def test_references_only_publish_safe_web_sources(self):
        episode = fixture()
        valid = {"title": "节目主页", "url": "https://example.org/show?episode=5&lang=zh", "note": "人物姓名核验"}
        episode["references"] = [
            {**valid, "raw_text": "private raw text", "local_path": "/private/source.txt"},
            {"title": "invalid", "url": "javascript:alert(1)"},
            {"title": "local", "url": "file:///private/source.txt"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            self.assertEqual(site_data(result)["episodes"][0]["references"], [valid])
            self.assertNotIn("/private/source.txt", result.read_text())
            self.assertNotIn("private raw text", result.read_text())

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
    def test_references_render_in_reader_body_with_escaped_content(self):
        episode = fixture(status="draft")
        episode["review"]["mode"] = "precise"
        episode["review"]["content_checked"] = False
        episode["segments"][0]["review_status"] = "unreviewed"
        episode["references"] = [{"title": '<img src=x onerror="alert(1)">', "url": "https://example.org/show?a=1&b=2", "note": '<script>alert("x")</script>'}]
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory), include_drafts=True)
            rendered = render_reader(result)
            self.assertIn("来源与人物核验", rendered)
            self.assertGreater(rendered.index('id="references-title"'), rendered.index('class="reader-body"'))
            self.assertIn('href="https://example.org/show?a=1&amp;b=2"', rendered)
            self.assertIn("&lt;img src=x", rendered)
            self.assertNotIn("<img src=x", rendered)
            self.assertNotIn("<script>", rendered)
            self.assertIn('class="segment-review">待核对', rendered)
            self.assertIn("段落说话人归属已确认。", rendered)
            self.assertNotIn("人物标签尚待确认", rendered)
            episode["review"]["speakers_confirmed"] = False
            build_site([episode], Path(directory), include_drafts=True)
            self.assertIn("段落说话人归属待复核。", render_reader(result))

    @unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
    def test_reader_client_renders_safe_text_and_bilibili_time_links(self):
        episode = fixture("中文/episode")
        episode["title"] = '<img src=x onerror="alert(1)">'
        episode["segments"][0].update(text='<script>alert("x")</script>', start=65, speaker_id=None)
        with tempfile.TemporaryDirectory() as directory:
            result = build_site([episode], Path(directory))
            rendered = render_reader(result)
            self.assertIn("未识别说话人", rendered)
            self.assertIn("https://www.bilibili.com/video/BVtest?t=65", rendered)
            self.assertIn("&lt;img src=x", rendered)
            self.assertNotIn("<img src=x", rendered)
            self.assertNotIn("<script>", rendered)
            self.assertIn("下载文件尚未生成", rendered)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
def test_default_auto_pending_reader_is_processing_not_manual_review(tmp_path):
    episode = fixture(status="draft")
    episode["review"] = {"speakers_confirmed": False, "content_checked": False}
    episode["segments"][0]["review_status"] = "needs_review"
    result = build_site([episode], tmp_path, include_drafts=True)
    rendered = render_reader(result)
    assert "自动整理处理中" in rendered and "文稿仍在整理中" in rendered
    assert 'class="segment-review"' not in rendered
    assert "待复核" not in rendered and "请结合来源核对" not in rendered
    episode["review"]["mode"] = "precise"
    result = build_site([episode], tmp_path, include_drafts=True)
    precise = render_reader(result)
    assert "精准校对进行中" in precise
    assert 'class="segment-review">待核对' in precise
    assert "段落说话人归属待复核。" in precise


@unittest.skipUnless(shutil.which("node"), "Node is optional; used only to exercise client rendering")
def test_completed_local_reader_preserves_basis_without_draft_notice(tmp_path):
    episode = fixture(status="draft")
    episode["segments"][0]["review_status"] = "reviewed"
    for basis, label in [("automated", "自动整理完成"), ("user_accepted", "用户已确认采用当前稿")]:
        episode["review"].update(mode="auto", basis=basis)
        result = build_site([episode], tmp_path, include_drafts=True)
        assert site_data(result)["episodes"][0]["review"]["basis"] == basis
        rendered = render_reader(result)
        assert rendered.count(label) == 1
        assert "未发布" in rendered
        assert "草稿预览" not in rendered and 'class="draft-notice"' not in rendered
        assert "人物与内容已校对" not in rendered
