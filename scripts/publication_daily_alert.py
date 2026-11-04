#!/usr/bin/env python3
"""Report a missed daily gadget publication once per JST day, without posting."""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone

from pipeline_common import JST, PipelineError

REPOSITORY = 'noranamani/noranamani-gadget-deals'
API = 'https://api.github.com/repos/' + REPOSITORY
TITLE_PREFIX = '[gadget-publication] '
SAFE_REASON = re.compile(r'[A-Z][A-Z0-9_]{2,79}\Z')


def issue_title(now: datetime) -> str:
    return TITLE_PREFIX + now.astimezone(JST).date().isoformat() + ' 公開・素材生成の要確認'


def alert_reason(prepare: str, deploy: str, note: str, gate_status: str,
                 gate_reason: str, publish_needed: str) -> str | None:
    """Report actual problems, never successful receipt or expected skip."""
    if prepare == 'failure' or prepare == 'cancelled':
        return 'PREPARE_FAILED'
    if gate_status == 'blocked':
        return gate_reason if SAFE_REASON.fullmatch(gate_reason) else 'GATE_BLOCKED'
    if publish_needed == 'true' and deploy not in {'success'}:
        return 'DEPLOY_NOT_VERIFIED'
    if note in {'failure', 'cancelled'}:
        return 'WEEKLY_NOTE_MATERIALS_FAILED'
    return None


def notify_once(now: datetime, *, run_id: str, reason: str, token: str,
                request) -> dict:
    """Idempotent by JST date. Closed issues also suppress duplicates."""
    if not re.fullmatch(r'[0-9]{5,}', run_id) or not SAFE_REASON.fullmatch(reason):
        raise PipelineError('PUBLICATION_ALERT_CONTEXT_INVALID')
    title = issue_title(now)
    for page in range(1, 6):
        existing = request('GET', f'/issues?state=all&per_page=100&page={page}', None, token)
        if not isinstance(existing, list):
            raise PipelineError('PUBLICATION_ALERT_API_INVALID')
        if any(isinstance(row, dict) and row.get('title') == title and
                'pull_request' not in row for row in existing):
            return {'status': 'ALREADY_REPORTED', 'title': title}
        if len(existing) < 100:
            break
    else:
        raise PipelineError('PUBLICATION_ALERT_PAGINATION_LIMIT')
    run = 'https://github.com/' + REPOSITORY + '/actions/runs/' + run_id
    body = '\n'.join([
        'GitHub Actionsの定期補完で、当日分のサイト公開または週次素材に要確認の状態を検出しました。',
        '',
        '- 判定コード: \`' + reason + '\`',
        '- 確認対象: ' + run,
        '',
        '**注意:** この通知は公開成功の証拠ではありません。',
        '当日分のPublish Pages成功を実行ログで確認し、必要であれば公開されている日次記事も照合してください。',
        '',
        '安全条件: 古い商品データや記事を再公開しない。'
        ' 鮮度・ハッシュ検証を解除しない。'
        ' 公開要求/クリックの結果が不明なときは再送しない。',
        '',
        'このIssueは同じJST日に1件のみ作成されます。'
        ' 以後の定期実行は同じIssueを使い、失敗メールを繰り返し送信しません。',
    ])
    created = request('POST', '/issues', {'title': title, 'body': body}, token)
    if not isinstance(created, dict) or not isinstance(created.get('number'), int):
        raise PipelineError('PUBLICATION_ALERT_CREATE_UNCONFIRMED')
    return {'status': 'CREATED', 'title': title, 'issue': created['number']}


def github_request(method, route, body, token):
    if method not in {'GET', 'POST'} or not route.startswith('/issues') or not token:
        raise PipelineError('PUBLICATION_ALERT_CONTEXT_INVALID')
    path = API + route
    req = urllib.request.Request(path, method=method,
        data=None if body is None else json.dumps(body).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json',
                 'X-GitHub-Api-Version': '2022-11-28',
                 'User-Agent': 'gadget-publication-health'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            value = json.load(response)
    except Exception:
        raise PipelineError('PUBLICATION_ALERT_API_UNAVAILABLE') from None
    return value


def main() -> int:
    try:
        if (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'
                or os.environ.get('GITHUB_EVENT_NAME') != 'schedule'):
            raise PipelineError('PUBLICATION_ALERT_CONTEXT_INVALID')
        now = datetime.now(timezone.utc)
        reason = alert_reason(
            os.environ.get('PREPARE_RESULT', ''),
            os.environ.get('DEPLOY_RESULT', ''),
            os.environ.get('NOTE_RESULT', ''),
            os.environ.get('GATE_STATUS', ''),
            os.environ.get('GATE_REASON', ''),
            os.environ.get('PUBLISH_NEEDED', ''))
        if reason is None:
            print('PUBLICATION_ALERT: NOTHING_TO_REPORT')
            return 0
        if now.astimezone(JST).time() < time(9, 10):
            # The primary Windows task and safe recovery window may still
            # complete. The later GitHub schedule checks can alert once.
            print('PUBLICATION_ALERT: WAIT_FOR_DAILY_RECOVERY')
            return 0
        result = notify_once(now,
            run_id=os.environ.get('GITHUB_RUN_ID', ''), reason=reason,
            token=os.environ.get('GH_TOKEN', ''),
            request=github_request)
        print('PUBLICATION_ALERT: ' + result['status'])
        # Exactly the first discovered failure of the day is a failed Actions
        # run/email. All subsequent stale checks stay green with explicit
        # not-published summaries and the durable issue still open/closed.
        return 1 if result['status'] == 'CREATED' else 0
    except PipelineError as exc:
        print('PUBLICATION_ALERT: ' + str(exc))
        return 1
    except Exception:
        print('PUBLICATION_ALERT: INTERNAL_ERROR')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
