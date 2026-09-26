"""Export checks use explicit fictional fixtures, never an alleged transcript."""

from copy import deepcopy
import pytest

from video_to_markdown.exporters import export_episode, render_markdown
from video_to_markdown.reading import reading_turns, segment_text_parts


@pytest.fixture
def episode():
    return {
        "schema_version": 1,
        "id": "fictional-export-test",
        "title": "导出测试：虚构对话（非视频转写）",
        "description": "此内容仅供软件测试，不对应真实节目。",
        "source": {"platform": "bilibili", "url": "https://www.bilibili.com/video/TEST?p=2", "author": "测试作者"},
        "series": {"id": "tests", "title": "虚构测试系列"},
        "duration_seconds": 125,
        "published_at": "2026-01-01",
        "speakers": [{"id": "a", "name": "说话人 A", "role": "测试角色"}],
        "segments": [
            {"id": "s1", "start": 0, "end": 30, "speaker_id": "a", "raw_text": "原始测试文本。", "text": "整理后的测试文本。"},
            {"id": "s2", "start": 30, "end": 125, "speaker_id": None, "raw_text": "未确认人物的测试文本。", "text": ""},
        ],
        "summary": ["这是虚构的导出功能测试。"],
        "chapters": [{"id": "c1", "title": "虚构章节", "start": 0, "segment_id": "s1"}],
        "status": "draft",
        "review": {"speakers_confirmed": False, "content_checked": False},
    }


def test_markdown_keeps_complete_dialogue_and_source(episode, tmp_path):
    original = deepcopy(episode)
    result = export_episode(episode, tmp_path, ["markdown"])
    document = result["markdown"].read_text(encoding="utf-8")
    assert episode == original
    assert "草稿 · 尚未发布" in document
    assert "整理后的测试文本。" in document
    assert "原始测试文本。" not in document
    assert "未确认人物的测试文本。" in document
    assert "说话人待确认" in document
    assert "p=2&t=30" in document
    assert "(#segment-1)" in document
    assert 'id="segment-1"' in document
    assert all(title in document for title in ["本期人物", "本期摘要", "章节目录", "完整对话"])


def test_untrusted_text_cannot_inject_markdown_or_html(episode):
    episode["title"] = "标题\n<script>alert(1)</script>"
    episode["segments"][0]["text"] = "[点击](javascript:alert(1)) <img src=x> **强制加粗**"
    episode["source"]["url"] = "javascript:alert(1)"
    document = render_markdown(episode)
    assert "<script>" not in document
    assert "<img" not in document
    assert "[原视频](javascript:" not in document
    assert r"\[点击\]" in document
    assert r"\*\*强制加粗\*\*" in document


def test_export_cannot_escape_directory_or_collide_after_sanitizing(episode, tmp_path):
    paths = []
    for value in ["../../outside", "..\\..\\outside", "outside", "../../"]:
        episode["id"] = value
        path = export_episode(episode, tmp_path, ["markdown"])["markdown"]
        assert path.parent == tmp_path
        assert path.is_file()
        paths.append(path)
    assert len(set(paths)) == 4


def test_invalid_format_does_not_write(episode, tmp_path):
    with pytest.raises(ValueError, match="不支持"):
        export_episode(episode, tmp_path / "not-created", ["markdown", "html"])
    assert not (tmp_path / "not-created").exists()


def test_failed_pdf_preserves_previous_markdown(episode, tmp_path, monkeypatch):
    import video_to_markdown.exporters as exporters

    target = tmp_path / "fictional-export-test.md"
    target.write_text("previous version", encoding="utf-8")
    monkeypatch.setattr(exporters, "_pdf_font", lambda: "unused")

    def fail(*args):
        raise RuntimeError("render failed")

    monkeypatch.setattr(exporters, "_render_pdf", fail)
    with pytest.raises(RuntimeError, match="render failed"):
        export_episode(episode, tmp_path, ["markdown", "pdf"])
    assert target.read_text(encoding="utf-8") == "previous version"
    assert sorted(path.name for path in tmp_path.iterdir()) == [target.name]


def test_pdf_chinese_long_speech_and_literal_markup(episode, tmp_path):
    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    episode["segments"][0]["text"] = (
        "这是虚构的长段落排版测试，数字 123 与英文 Markdown 应当正确显示。" * 190
        + "段落末尾标记。 <b>原样保留</b> & 不解析文稿 HTML。"
    )
    result = export_episode(episode, tmp_path, ["pdf"])
    reader = pypdf.PdfReader(result["pdf"])
    text = "".join(page.extract_text() for page in reader.pages)
    assert len(reader.pages) >= 3
    assert "草稿" in text
    assert "段落末尾标记" in text
    assert "说话人待确认" in text
    assert "<b>原样保留</b>" in text.replace("\n", "")
    for index, page in enumerate(reader.pages, 1):
        assert f"第 {index} 页" in page.extract_text()
    fonts = [font.get_object() for page in reader.pages for font in page["/Resources"]["/Font"].values()]
    assert any(
        "/FontDescriptor" in font and "/FontFile2" in font["/FontDescriptor"].get_object()
        for font in fonts
    )


def test_published_markdown_omits_draft_banner(episode):
    episode["status"] = "published"
    episode["review"] = {"speakers_confirmed": True, "content_checked": True}
    assert "尚未发布" not in render_markdown(episode)


def test_empty_transcript_is_explicit(episode):
    episode["segments"] = []
    episode["speakers"] = []
    episode["summary"] = []
    episode["chapters"] = []
    document = render_markdown(episode)
    assert "尚无转写内容" in document
    assert "暂无摘要" in document
    assert "人物待确认" in document


def test_continuous_speech_preserves_chapter_targets_without_repeated_labels(episode, tmp_path):
    episode["segments"] = [
        {"id": "s1", "start": 22, "end": 30, "speaker_id": "a", "text": "遇到问题，"},
        {"id": "s2", "start": 30, "end": 56, "speaker_id": "a", "text": "要第一时间面对。", "review_status": "needs_review"},
        {"id": "s3", "start": 56, "end": 60, "speaker_id": "a", "text": "承担后果。"},
        {"id": "s4", "start": 60, "end": 61, "speaker_id": None, "text": "是吗？"},
    ]
    episode["chapters"] = [{"title": "面对问题", "start": 30, "segment_id": "s2"}]
    original = deepcopy(episode)
    document = render_markdown(episode)
    dialogue = document.split("## 完整对话", 1)[1]
    assert dialogue.count("· 说话人 A") == 1
    assert "p=2&t=22" in dialogue
    assert "p=2&t=30" not in dialogue
    assert '遇到问题，<a id="segment-2"></a>要第一时间面对。<a id="segment-3"></a>承担后果。' in dialogue
    assert "(#segment-2)" in document
    assert "### 面对问题" not in dialogue
    assert "待核对" in dialogue
    assert "说话人待确认" in dialogue
    assert episode == original

    pytest.importorskip("reportlab")
    pypdf = pytest.importorskip("pypdf")
    path = export_episode(episode, tmp_path, ["pdf"])["pdf"]
    reader = pypdf.PdfReader(path)
    visible = "".join(page.extract_text() for page in reader.pages).replace("\n", "")
    assert "遇到问题，要第一时间面对。承担后果。" in visible
    assert visible.count("· 说话人 A") == 1
    assert "00:00:22 · 说话人 A · 待核对" in visible
    assert any(annotation.get_object().get("/Dest") for page in reader.pages for annotation in page.get("/Annots", []))
    assert episode == original


def test_turns_keep_real_speaker_changes_and_unknowns_separate(episode):
    episode["segments"] = [
        {"speaker_id": speaker, "start": i, "end": i + 1, "text": str(i)}
        for i, speaker in enumerate(["a", "a", None, None, "invalid", "invalid", "a"])
    ]
    original = deepcopy(episode)
    turns = reading_turns(episode)
    assert [len(turn["segments"]) for turn in turns] == [2, 1, 1, 1, 1, 1]
    assert [s["index"] for t in turns for s in t["segments"]] == list(range(7))
    assert (turns[0]["start"], turns[0]["end"]) == (0, 2)
    assert episode == original


def test_joined_text_keeps_paragraphs_and_english_word_spacing():
    assert "".join(segment_text_parts([
        {"text": "你好，"}, {"raw_text": "世界。\n\n这是第二段。"},
    ])) == "你好，世界。\n\n这是第二段。"
    assert "".join(segment_text_parts([
        {"text": "Hello,"}, {"text": "world."}, {"text": "Next"}, {"text": "sentence."},
    ])) == "Hello, world. Next sentence."
