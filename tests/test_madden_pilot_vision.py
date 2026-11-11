"""Vision connector and comparison-test tests (MADDEN_SPIKE_AGENT_PLAN section 4).

No live model, no network: every call goes through a fake client. Images are synthetic PNGs
written into tmp_path.
"""
import base64
import io
import json

import pytest
import requests
from PIL import Image

from tools.madden_pilot import vision
from tools.madden_pilot import vision_bench


# --------------------------------------------------------------------------- helpers
GOOD = json.dumps({
    "kind": "menu",
    "title": "MAIN MENU",
    "options": ["PLAY", "SETTINGS"],
    "selected": "PLAY",
    "overlays": ["ticker bottom-left"],
    "warning": None,
    "summary": "The main menu with two options.",
})


def png(path, size=(320, 180), colour=(40, 80, 120)):
    Image.new("RGB", size, colour).save(path)
    return path


class FakeOllama:
    """Stands in for the Ollama call: records kwargs and returns a fixed answer."""

    def __init__(self, answer=GOOD, error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.answer


class FakeDeepSeek:
    """Stands in for an OpenAI-compatible client with chat.completions.create."""

    def __init__(self, answer=GOOD, error=None):
        self.answer = answer
        self.error = error
        self.calls = []
        outer = self

        class Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer.error:
                    raise outer.error
                return {"choices": [{"message": {"content": outer.answer}}]}

        class Chat:
            completions = Completions()

        self.chat = Chat()


class Router(FakeOllama):
    """One test client that serves both backends: generate() for Ollama, chat for DeepSeek."""

    def __init__(self, ollama_error=None, deepseek_answer=GOOD, deepseek_error=None):
        super().__init__(error=ollama_error)
        self.deepseek = FakeDeepSeek(deepseek_answer, deepseek_error)

    @property
    def chat(self):
        return self.deepseek.chat


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Point the choice file at tmp and clear ambient vision env vars for every test."""
    choice = tmp_path / "vision_choice.json"
    choice.write_text("{}", encoding="utf-8")
    monkeypatch.setenv(vision.ENV_VISION_CHOICE, str(choice))
    monkeypatch.delenv(vision.ENV_VISION, raising=False)
    monkeypatch.delenv(vision.ENV_VISION_HEAVY, raising=False)
    monkeypatch.setenv(vision.ENV_VISION_FALLBACK, "none")
    return choice


def written_choice(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


# --------------------------------------------------------------------------- describe
def test_describe_reads_good_json(tmp_path):
    frame = png(tmp_path / "frame.png")
    client = FakeOllama(GOOD)
    desc = vision.describe(frame, backend="ollama:test-model", client=client)
    assert desc.kind == "menu"
    assert desc.title == "MAIN MENU"
    assert desc.options == ("PLAY", "SETTINGS")
    assert desc.selected == "PLAY"
    assert desc.overlays == ("ticker bottom-left",)
    assert desc.warning is None
    assert desc.summary.startswith("The main menu")
    assert desc.model == "ollama:test-model"
    assert desc.seconds >= 0.0
    assert client.calls[0]["model"] == "test-model"
    assert client.calls[0]["images"]


def test_describe_tolerates_code_fences_and_prose(tmp_path):
    frame = png(tmp_path / "frame.png")
    answer = "Here you go:\n```json\n" + GOOD + "\n```\nDone."
    desc = vision.describe(frame, backend="ollama:test", client=FakeOllama(answer))
    assert desc.kind == "menu"
    assert desc.options == ("PLAY", "SETTINGS")


def test_describe_malformed_json_goes_to_other(tmp_path):
    frame = png(tmp_path / "frame.png")
    desc = vision.describe(frame, backend="ollama:test",
                           client=FakeOllama("I cannot read this screenshot."))
    assert desc.kind == "other"
    assert "I cannot read this screenshot." in desc.summary
    assert desc.options == ()


def test_describe_unknown_kind_is_coerced(tmp_path):
    frame = png(tmp_path / "frame.png")
    answer = json.dumps({"kind": "scoreboard", "summary": "A scoreboard."})
    desc = vision.describe(frame, backend="ollama:test", client=FakeOllama(answer))
    assert desc.kind == "other"
    assert desc.summary == "A scoreboard."


def test_describe_unreachable_raises(tmp_path):
    frame = png(tmp_path / "frame.png")
    client = FakeOllama(error=requests.ConnectionError("connection refused"))
    with pytest.raises(vision.VisionUnavailable):
        vision.describe(frame, backend="ollama:test", client=client)


def test_describe_missing_frame_raises_unavailable(tmp_path):
    with pytest.raises(vision.VisionUnavailable):
        vision.describe(tmp_path / "nope.png", backend="ollama:test",
                        client=FakeOllama(GOOD))


# --------------------------------------------------------------------------- fallback chain
def test_primary_down_deepseek_answers(tmp_path, isolated_config, monkeypatch):
    written_choice(isolated_config, {"deepseek_images": True})
    monkeypatch.delenv(vision.ENV_VISION_FALLBACK, raising=False)
    frame = png(tmp_path / "frame.png")
    client = Router(ollama_error=requests.ConnectionError("down"))
    desc = vision.describe(frame, backend="ollama:qwen3.5:4b", client=client)
    assert desc.model == "deepseek:deepseek-flash"
    assert desc.kind == "menu"
    assert client.deepseek.calls, "the fallback must make the DeepSeek call"


def test_fallback_none_raises(tmp_path, isolated_config, monkeypatch):
    written_choice(isolated_config, {"deepseek_images": False})
    monkeypatch.delenv(vision.ENV_VISION_FALLBACK, raising=False)
    frame = png(tmp_path / "frame.png")
    client = Router(ollama_error=requests.ConnectionError("down"))
    with pytest.raises(vision.VisionUnavailable):
        vision.describe(frame, backend="ollama:qwen3.5:4b", client=client)
    assert client.deepseek.calls == []


def test_primary_answering_does_not_call_deepseek(tmp_path, isolated_config, monkeypatch):
    written_choice(isolated_config, {"deepseek_images": True})
    monkeypatch.delenv(vision.ENV_VISION_FALLBACK, raising=False)
    frame = png(tmp_path / "frame.png")
    client = Router()
    desc = vision.describe(frame, backend="ollama:qwen3.5:4b", client=client)
    assert desc.model == "ollama:qwen3.5:4b"
    assert client.deepseek.calls == []


# --------------------------------------------------------------------------- DeepSeek request
def test_deepseek_request_carries_downscaled_image_data_uri(tmp_path):
    frame = png(tmp_path / "wide.png", size=(2000, 1000))
    client = FakeDeepSeek(GOOD)
    desc = vision.describe(frame, backend="deepseek:deepseek-flash", client=client)
    assert desc.model == "deepseek:deepseek-flash"
    request = client.calls[0]
    assert request["model"] == "deepseek-flash"
    content = request["messages"][0]["content"]
    text_parts = [part for part in content if part.get("type") == "text"]
    image_parts = [part for part in content if part.get("type") == "image_url"]
    assert text_parts and "Madden" in text_parts[0]["text"]
    assert len(image_parts) == 1
    uri = image_parts[0]["image_url"]["url"]
    assert uri.startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as sent:
        assert sent.width == vision.MAX_WIDTH == 1280
        assert sent.height == 640


def test_ollama_tag_keeps_its_own_colon(tmp_path):
    frame = png(tmp_path / "frame.png")
    client = FakeOllama(GOOD)
    vision.describe(frame, backend="ollama:qwen3.5:4b", client=client)
    assert client.calls[0]["model"] == "qwen3.5:4b"


# --------------------------------------------------------------------------- configuration
def test_backend_parsing():
    assert vision.parse_backend("ollama:qwen3.5:4b") == ("ollama", "qwen3.5:4b")
    assert vision.parse_backend("deepseek:deepseek-flash") == ("deepseek", "deepseek-flash")
    with pytest.raises(ValueError):
        vision.parse_backend("ollama")
    with pytest.raises(ValueError):
        vision.parse_backend("gemini:flash")


def test_env_backend_wins(tmp_path, monkeypatch):
    monkeypatch.setenv(vision.ENV_VISION, "ollama:from-env")
    assert vision.resolve_backend() == "ollama:from-env"
    assert vision.resolve_backend("ollama:base", model="override") == "ollama:override"


def test_choice_file_backend_and_heavy(tmp_path, isolated_config):
    written_choice(isolated_config, {"live": "ollama:live-pick", "heavy": "ollama:heavy-pick"})
    assert vision.default_backend() == "ollama:live-pick"
    assert vision.heavy_backend() == "ollama:heavy-pick"


def test_missing_choice_file_falls_back_gracefully(tmp_path, monkeypatch):
    monkeypatch.setenv(vision.ENV_VISION_CHOICE, str(tmp_path / "missing.json"))
    monkeypatch.delenv(vision.ENV_VISION, raising=False)
    assert vision.read_vision_choice() == {}
    assert vision.default_backend() == vision.DEFAULT_LIVE


# --------------------------------------------------------------------------- vision_bench
def _desc(kind, options=(), selected=None, seconds=1.0):
    return vision.ScreenDescription(kind=kind, title=None, options=tuple(options),
                                    selected=selected, overlays=(), warning=None,
                                    summary="", model="fake", seconds=seconds)


def _five_labels():
    return [
        {"png": "f0", "kind": "menu", "options": ["A", "B"], "selected": "A", "layout": None},
        {"png": "f1", "kind": "hub", "options": [], "selected": None, "layout": None},
        {"png": "f2", "kind": "gameplay", "options": [], "selected": None, "layout": "sn"},
        {"png": "f3", "kind": "dialog", "options": ["YES"], "selected": "NO", "layout": None},
        {"png": "f4", "kind": "loading", "options": [], "selected": None, "layout": None},
    ]


def test_vision_bench_scores_three_models_and_picks_within_budget(tmp_path, monkeypatch):
    labels = _five_labels()
    answers = {
        "ollama:small": [_desc("menu", ["A"], "A", 1.0), _desc("hub", (), None, 1.0),
                         _desc("dialog", (), None, 1.0), _desc("dialog", [], None, 1.0),
                         _desc("menu", (), None, 1.0)],
        "ollama:mid": [_desc("menu", ["A", "B"], "A", 2.0), _desc("hub", (), None, 2.0),
                       _desc("gameplay", (), None, 2.0), _desc("dialog", ["YES"], "NO", 2.0),
                       _desc("menu", (), None, 2.0)],
        "ollama:big": [_desc("menu", ["A", "B"], "A", 4.0), _desc("hub", (), None, 4.0),
                       _desc("gameplay", (), None, 4.0), _desc("dialog", ["YES"], "NO", 4.0),
                       _desc("loading", (), None, 4.0)],
    }
    layouts = {"ollama:small": "standard", "ollama:mid": "sn", "ollama:big": "sn"}
    vram = {"ollama:small": 3.0, "ollama:mid": 4.5, "ollama:big": 7.0}
    calls = {}

    def describe_fn(png, *, backend, timeout=30.0):
        calls.setdefault(backend, []).append(png)
        return answers[backend][int(png[1:])]

    def layout_fn(png, *, backend, timeout=30.0):
        return layouts[backend]

    result = vision_bench.bench(["ollama:small", "ollama:mid", "ollama:big"], labels,
                                describe_fn=describe_fn, layout_fn=layout_fn,
                                vram_fn=vram.__getitem__, budget_gb=5.0)

    scores = result["models"]
    assert scores["ollama:small"]["kind_acc"] == pytest.approx(0.6)
    assert scores["ollama:mid"]["kind_acc"] == pytest.approx(0.8)
    assert scores["ollama:big"]["kind_acc"] == pytest.approx(1.0)
    assert scores["ollama:mid"]["selected_acc"] == pytest.approx(1.0)
    assert scores["ollama:mid"]["layout_acc"] == pytest.approx(1.0)
    assert scores["ollama:small"]["layout_acc"] == pytest.approx(0.0)
    assert scores["ollama:mid"]["median_s"] == pytest.approx(2.0)
    assert scores["ollama:mid"]["p95_s"] == pytest.approx(2.0)
    assert result["live"] == "ollama:mid"          # 4.5 GB fits the 5 GB budget
    assert result["heavy"] == "ollama:big"         # best score overall, over budget
    assert result["labels"] == 5
    assert calls["ollama:mid"] == ["f0", "f1", "f2", "f3", "f4"]

    markdown, choice = vision_bench.write_outputs(result, tmp_path / "bench")
    assert markdown.exists() and choice.exists()
    document = json.loads(choice.read_text())
    assert document["live"] == "ollama:mid"
    assert document["heavy"] == "ollama:big"
    assert "| ollama:mid |" in markdown.read_text()


def test_vision_bench_marks_a_refusing_model_no_vision(tmp_path):
    labels = _five_labels()
    good = _desc("menu")
    blind = "ollama:blind"

    def describe_fn(png, *, backend, timeout=30.0):
        if backend == blind:
            raise vision.VisionUnavailable("no image support")
        return good

    result = vision_bench.bench(["ollama:ok", blind], labels,
                                describe_fn=describe_fn, layout_fn=lambda *a, **k: None,
                                vram_fn=lambda model: 2.0)
    assert result["models"][blind]["status"] == "no vision"
    assert result["models"][blind]["frames"] == 0
    assert result["live"] == "ollama:ok"


def test_bench_parser_help_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        vision_bench.build_parser().parse_args(["--help"])
    assert excinfo.value.code == 0


def test_parse_layout():
    assert vision_bench.parse_layout("SN") == "sn"
    assert vision_bench.parse_layout("Sunday Night Football") == "sn"
    assert vision_bench.parse_layout("standard") == "standard"
    assert vision_bench.parse_layout("none") is None


# --------------------------------------------------------------------------- labels
def test_build_labels_maps_catalog_screens_and_presentation(tmp_path):
    from PIL import ImageDraw

    steps = tmp_path / "runs" / "2026-11-11_1200_test" / "steps"
    steps.mkdir(parents=True)
    frame = steps / "0007_HUB_GAMEDAY.png"
    image = Image.new("RGB", (200, 120), (20, 20, 30))
    ImageDraw.Draw(image).rectangle([20, 60, 180, 62], fill=(255, 255, 255))
    image.save(frame)
    frame.with_suffix(".json").write_text(json.dumps({"words": [
        {"text": "ADVANCE WEEK", "box": [20, 63, 160, 15], "line": 0},
        {"text": "MANAGE ROSTER", "box": [20, 10, 160, 15], "line": 1},
    ]}))

    presentation = tmp_path / "presentation.json"
    presentation.write_text(json.dumps([
        {"frame": str(tmp_path / "snap.png"), "kind": "gameplay", "layout": "sn"},
    ]))
    labels = vision_bench.build_labels(pilot_root=tmp_path / "runs",
                                       presentation_path=presentation)
    assert [label["source"] for label in labels] == ["catalog", "presentation"]
    catalog = labels[0]
    assert catalog["screen"] == "HUB_GAMEDAY"
    assert catalog["kind"] == "hub"
    assert catalog["options"] == ["WEEKLY RECAP", "ADVANCE WEEK", "PLAY SEASON GAME"]
    assert catalog["selected"] == "ADVANCE WEEK"
    assert labels[1]["kind"] == "gameplay"
    assert labels[1]["layout"] == "sn"

    # A missing presentation file is skipped quietly.
    assert len(vision_bench.build_labels(pilot_root=tmp_path / "runs",
                                         presentation_path=tmp_path / "absent.json")) == 1
