#!/usr/bin/env python3
"""Read-only Pages artifact visibility gate before the FIRST deploy-pages call.

An upload-pages-artifact step may return success before its metadata becomes
visible to the deployment API. Fail before calling deploy-pages, so a following
fresh, permitted schedule can safely retry without an uncertain deployment.
This is not a guarantee of Pages deployment success, nor an override of the
daily freshness / SHA / single-publication protections.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

EXPECTED_REPO = "noranamani/noranamani-gadget-deals"
ARTIFACT_NAME = "github-pages"
SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ArtifactNotReady(RuntimeError):
    pass


def artifact_visible(payload: dict, run_id: int, *, now: datetime | None = None) -> bool:
    """Find exactly one current run's verified and unexpired Pages artifact."""
    if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
        raise ArtifactNotReady("PAGES_ARTIFACT_API_INVALID")
    matches = [a for a in payload["artifacts"]
               if isinstance(a, dict) and a.get("name") == ARTIFACT_NAME]
    if len(matches) > 1:
        raise ArtifactNotReady("PAGES_ARTIFACT_AMBIGUOUS")
    if not matches:
        return False
    a = matches[0]
    if (type(a.get("id")) is not int or a["id"] <= 0
            or type(a.get("size_in_bytes")) is not int
            or a["size_in_bytes"] <= 0
            or a.get("expired") is not False
            or not isinstance(a.get("workflow_run"), dict)
            or a["workflow_run"].get("id") != run_id
            or not isinstance(a.get("digest"), str)
            or not SHA256.fullmatch(a["digest"])):
        return False
    try:
        expires = datetime.fromisoformat(str(a.get("expires_at", "")).replace("Z", "+00:00"))
        stamp = now or datetime.now(timezone.utc)
        return expires.tzinfo is not None and expires > stamp
    except ValueError:
        return False


def wait_for_visible(read, run_id, *, attempts=9, seconds=7, pause=time.sleep, clock=None):
    """Two consecutive read-only receipts; no retry of any external WRITE."""
    if type(run_id) is not int or run_id <= 0 or attempts < 2 or seconds < 0:
        raise ArtifactNotReady("PAGES_ARTIFACT_PREFLIGHT_PARAMETERS_INVALID")
    consecutive = 0
    for i in range(attempts):
        payload = read()
        if artifact_visible(payload, run_id, now=clock() if clock else None):
            consecutive += 1
            if consecutive == 2:
                return True
        else:
            consecutive = 0
        if i < attempts - 1:
            pause(seconds)
    raise ArtifactNotReady("PAGES_ARTIFACT_METADATA_NOT_READY_NO_DEPLOY_ATTEMPT")


def api_read(repo, run_id, token):
    if repo != EXPECTED_REPO or not token:
        raise ArtifactNotReady("PAGES_ARTIFACT_API_CONTEXT_INVALID")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100",
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "User-Agent": "gadget-pages-metadata-readonly"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except Exception:
        raise ArtifactNotReady("PAGES_ARTIFACT_READ_ONLY_API_UNAVAILABLE") from None


def main() -> int:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    text = os.getenv("GITHUB_RUN_ID", "")
    token = os.getenv("GH_TOKEN", "")
    if repo != EXPECTED_REPO or not re.fullmatch("[1-9][0-9]+", text) or not token:
        print("PAGES_ARTIFACT_PREFLIGHT_STOP: CONTEXT_INVALID")
        return 1
    run_id = int(text)
    try:
        wait_for_visible(lambda: api_read(repo, run_id, token), run_id)
        print("PAGES_ARTIFACT_PREFLIGHT: ARTIFACT_VISIBLE_TWO_READS; DEPLOY_STILL_NOT_CONFIRMED")
        return 0
    except ArtifactNotReady as exc:
        # The failing step is BEFORE Publish Pages. Never fabricate a Pages receipt.
        print("PAGES_ARTIFACT_PREFLIGHT_STOP: " + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
