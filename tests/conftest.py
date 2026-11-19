import json

import pytest
from image_variations.adapters.mock import fixture_profile
from image_variations.demo import fixture_creative, fixture_review
from image_variations.jobs import create_job, get_status, review_job, step_job
from image_variations.request import resolve_request


def request_for(mode="grid", **output):
    return {
        "input": {"user_prompt": "同一个陶瓷杯的视觉变化"},
        "output": {"mode": mode, **output},
        "variation": {"direction": "state"},
    }


def elapse_backoff(root):
    path = root / "job.json"
    state = json.loads(path.read_text())
    for unit in state["units"]:
        unit.pop("retry_not_before", None)
    path.write_text(json.dumps(state))


def finish(root, provider):
    for _ in range(100):
        status = get_status(root)
        if status["status"] in {"completed", "partial_failed", "failed"}:
            return status
        elapse_backoff(root)
        status = step_job(root, provider)
        if status["pending_review"]:
            review_job(root, fixture_review(root))
    raise AssertionError("job did not terminate")


@pytest.fixture
def make_job(tmp_path):
    def make(mode="grid", *, direction="state", retries=None, **output):
        request = request_for(mode, **output)
        request["variation"]["direction"] = direction
        if retries is not None:
            request["max_extra_retries"] = retries
        task = resolve_request(request)
        root = tmp_path / (mode + str(len(list(tmp_path.iterdir()))))
        create_job(
            root, request, fixture_creative(task), fixture_profile(), tmp_path / "out", environment="mock"
        )
        return root

    return make
