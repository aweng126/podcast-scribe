"""Hosted-library boundaries and asynchronous reader behavior."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from podcast_scribe.public_site import build_public_site, load_series_overrides, loads_record, validate_record
from podcast_scribe.share import submission_digest


ROOT = Path(__file__).resolve().parents[1]


def record(identifier="episode-one", issue=1, content="完整正文：只在单篇与搜索索引中出现。"):
    submission = {"schema_version": 1, "attribution": "测试投稿人", "episode": {
        "id": identifier, "title": f"公开文稿 {identifier}", "description": "本期介绍",
        "source": {"platform": "web", "url": "https://example.com/episode", "author": "原作者"},
        "series": {"id": "series", "title": "访谈节目", "description": "节目介绍"},
        "duration_seconds": 10, "published_at": "2026-09-26",
        "speakers": [{"id": "a", "name": "说话人", "role": "嘉宾"}],
        "segments": [{"id": "s1", "start": 0, "end": 10, "speaker_id": "a", "text": content, "review_status": "reviewed"}],
        "chapters": [{"id": "c1", "title": "开场", "start": 0, "segment_id": "s1"}],
        "summary": ["节目摘要"], "references": [],
        "review": {"speakers_confirmed": True, "content_checked": True},
    }}
    return {"schema_version": 1, "submission": submission, "provenance": {
        "issue_url": f"https://github.com/aweng126/podcast-scribe/issues/{issue}",
        "submitter": "someone", "payload_sha256": submission_digest(submission),
    }}


def metadata(path):
    match = re.search(r'<script id="transcript-data" type="application/json">(.*?)</script>', path.read_text(), re.S)
    return json.loads(match.group(1))


def builder_module():
    spec = importlib.util.spec_from_file_location("public_builder", ROOT / "scripts" / "build_public_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_empty_library_has_useful_public_landing_without_example_content(tmp_path):
    result = build_public_site([], tmp_path)
    assert metadata(result)["episodes"] == []
    assert metadata(result)["mode"] == "public"
    assert metadata(result)["submit_url"].endswith("/issues/new?template=share.yml")
    assert json.loads((tmp_path / "search-index.json").read_text())["episodes"] == []
    assert (tmp_path / ".nojekyll").is_file()
    assert not (tmp_path / "episodes").exists()


def test_home_metadata_is_light_and_downloads_are_derived_only_from_public_data(tmp_path):
    source = record()
    before = deepcopy(source)
    result = build_public_site([source], tmp_path)
    listing = metadata(result)["episodes"][0]
    assert "完整正文" not in result.read_text()
    assert not {"segments", "turns", "raw_text", "artifacts", "history"} & listing.keys()
    assert re.fullmatch(r"episodes/[a-f0-9]{64}\.json", listing["data_url"])
    episode = json.loads((tmp_path / listing["data_url"]).read_text())
    assert episode["segments"][0]["text"] == source["submission"]["episode"]["segments"][0]["text"]
    assert episode["turns"][0]["segment_indices"] == [0]
    assert episode["provenance"]["issue_url"].endswith("/1")
    assert episode["attribution"] == "测试投稿人"
    assert set(episode["downloads"]) == {"markdown"}
    markdown = (tmp_path / episode["downloads"]["markdown"]).read_text()
    assert "完整正文" in markdown and "测试投稿人" in markdown and "issues/1" in markdown
    search = json.loads((tmp_path / "search-index.json").read_text())
    assert "完整正文" in search["episodes"][0]["text"]
    assert source == before
    serialized = json.dumps(episode)
    for private_field in ("raw_text", "artifacts", "history"):
        assert private_field not in serialized
    assert 'href="app.css"' in result.read_text()
    assert 'src="app.js"' in result.read_text()


def test_record_validation_is_detached_and_binds_provenance():
    original = record()
    result = validate_record(original)
    result["submission"]["episode"]["title"] = "changed"
    result["provenance"]["submitter"] = "changed"
    assert original["submission"]["episode"]["title"] != "changed"
    assert original["provenance"]["submitter"] != "changed"
    original["provenance"]["payload_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="payload_sha256"):
        validate_record(original)


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(secret="private"),
    lambda value: value.update(schema_version=True),
    lambda value: value["provenance"].update(issue_url="javascript:alert(1)"),
    lambda value: value["provenance"].update(issue_url="https://github.com/other/repo/issues/1"),
    lambda value: value["provenance"].update(submitter="<script>"),
    lambda value: value["submission"]["episode"].update(artifacts={"markdown": "/private/secret"}),
    lambda value: value["submission"]["episode"]["segments"][0].update(raw_text="secret"),
    lambda value: value["submission"]["episode"]["review"].update(content_checked=False),
])
def test_invalid_record_cannot_change_existing_output(tmp_path, mutate):
    good = record()
    output = tmp_path / "site"
    build_public_site([good], output)
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    bad = deepcopy(good)
    mutate(bad)
    with pytest.raises(ValueError):
        build_public_site([good, bad], output)
    assert {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()} == before


def test_duplicates_fail_before_creating_output(tmp_path):
    first = record()
    for second in (record(issue=2), record("different", issue=1)):
        with pytest.raises(ValueError, match="unique"):
            build_public_site([first, second], tmp_path / "absent")
        assert not (tmp_path / "absent").exists()


def test_rebuild_removes_withdrawn_episode_and_download_but_preserves_other_files(tmp_path):
    result = build_public_site([record(), record("episode-two", issue=2)], tmp_path)
    removed_meta = metadata(result)["episodes"][0]
    removed_path = tmp_path / removed_meta["data_url"]
    download = tmp_path / json.loads(removed_path.read_text())["downloads"]["markdown"]
    unrelated = tmp_path / "downloads" / "keep.md"
    unrelated.write_text("unrelated")
    build_public_site([record("episode-two", issue=2)], tmp_path)
    assert not removed_path.exists() and not download.exists()
    assert unrelated.read_text() == "unrelated"
    assert "episode-one" not in result.read_text()
    assert "episode-one" not in (tmp_path / "search-index.json").read_text()
    build_public_site([], tmp_path)
    assert list((tmp_path / "episodes").iterdir()) == []
    assert list((tmp_path / "downloads").iterdir()) == [unrelated]


def test_manifest_cannot_delete_external_files_and_generated_symlinks_are_rejected(tmp_path):
    output = tmp_path / "site"
    output.mkdir()
    keep = tmp_path / "keep.md"
    keep.write_text("keep")
    (output / ".public-site-manifest.json").write_text(json.dumps({"files": ["../keep.md", "downloads/../../keep.md"]}))
    with pytest.raises(ValueError, match="manifest"):
        build_public_site([], output)
    (output / ".public-site-manifest.json").write_text(json.dumps({"files": []}))
    build_public_site([], output)
    assert keep.read_text() == "keep"
    (output / "episodes").symlink_to(tmp_path, target_is_directory=True)
    before = (output / "index.html").read_bytes()
    with pytest.raises(ValueError, match="symlink"):
        build_public_site([record()], output)
    assert (output / "index.html").read_bytes() == before


def test_record_json_and_content_filenames_are_strict(tmp_path):
    valid = json.dumps(record(), ensure_ascii=False).encode()
    assert loads_record(valid)["submission"]["episode"]["id"] == "episode-one"
    for payload in (b'{"schema_version":1,"schema_version":1}', b'{"x":NaN}', b'{"x":Infinity}', b'\xff', b"[" * 1100):
        with pytest.raises(ValueError):
            loads_record(payload)
    builder = builder_module()
    path = tmp_path / "issue-1.json"
    path.write_bytes(valid)
    assert len(builder.load_records(tmp_path)) == 1
    path.rename(tmp_path / "issue-2.json")
    with pytest.raises(ValueError, match="match"):
        builder.load_records(tmp_path)
    (tmp_path / "issue-2.json").rename(tmp_path / "arbitrary.json")
    with pytest.raises(ValueError, match="filename"):
        builder.load_records(tmp_path)
    (tmp_path / "arbitrary.json").rename(path)
    (tmp_path / "alias").symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        builder.load_records(tmp_path)


NODE_HARNESS = r'''
const fs = require("fs"), vm = require("vm");
const spec = JSON.parse(fs.readFileSync(0, "utf8"));
const elements = new Map(), handlers = {}, calls = [], pending = [], downloads = [];
function element(id) {
  if (!elements.has(id)) elements.set(id, {
    innerHTML:"", textContent:"", value:"", listeners:{}, focus(){}, scrollIntoView(){},
    addEventListener(event, callback){ this.listeners[event] = callback; },
    querySelectorAll(selector){
      if (selector !== "[data-page]") return [];
      return [...this.innerHTML.matchAll(/<button[^>]*data-page="(-?[0-9]+)"([^>]*)>/g)].map(match => {
        const segment = /data-segment="([^"]+)"/.exec(match[2]);
        const button = element(segment ? `[data-segment="${segment[1]}"]` : `[data-page="${match[1]}"]`);
        button.dataset = {page: match[1], segment: segment?.[1]};
        return button;
      });
    }, querySelector:element
  });
  return elements.get(id);
}
element("transcript-data").textContent = JSON.stringify(spec.data);
global.document = {getElementById:element, querySelector:element, title:"", createElement(){return {click(){}};}};
global.URL.createObjectURL = blob => { blob.text().then(text => downloads.push(text)); return "blob:download"; };
global.URL.revokeObjectURL = () => {};
global.setTimeout = callback => callback();
global.location = {hash:spec.hash || "#/"};
global.history = {replaceState(_a,_b,hash){location.hash=hash;}};
global.window = {scrollTo(){},addEventListener(name, fn){handlers[name]=fn;}};
global.fetch = (url, options) => {
  calls.push({url, options});
  return new Promise((resolve,reject) => pending.push({url,resolve,reject}));
};
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const snapshots = [];
async function flush() { await new Promise(resolve => setImmediate(resolve)); }
function snapshot() { snapshots.push({html:element("content").innerHTML,results:element("search-results").innerHTML,calls:calls.map(item=>item.url), title:document.title, downloads:[...downloads]}); }
(async()=>{
  await flush(); snapshot();
  for (const action of spec.actions || []) {
    if (action.type === "route") { location.hash=action.hash; handlers.hashchange(); }
    if (action.type === "input") { const input=element("search-input"); input.value=action.value; input.listeners.input(); }
    if (action.type === "click") element(action.selector).listeners.click({preventDefault(){}, currentTarget: element(action.selector)});
    if (action.type === "resolve" || action.type === "reject") {
      const index=pending.findIndex(item=>item.url===action.url);
      if(index<0) throw Error("No request: "+action.url);
      const request=pending.splice(index,1)[0];
      if(action.type === "reject") request.reject(Error("offline"));
      else request.resolve({ok:true,json:async()=>spec.resources[action.url]});
    }
    await flush(); snapshot();
  }
  process.stdout.write(JSON.stringify(snapshots));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
'''


def client(path, actions=None, hash="#/"):
    if not shutil.which("node"):
        pytest.skip("Node.js is required for the reader behavior harness")
    resources = {item.relative_to(path.parent).as_posix(): json.loads(item.read_text())
                 for item in path.parent.rglob("*.json") if not item.name.startswith(".")}
    result = subprocess.run(["node", "-e", NODE_HARNESS, str(path.parent / "app.js")],
                            input=json.dumps({"data": metadata(path), "resources": resources, "hash": hash, "actions": actions or []}),
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def test_hosted_home_and_empty_search_do_not_fetch_transcripts(tmp_path):
    result = build_public_site([], tmp_path)
    snapshots = client(result, [{"type": "route", "hash": "#/search"}])
    assert "投稿一篇文稿" in snapshots[0]["html"]
    assert "审核通过" in snapshots[0]["html"]
    assert all(snapshot["calls"] == [] for snapshot in snapshots)


def test_hosted_routes_load_one_episode_and_ignore_stale_fetches(tmp_path):
    result = build_public_site([record(content="第一篇独有正文"), record("episode-two", 2, "第二篇独有正文")], tmp_path)
    first, second = [item["data_url"] for item in metadata(result)["episodes"]]
    snapshots = client(result, [
        {"type": "route", "hash": "#/episode/episode-two"},
        {"type": "resolve", "url": second},
        {"type": "resolve", "url": first},
        {"type": "route", "hash": "#/"},
        {"type": "route", "hash": "#/episode/episode-one"},
    ], hash="#/episode/episode-one")
    assert snapshots[0]["calls"] == [first]
    assert snapshots[1]["calls"] == [first, second]
    assert "第二篇独有正文" in snapshots[2]["html"]
    assert snapshots[3]["html"] == snapshots[2]["html"]
    assert "第一篇独有正文" in snapshots[-1]["html"]
    assert len(snapshots[-1]["calls"]) == 2
    assert "投稿署名：测试投稿人" in snapshots[-1]["html"]
    assert "issues/1" in snapshots[-1]["html"]


def test_episode_fetch_failure_can_retry_without_reloading_the_page(tmp_path):
    result = build_public_site([record()], tmp_path)
    url = metadata(result)["episodes"][0]["data_url"]
    snapshots = client(result, [
        {"type": "reject", "url": url},
        {"type": "click", "selector": "[data-retry]"},
        {"type": "resolve", "url": url},
    ], hash="#/episode/episode-one")
    assert "文稿暂时未能加载" in snapshots[1]["html"]
    assert snapshots[2]["calls"] == [url, url]
    assert "完整正文" in snapshots[3]["html"]


def test_fulltext_search_loads_only_its_index_and_ignores_stale_query(tmp_path):
    result = build_public_site([record(content="needle全文词"), record("episode-two", 2, "其他正文")], tmp_path)
    snapshots = client(result, [
        {"type": "input", "value": "needle"},
        {"type": "resolve", "url": "search-index.json"},
        {"type": "input", "value": "其他正文"},
    ], hash="#/search")
    assert snapshots[0]["calls"] == []
    assert snapshots[1]["calls"] == ["search-index.json"]
    assert "episode-one" in snapshots[2]["results"] and "episode-two" not in snapshots[2]["results"]
    assert "episode-two" in snapshots[3]["results"] and "episode-one" not in snapshots[3]["results"]
    assert snapshots[3]["calls"] == ["search-index.json"]
    stale = client(result, [
        {"type": "input", "value": ""},
        {"type": "resolve", "url": "search-index.json"},
    ], hash="#/search?q=needle")
    assert "全部文稿 · 2 篇" in stale[-1]["results"]


def test_public_reader_escapes_body_attribution_and_title(tmp_path):
    source = record(content='<img src=x onerror="alert(1)">')
    source["submission"]["episode"]["title"] = "</script><svg onload=alert(1)>"
    source["submission"]["attribution"] = "<img src=x onerror=alert(1)>"
    source["provenance"]["payload_sha256"] = submission_digest(source["submission"])
    result = build_public_site([source], tmp_path)
    assert "</script><svg" not in result.read_text()
    url = metadata(result)["episodes"][0]["data_url"]
    snapshots = client(result, [{"type": "resolve", "url": url}], hash="#/episode/episode-one")
    assert "<img" not in snapshots[-1]["html"] and "<svg" not in snapshots[-1]["html"]
    assert "&lt;img" in snapshots[-1]["html"]


def paged_record(monkeypatch):
    monkeypatch.setattr("podcast_scribe.public_site.PAGED_TEXT_BYTES", 1)
    monkeypatch.setattr("podcast_scribe.public_site.PAGE_TEXT_BYTES", 25)
    source = record(content="第一页正文")
    episode = source["submission"]["episode"]
    episode["segments"] = [dict(episode["segments"][0], id=f"s{i}", start=i*10, end=(i+1)*10, text=f"第{i}页独有正文") for i in range(3)]
    episode["duration_seconds"] = 30
    episode["chapters"] = [{"id": "c1", "title": "开场", "start": 0, "segment_id": "s0"},
                           {"id": "c2", "title": "结尾", "start": 20, "segment_id": "s2"}]
    source["provenance"]["payload_sha256"] = submission_digest(source["submission"])
    return source


def test_large_body_is_stored_once_and_pages_removed_on_withdrawal(tmp_path, monkeypatch):
    source = paged_record(monkeypatch)
    result = build_public_site([source], tmp_path)
    url = metadata(result)["episodes"][0]["data_url"]
    manifest = json.loads((tmp_path / url).read_text())
    assert manifest["paginated"] is True and manifest["segments"] == manifest["turns"] == []
    assert manifest["chapters"][1]["page"] == 2
    assert manifest["downloads"] == {}
    assert "第0页" not in (tmp_path / "search-index.json").read_text()
    pages = [(tmp_path / page["url"]) for page in manifest["pages"]]
    segments = [segment for path in pages for segment in json.loads(path.read_text())["segments"]]
    assert segments == source["submission"]["episode"]["segments"]
    assert not (tmp_path / "downloads").exists()
    build_public_site([], tmp_path)
    assert all(not path.exists() for path in pages)


def test_large_reader_fetches_one_page_and_chapters_cross_pages(tmp_path, monkeypatch):
    result = build_public_site([paged_record(monkeypatch)], tmp_path)
    url = metadata(result)["episodes"][0]["data_url"]
    pages = json.loads((tmp_path / url).read_text())["pages"]
    snapshots = client(result, [
        {"type": "resolve", "url": url},
        {"type": "resolve", "url": pages[0]["url"]},
        {"type": "click", "selector": '[data-segment="s2"]'},
        {"type": "resolve", "url": pages[2]["url"]},
        {"type": "click", "selector": '[data-page="1"]'},
        {"type": "route", "hash": "#/"},
        {"type": "resolve", "url": pages[1]["url"]},
    ], hash="#/episode/episode-one")
    assert snapshots[1]["calls"] == [url, pages[0]["url"]]
    assert "第0页独有正文" in snapshots[2]["html"] and "第1页独有正文" not in snapshots[2]["html"]
    assert "第2页独有正文" in snapshots[4]["html"] and "第0页独有正文" not in snapshots[4]["html"]
    assert snapshots[-1]["html"] == snapshots[-2]["html"]
    search = client(result, hash="#/search")
    assert "大稿仅搜索标题" in search[0]["html"]


@pytest.mark.parametrize("basis,label", [
    (None, "人物与内容已校对"),
    ("automated", "自动整理完成"),
    ("user_accepted", "用户已确认采用当前稿"),
])
def test_large_reader_download_fetches_remaining_body_only_after_click(tmp_path, monkeypatch, basis, label):
    source = paged_record(monkeypatch)
    if basis:
        source["submission"]["episode"]["review"].update(mode="auto", basis=basis)
        source["provenance"]["payload_sha256"] = submission_digest(source["submission"])
    result = build_public_site([source], tmp_path)
    url = metadata(result)["episodes"][0]["data_url"]
    pages = json.loads((tmp_path / url).read_text())["pages"]
    snapshots = client(result, [
        {"type": "resolve", "url": url},
        {"type": "resolve", "url": pages[0]["url"]},
        {"type": "click", "selector": "[data-download-full]"},
        {"type": "resolve", "url": pages[0]["url"]},
        {"type": "resolve", "url": pages[1]["url"]},
        {"type": "resolve", "url": pages[2]["url"]},
    ], hash="#/episode/episode-one")
    assert snapshots[2]["calls"] == [url, pages[0]["url"]]
    assert snapshots[-1]["calls"] == [url, pages[0]["url"], *(page["url"] for page in pages)]
    assert len(snapshots[-1]["downloads"]) == 1
    markdown = snapshots[-1]["downloads"][0]
    assert all(f"第{index}页独有正文" in markdown for index in range(3))
    assert "投稿署名：测试投稿人" in markdown
    assert markdown.count(label) == 1
    assert snapshots[2]["html"].count(label) == 1
    if basis:
        assert "人物与内容已校对" not in markdown
        assert "说话人归属校对**：已确认" not in markdown


def test_site_total_limit_fails_before_touching_previous_output(tmp_path, monkeypatch):
    build_public_site([record()], tmp_path)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setattr("podcast_scribe.public_site.MAX_SITE_BYTES", 500)
    with pytest.raises(ValueError, match="1 GB"):
        build_public_site([record("other", 2)], tmp_path)
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_paged_reading_and_download_preserve_turns_anchors_and_metadata(tmp_path, monkeypatch):
    source = paged_record(monkeypatch)
    monkeypatch.setattr("podcast_scribe.public_site.PAGE_TEXT_BYTES", 12)
    episode = source["submission"]["episode"]
    episode["segments"] = [dict(episode["segments"][0], id=f"s{i}", start=i*10, end=(i+1)*10, text=value)
                           for i, value in enumerate(["Hello,", "world.", "今天", "很好"])]
    episode["duration_seconds"] = 40
    episode["chapters"] = [{"id": "c1", "title": "开场", "start": 0, "segment_id": "s0"},
                           {"id": "c2", "title": "句中章节", "start": 10, "segment_id": "s1"},
                           {"id": "c3", "title": "结尾", "start": 30, "segment_id": "s3"}]
    episode["references"] = [{"title": "人物核验", "url": "https://example.com/person"}]
    source["provenance"]["payload_sha256"] = submission_digest(source["submission"])
    result = build_public_site([source], tmp_path)
    url = metadata(result)["episodes"][0]["data_url"]
    pages = json.loads((tmp_path / url).read_text())["pages"]
    snapshots = client(result, [
        {"type": "resolve", "url": url},
        {"type": "resolve", "url": pages[0]["url"]},
        {"type": "click", "selector": "[data-download-full]"},
        *({"type": "resolve", "url": page["url"]} for page in pages),
    ], hash="#/episode/episode-one")
    html = snapshots[2]["html"]
    assert html.count('class="dialogue"') == 1
    assert '>Hello,</span>' in html and '> world.</span>' in html
    assert '<h3 class="transcript-chapter">句中章节' not in html
    markdown = snapshots[-1]["downloads"][0]
    assert markdown.count("**[") == 1  # Same speaker remains one turn across pages.
    assert 'Hello,<a id="segment-2"></a> world.<a id="segment-3"></a>今天<a id="segment-4"></a>很好' in markdown
    assert "### 句中章节" not in markdown
    for expected in ("#segment-2", "人物核验", "https://example.com/person", "访谈节目", "投稿账号：someone"):
        assert expected in markdown


@pytest.mark.parametrize('broken', ['{', '[]', '{}', '{"files":false}', '{"files":[5]}', '{"files":["../private"]}'])
def test_corrupt_existing_manifest_fails_without_orphaning_withdrawn_pages(tmp_path, monkeypatch, broken):
    build_public_site([paged_record(monkeypatch)], tmp_path)
    (tmp_path / '.public-site-manifest.json').write_text(broken)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='manifest'):
        build_public_site([], tmp_path)
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}


def series_catalog():
    return {"schema_version": 1, "series": [{
        "id": "confirmed-series", "title": "确认的访谈系列", "description": "维护者确认的节目介绍",
        "aliases": ["节目简称"], "sources": [{"title": "官方节目页", "url": "https://example.com/show"}],
    }]}


def series_overrides(issue="1"):
    return {"schema_version": 1, "issues": {issue: {
        "series_id": "confirmed-series", "evidence_url": "https://example.com/show/episode",
    }}}


def test_maintainer_series_override_is_consistent_without_changing_frozen_submission(tmp_path):
    source = record()
    before = deepcopy(source)
    catalog, overrides = series_catalog(), series_overrides()
    result = build_public_site([source], tmp_path, series_catalog=catalog, series_overrides=overrides)
    listing = metadata(result)["episodes"][0]
    expected = {key: catalog["series"][0][key] for key in ("id", "title", "description")}
    assert listing["series"] == expected
    public = json.loads((tmp_path / listing["data_url"]).read_text())
    assert public["series"] == expected
    assert public["provenance"] == before["provenance"]
    assert public["references"][-1] == {
        "title": "节目系列来源（维护者确认）",
        "url": overrides["issues"]["1"]["evidence_url"],
    }
    markdown = (tmp_path / public["downloads"]["markdown"]).read_text()
    assert "确认的访谈系列" in markdown and "访谈节目" not in markdown
    assert "节目系列来源（维护者确认）" in markdown and "https://example.com/show/episode" in markdown
    search = json.loads((tmp_path / "search-index.json").read_text())["episodes"][0]["text"]
    assert "确认的访谈系列" in search and "访谈节目" not in search
    assert json.loads((tmp_path / "series-catalog.json").read_text()) == catalog
    assert source == before and validate_record(source) == before
    snapshots = client(result, [{"type": "resolve", "url": listing["data_url"]},
                                {"type": "route", "hash": "#/series/confirmed-series"}],
                       hash="#/episode/episode-one")
    assert '#/series/confirmed-series' in snapshots[1]["html"]
    assert '#/series/series"' not in snapshots[1]["html"]
    assert "确认的访谈系列" in snapshots[-1]["html"]


def test_paged_override_and_download_use_the_same_series_and_evidence(tmp_path, monkeypatch):
    source = paged_record(monkeypatch)
    original = deepcopy(source)
    result = build_public_site([source], tmp_path, series_catalog=series_catalog(), series_overrides=series_overrides())
    listing = metadata(result)["episodes"][0]
    public = json.loads((tmp_path / listing["data_url"]).read_text())
    assert public["series"]["id"] == listing["series"]["id"] == "confirmed-series"
    assert public["provenance"]["payload_sha256"] == original["provenance"]["payload_sha256"]
    snapshots = client(result, [
        {"type": "resolve", "url": listing["data_url"]},
        {"type": "resolve", "url": public["pages"][0]["url"]},
        {"type": "click", "selector": "[data-download-full]"},
        *({"type": "resolve", "url": page["url"]} for page in public["pages"]),
    ], hash="#/episode/episode-one")
    markdown = snapshots[-1]["downloads"][0]
    assert "确认的访谈系列" in markdown and "访谈节目" not in markdown
    assert "节目系列来源（维护者确认）" in markdown and "https://example.com/show/episode" in markdown
    assert source == original


def test_known_series_uses_current_canonical_metadata_and_unknown_uses_neutral_label(tmp_path):
    known = record()
    known["submission"]["episode"]["series"].update(id="confirmed-series", title="旧名称")
    known["provenance"]["payload_sha256"] = submission_digest(known["submission"])
    unknown = record("unknown", 2)
    unknown["submission"]["episode"]["series"].update(id="inbox", title="待归类", description="")
    unknown["provenance"]["payload_sha256"] = submission_digest(unknown["submission"])
    catalog = series_catalog()
    catalog["series"][0]["aliases"].append("旧名称")
    result = build_public_site([known, unknown], tmp_path, series_catalog=catalog)
    listings = {item["id"]: item for item in metadata(result)["episodes"]}
    assert listings["episode-one"]["series"]["title"] == "确认的访谈系列"
    assert listings["unknown"]["series"]["title"] == "未分类"
    assert known["submission"]["episode"]["series"]["title"] == "旧名称"
    assert unknown["submission"]["episode"]["series"]["title"] == "待归类"


def test_catalog_id_collision_requires_an_explicit_maintainer_decision(tmp_path):
    source = record()
    source["submission"]["episode"]["series"].update(id="confirmed-series", title="另一个同 ID 节目")
    source["provenance"]["payload_sha256"] = submission_digest(source["submission"])
    before = deepcopy(source)
    with pytest.raises(ValueError, match="title does not"):
        build_public_site([source], tmp_path / "site", series_catalog=series_catalog())
    assert not (tmp_path / "site").exists()
    result = build_public_site([source], tmp_path / "site", series_catalog=series_catalog(),
                               series_overrides=series_overrides())
    assert metadata(result)["episodes"][0]["series"]["title"] == "确认的访谈系列"
    assert source == before


def test_maintainer_can_retract_a_wrong_series_to_unclassified(tmp_path):
    source = record()
    original = deepcopy(source)
    overrides = series_overrides()
    overrides["issues"]["1"]["series_id"] = "inbox"
    result = build_public_site([source], tmp_path, series_catalog=series_catalog(), series_overrides=overrides)
    listing = metadata(result)["episodes"][0]
    assert listing["series"] == {"id": "inbox", "title": "未分类", "description": ""}
    public = json.loads((tmp_path / listing["data_url"]).read_text())
    markdown = (tmp_path / public["downloads"]["markdown"]).read_text()
    assert "未分类" in markdown and "访谈节目" not in markdown
    assert public["provenance"] == original["provenance"] and source == original


def test_catalog_is_published_without_episodes_and_stale_issue_override_does_not_block_withdrawal(tmp_path):
    result = build_public_site([], tmp_path, series_catalog=series_catalog(), series_overrides=series_overrides("123"))
    assert metadata(result)["episodes"] == []
    assert json.loads((tmp_path / "series-catalog.json").read_text()) == series_catalog()


def test_conflicting_series_ids_require_an_override_instead_of_merging_unrelated_routes(tmp_path):
    first, second = record(), record("second", 2)
    second["submission"]["episode"]["series"]["title"] = "另一个系列"
    second["provenance"]["payload_sha256"] = submission_digest(second["submission"])
    with pytest.raises(ValueError, match="conflicting titles"):
        build_public_site([first, second], tmp_path / "site")
    assert not (tmp_path / "site").exists()
    result = build_public_site([first, second], tmp_path / "site", series_catalog=series_catalog(),
                               series_overrides=series_overrides("2"))
    assert {item["series"]["id"] for item in metadata(result)["episodes"]} == {"series", "confirmed-series"}


@pytest.mark.parametrize("invalid", [
    {}, {"schema_version": True, "issues": {}}, {"schema_version": 1, "issues": [], "extra": 1},
    {"schema_version": 1, "issues": {"0": {"series_id": "confirmed-series", "evidence_url": "https://example.com"}}},
    {"schema_version": 1, "issues": {"01": {"series_id": "confirmed-series", "evidence_url": "https://example.com"}}},
    {"schema_version": 1, "issues": {"1": {"series_id": "unknown", "evidence_url": "https://example.com"}}},
    {"schema_version": 1, "issues": {"1": {"series_id": "confirmed-series"}}},
    {"schema_version": 1, "issues": {"1": {"series_id": "confirmed-series", "evidence_url": "https://example.com", "title": "other"}}},
    *({"schema_version": 1, "issues": {"1": {"series_id": "confirmed-series", "evidence_url": url}}}
      for url in [None, "javascript:alert(1)", "file:///private/local", "https://user:secret@example.com", "https://example.com:notaport"]),
])
def test_invalid_series_override_cannot_change_existing_site(tmp_path, invalid):
    build_public_site([record()], tmp_path)
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        build_public_site([record()], tmp_path, series_catalog=series_catalog(), series_overrides=invalid)
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}


def test_override_loader_and_catalog_output_have_path_and_size_checks(tmp_path, monkeypatch):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps(series_overrides()))
    assert load_series_overrides(path) == series_overrides()
    path.write_text('{"schema_version":1,"schema_version":1,"issues":{}}')
    with pytest.raises(ValueError, match="JSON"):
        load_series_overrides(path)
    path.write_text('{"schema_version":1,"issues":{"1":NaN}}')
    with pytest.raises(ValueError, match="JSON"):
        load_series_overrides(path)
    monkeypatch.setattr("podcast_scribe.public_site.MAX_SERIES_OVERRIDES_BYTES", 10)
    with pytest.raises(ValueError, match="1 MiB"):
        load_series_overrides(path)
    output = tmp_path / "site"
    output.mkdir()
    (output / "series-catalog.json").symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        build_public_site([], output)
    assert not (output / "index.html").exists()


def test_public_build_script_reads_default_and_custom_series_overrides(tmp_path, monkeypatch):
    builder = builder_module()
    content = tmp_path / "content"
    episodes = content / "episodes"
    episodes.mkdir(parents=True)
    (episodes / "issue-1.json").write_text(json.dumps(record()))
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(series_catalog()))
    default_overrides = content / "series-overrides.json"
    default_overrides.write_text(json.dumps(series_overrides()))
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    output = tmp_path / "out"
    assert builder.main(["--output-dir", str(output), "--series-catalog", str(catalog_file)]) == 0
    assert metadata(output / "index.html")["episodes"][0]["series"]["id"] == "confirmed-series"
    alternate = tmp_path / "other-overrides.json"
    alternate.write_text('{"schema_version":1,"issues":{}}')
    assert builder.main(["--output-dir", str(output), "--series-catalog", str(catalog_file),
                         "--series-overrides", str(alternate)]) == 0
    assert metadata(output / "index.html")["episodes"][0]["series"]["id"] == "series"
