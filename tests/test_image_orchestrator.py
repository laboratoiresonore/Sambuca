"""The thing that finally posts the graph, driven against a real socket.

THE PICTURE HALF HAD EVERYTHING EXCEPT THIS. A pinned checkpoint, a hardened
ComfyUI with its arbitrary-code path disabled, a tmpfs for outputs, a curated
API-format graph — and nothing that loaded the graph, filled it in, or posted
it. The workflow README described this program in the present tense while it did
not exist.

The stub speaks ComfyUI's actual protocol on a real port rather than being a
mock, for the same reason the beacon tests spawn a real process: the interesting
failures here are a body posted in the wrong shape and a poll that reads the
wrong field, and a mock agrees with whatever the code already does.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOL = REPO / "engine" / "image" / "sambuca-image.py"
WORKFLOW = REPO / "compose/config/comfyui/workflows/flux-schnell.json"


def _load():
    spec = importlib.util.spec_from_file_location("sambuca_image", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SI = _load()
GRAPH = json.loads(WORKFLOW.read_text(encoding="utf-8"))


def test_the_default_workflow_path_is_the_one_the_container_sees() -> None:
    """The orchestrator runs INSIDE the comfyui container, not on the host.

    ComfyUI has no ports mapping on purpose — the Caddyfile calls an ungated
    route to it a remote-code-execution surface — so sambuca-image goes to it
    via docker exec rather than reaching in from outside. That means the default
    must be the path compose mounts INSIDE the container. The first version used
    the host path, which does not exist in there: the program would have shipped
    wired up and unable to find its own graph.
    """
    import yaml
    compose = yaml.safe_load((REPO / "compose/image.yml").read_text(encoding="utf-8"))
    mounts = compose["services"]["comfyui"]["volumes"]
    target = next(m.split(":")[1] for m in mounts if "workflows" in m)
    assert str(SI.DEFAULT_WORKFLOW).replace("\\", "/").startswith(target), (
        f"default is {SI.DEFAULT_WORKFLOW}, but compose mounts the workflows at "
        f"{target} inside the container")


def test_the_orchestrator_is_mounted_where_the_wrapper_runs_it() -> None:
    """A command that is installed but whose payload is not there is worse than
    one that is missing: it fails at the moment somebody first tries it."""
    compose = (REPO / "compose/image.yml").read_text(encoding="utf-8")
    wrapper = (REPO / "engine/image/sambuca-image").read_text(encoding="utf-8")
    assert "sambuca-image.py:/opt/sambuca-image.py:ro" in compose, (
        "the orchestrator is not mounted into the container")
    assert "/opt/sambuca-image.py" in wrapper, (
        "the wrapper runs a path the compose file does not provide")


# --------------------------------------------------------------- the stub


class _Comfy(BaseHTTPRequestHandler):
    posted: dict = {}
    ready_after: int = 0          # polls before the picture "exists"
    polls: int = 0
    fail: bool = False

    def log_message(self, *a):    # keep the test output readable
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):            # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        _Comfy.posted = json.loads(self.rfile.read(n) or b"{}")
        self._json(200, {"prompt_id": "abc-123"})

    def do_GET(self):             # noqa: N802
        _Comfy.polls += 1
        if _Comfy.fail:
            self._json(200, {"abc-123": {"status": {"status_str": "error"}}})
            return
        if _Comfy.polls <= _Comfy.ready_after:
            self._json(200, {})           # not finished yet
            return
        self._json(200, {"abc-123": {
            "status": {"status_str": "success"},
            "outputs": {"6": {"images": [{"filename": "sambuca_00001_.png"}]}},
        }})


@pytest.fixture
def comfy():
    _Comfy.posted, _Comfy.polls, _Comfy.ready_after, _Comfy.fail = {}, 0, 0, False
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    srv = HTTPServer(("127.0.0.1", port), _Comfy)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{port}", _Comfy
    finally:
        srv.shutdown()
        srv.server_close()


# ------------------------------------------------- substitution is structural


def test_a_prompt_cannot_become_structure() -> None:
    """THE SECURITY ARGUMENT OF THE WHOLE FILE.

    %PROMPT% sits inside a JSON string. Read the file as text, str.replace, then
    parse — the obvious implementation — and a double quote in the prompt ends
    the string and starts writing graph. ComfyUI executes whatever graph it is
    handed.
    """
    evil = ('", "99": {"class_type": "SaveImage", "inputs": '
            '{"filename_prefix": "/etc/passwd"}}, "x": {"y": "')
    graph = SI.build_graph(GRAPH, prompt=evil, width=512, height=512,
                           steps=4, seed=1)
    assert len(graph) == len(GRAPH), "the prompt added or removed nodes"
    assert "99" not in graph
    assert graph["2"]["inputs"]["text"] == evil, "the prompt was mangled"


@pytest.mark.parametrize("hostile", [
    '{"not": "a graph"}',
    "backslash \\ and \"quotes\"",
    "newline\nand\ttab",
    "%STEPS%",                     # a placeholder INSIDE the prompt
    "🖼" * 50,
])
def test_hostile_prompts_survive_as_data(hostile: str) -> None:
    graph = SI.build_graph(GRAPH, prompt=hostile, width=512, height=512,
                           steps=4, seed=1)
    assert len(graph) == len(GRAPH)
    # It must arrive exactly as typed — including a prompt that looks like a
    # placeholder, which a second substitution pass would have eaten.
    assert graph["2"]["inputs"]["text"] == hostile


def test_numbers_arrive_as_numbers() -> None:
    """ComfyUI validates types; "4" is not 4 and the graph would be refused."""
    g = SI.build_graph(GRAPH, prompt="x", width=768, height=512, steps=4, seed=9)
    assert g["5"]["inputs"]["steps"] == 4
    assert isinstance(g["5"]["inputs"]["steps"], int)
    assert g["5"]["inputs"]["seed"] == 9
    assert g["4"]["inputs"]["width"] == 768
    assert isinstance(g["4"]["inputs"]["width"], int)
    assert "%" not in json.dumps(g), "a placeholder was left behind"


# ------------------------------------------------------------------ bounds


@pytest.mark.parametrize(("kw", "why"), [
    ({"steps": 4000}, "steps"),
    ({"steps": 0}, "steps"),
    ({"width": 100}, "width"),
    ({"width": 4096}, "width"),
    ({"height": 513}, "multiple"),
    ({"prompt": "   "}, "empty"),
    ({"prompt": "x" * 5000}, "limit"),
])
def test_out_of_range_is_refused_with_a_reason(kw: dict, why: str) -> None:
    """A refusal has to say which number and what the limit is — the owner
    cannot read the source to find out."""
    args = {"prompt": "a bicycle", "width": 512, "height": 512,
            "steps": 4, "seed": 1}
    args.update(kw)
    with pytest.raises(SI.ImageError) as exc:
        SI.build_graph(GRAPH, **args)
    assert why in str(exc.value).lower()


# ------------------------------------------------------- the wire, for real


def test_it_posts_a_graph_the_service_can_read(comfy) -> None:
    base, stub = comfy
    graph = SI.build_graph(GRAPH, prompt="a red bicycle", width=512,
                           height=512, steps=4, seed=1)
    prompt_id = SI.submit(base, graph, client_id="test")
    assert prompt_id == "abc-123"
    # ComfyUI wants {"prompt": <graph>, "client_id": ...}, not the bare graph.
    assert set(stub.posted) >= {"prompt", "client_id"}
    assert stub.posted["prompt"]["2"]["inputs"]["text"] == "a red bicycle"


def test_it_waits_and_returns_the_filename(comfy) -> None:
    base, stub = comfy
    stub.ready_after = 2                      # not finished on the first polls
    names = SI.wait_for(base, "abc-123", timeout=10, poll=0,
                        sleep=lambda _s: None)
    assert names == ["sambuca_00001_.png"]
    assert stub.polls >= 3, "it did not actually poll"


def test_a_graph_the_service_rejects_is_reported_not_retried(comfy) -> None:
    base, stub = comfy
    stub.fail = True
    with pytest.raises(SI.ImageError) as exc:
        SI.wait_for(base, "abc-123", timeout=10, poll=0, sleep=lambda _s: None)
    assert "could not run" in str(exc.value)


def test_it_gives_up_rather_than_hanging(comfy) -> None:
    """A hang is indistinguishable from slowness, and on a CPU-only machine
    slowness is expected — so the difference has to be made explicit."""
    base, stub = comfy
    stub.ready_after = 10_000
    with pytest.raises(SI.ImageError) as exc:
        SI.wait_for(base, "abc-123", timeout=0.05, poll=0, sleep=lambda _s: None)
    assert "no picture after" in str(exc.value)


def test_an_absent_service_says_so_in_plain_words() -> None:
    """Image generation is optional and off on small machines. "Connection
    refused" is not an answer somebody can act on."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead = s.getsockname()[1]
    s.close()
    with pytest.raises(SI.ImageError) as exc:
        SI.submit(f"http://127.0.0.1:{dead}", {"1": {}}, client_id="t")
    msg = str(exc.value)
    assert "cannot reach the image service" in msg
    assert "switched off" in msg, "it must name the likely cause"
