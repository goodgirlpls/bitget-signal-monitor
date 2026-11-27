import os
import json
import base64
import urllib.request
import urllib.error


REPOSITORY = "goodgirlpls/bitget-signal-monitor"
BRANCH = "main"
STATE_PATH = "position_state.json"

API_URL = (
    "https://api.github.com/repos/"
    + REPOSITORY
    + "/contents/"
    + STATE_PATH
)


def github_request(method, body=None):

    token = os.environ.get("GITHUB_TOKEN")

    if not token:
        raise RuntimeError("Missing GitHub token")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    url = API_URL

    if method == "GET":
        url += "?ref=" + BRANCH

    data = None

    if body is not None:
        data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(
        url,
        headers=headers,
        data=data,
        method=method
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:

            return json.load(response)

    except urllib.error.HTTPError as error:

        raise RuntimeError(
            f"State API failed: HTTP {error.code}"
        ) from None


def validate_state(state):

    if not isinstance(state, dict):
        raise RuntimeError("Invalid state")

    if state.get("version") != 1:
        raise RuntimeError("Unsupported state version")

    if not isinstance(state.get("positions"), dict):
        raise RuntimeError("Invalid position state")

    if not isinstance(
        state.get("execution_blocked"),
        bool
    ):
        raise RuntimeError("Invalid execution lock")

    if "block_reason" not in state:
        raise RuntimeError("Missing block reason")

    return state


def load_state():

    result = github_request("GET")

    if not isinstance(result, dict):
        raise RuntimeError("Invalid state response")

    sha = result.get("sha")
    content = result.get("content")

    if not sha or not content:
        raise RuntimeError("State content missing")

    decoded = base64.b64decode(
        content
    ).decode("utf-8")

    state = json.loads(decoded)

    validate_state(state)

    return state, sha


def save_state(state, sha):

    validate_state(state)

    if not sha:
        raise RuntimeError("Missing state SHA")

    content = json.dumps(
        state,
        indent=4,
        ensure_ascii=False,
        allow_nan=False
    )

    encoded = base64.b64encode(
        content.encode("utf-8")
    ).decode("utf-8")

    result = github_request(
        "PUT",
        {
            "message": "Update demo position state",
            "content": encoded,
            "sha": sha,
            "branch": BRANCH
        }
    )

    if not isinstance(result, dict):
        raise RuntimeError("Invalid state save response")

    updated = result.get("content")

    if not isinstance(updated, dict):
        raise RuntimeError("State save not confirmed")

    new_sha = updated.get("sha")

    if not new_sha:
        raise RuntimeError("Updated SHA missing")

    return new_sha


def block_execution(state, sha, reason):

    state["execution_blocked"] = True
    state["block_reason"] = str(reason)

    return save_state(state, sha)
