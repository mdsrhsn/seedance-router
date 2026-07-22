"""BytePlus ModelArk client.

Everything that touches the BytePlus wire format lives in this file. When you run
your first real curl against the API and the response looks different from what is
assumed here, this is the only file that needs editing.

Assumed contract (Seedance video generation, async submit-then-poll):

    POST {base}/contents/generations/tasks
        -> {"id": "cgt-2026...."}

    GET  {base}/contents/generations/tasks/{task_id}
        -> {"id": ..., "status": "queued|running|succeeded|failed",
            "content": {"video_url": "https://..."},
            "usage": {...}, "error": {"message": "..."}}
"""

import requests

TIMEOUT = 60


class ArkError(Exception):
    """Raised when ModelArk returns a non-2xx response.

    `status_code` is what the router uses to decide whether to cool the key down,
    mark it exhausted, or disable it outright.
    """

    def __init__(self, status_code, message, body=None):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.body = body or {}


def _headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _raise_for_status(response):
    if response.status_code < 400:
        return
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text[:500]}
    message = (
        body.get("error", {}).get("message")
        if isinstance(body.get("error"), dict)
        else body.get("message") or response.text[:300]
    )
    raise ArkError(response.status_code, message or "unknown error", body)


def build_content(prompt, workflow="t2v", image_url="", duration=5, resolution="720p"):
    """Assemble the `content` array. Seedance takes generation parameters as
    inline flags appended to the prompt text."""
    text = f"{prompt} --resolution {resolution} --duration {duration}"
    content = [{"type": "text", "text": text}]
    if workflow in ("i2v", "f2f", "r2v") and image_url:
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    return content


def submit_task(base_url, api_key, model_id, content):
    """Submit a generation. Returns the ModelArk task ID."""
    response = requests.post(
        f"{base_url}/contents/generations/tasks",
        headers=_headers(api_key),
        json={"model": model_id, "content": content},
        timeout=TIMEOUT,
    )
    _raise_for_status(response)
    data = response.json()
    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise ArkError(response.status_code, f"No task id in response: {data}", data)
    return task_id, data


def get_task(base_url, api_key, task_id):
    """Poll a task. Returns (status, video_url, raw_body).

    status is normalised to one of: queued, running, succeeded, failed.
    """
    response = requests.get(
        f"{base_url}/contents/generations/tasks/{task_id}",
        headers=_headers(api_key),
        timeout=TIMEOUT,
    )
    _raise_for_status(response)
    data = response.json()

    raw_status = (data.get("status") or "").lower()
    status = {
        "queued": "queued",
        "pending": "queued",
        "running": "running",
        "in_progress": "running",
        "succeeded": "succeeded",
        "success": "succeeded",
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "failed",
        "error": "failed",
    }.get(raw_status, "running")

    content = data.get("content") or {}
    video_url = content.get("video_url") or data.get("video_url") or ""

    return status, video_url, data


def check_health(base_url, api_key):
    """Cheap validity check for a key. A 401/403 means the key is bad; anything
    else means it at least authenticates."""
    try:
        response = requests.get(
            f"{base_url}/contents/generations/tasks/health-probe-does-not-exist",
            headers=_headers(api_key),
            timeout=20,
        )
    except requests.RequestException as exc:
        return False, str(exc)

    if response.status_code in (401, 403):
        return False, "Key rejected by ModelArk (401/403)"
    return True, "Key authenticates"
