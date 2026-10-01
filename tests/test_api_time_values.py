# -*- coding: utf-8 -*-
"""接口时间出口和UTC存储时钟：不依赖机器当前时区/钟点。"""

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import main
from layers import auth, memory, enterprise_password
from utils import time_values
from utils.time_values import API_TIMESTAMP_FIELDS, UTCJSONResponse, serialize_api_times


@pytest.mark.parametrize('field', sorted(API_TIMESTAMP_FIELDS))
@pytest.mark.parametrize('value', [
    '2026-09-30T02:46:13', '2026-09-30 02:46:13',
    '2026-09-30T02:46:13Z', '2026-09-30T10:46:13+08:00',
])
def test_every_public_time_field_is_utc_at_http_egress(field, value):
    app = FastAPI(default_response_class=UTCJSONResponse)

    @app.get('/time')
    def sample():
        return {'items': [{field: value}], 'empty': {field: None}, 'unset': {field: ''}}

    response = TestClient(app).get('/time')
    assert response.status_code == 200
    assert response.json() == {
        'items': [{field: '2026-09-30T02:46:13Z'}],
        'empty': {field: None}, 'unset': {field: ''},
    }


def test_calendar_labels_and_user_content_are_not_rewritten():
    payload = {
        'business_day': '2026-09-30', 'snapshot_date': '2026-09-30',
        'previous_snapshot_date': '2026-09-29', 'year_month': '2026-09',
        'content': '2026-09-30T02:46:13', 'source': '2026-09-30T02:46:13',
    }
    assert serialize_api_times(payload) == payload


def test_public_schema_time_fields_are_registered():
    schema = main.app.openapi()
    names = {
        name
        for model in schema.get('components', {}).get('schemas', {}).values()
        for name in model.get('properties', {})
        if name.endswith(('_at', '_until')) or name in {'timestamp', 'last_active', 'active_boundary'}
    }
    assert names
    assert names <= API_TIMESTAMP_FIELDS


def test_main_explicit_json_and_sse_share_the_same_time_exit(client):
    assert main.app.router.default_response_class is UTCJSONResponse
    for endpoint in ['/health', '/ready']:
        response = client.get(endpoint)
        assert response.json()['timestamp'].endswith('Z')
    payload = {'created_at': '2026-09-30T02:46:13'}
    expected = {'created_at': '2026-09-30T02:46:13Z'}
    assert json.loads(main.JSONResponse(content=payload).body) == expected
    assert json.loads(main._sse_data(payload)[6:]) == expected


def test_http_error_detail_times_use_the_same_exit():
    app = FastAPI()
    app.add_exception_handler(main.StarletteHTTPException, main.utc_http_exception_handler)

    @app.get('/error')
    def sample():
        raise HTTPException(429, detail={'locked_until': '2026-09-30T02:46:13'})

    response = TestClient(app).get('/error')
    assert response.status_code == 429
    assert response.json() == {'detail': {'locked_until': '2026-09-30T02:46:13Z'}}


def test_real_session_history_times(client, auth_headers):
    headers, user = auth_headers('customer')
    session_id = 'utc-time-session'
    auth.bind_session(session_id, user['user_id'])
    memory.save_message(session_id, 'user', '时间测试')
    response = client.get('/memory/sessions', headers=headers)
    item = next(item for item in response.json()['sessions'] if item['session_id'] == session_id)
    for field in ['created_at', 'last_active']:
        assert item[field].endswith('Z')
    history = client.get('/memory/' + session_id, headers=headers).json()
    assert history['history'][0]['timestamp'].endswith('Z')


def test_utc_storage_clock_and_age_are_independent_of_local_clock(monkeypatch):
    calls = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            calls.append(tz)
            instant = cls(2026, 9, 30, 2, 46, 13, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(hour=10, tzinfo=None)

    monkeypatch.setattr(time_values, 'datetime', Clock)
    value = time_values.utc_now_naive()
    assert value.isoformat() == '2026-09-30T02:46:13'
    assert value.tzinfo is None
    assert calls == [timezone.utc]
    assert memory._memory_age_days('2026-09-29T10:46:13+08:00', now=value) == 1


def test_persistent_writers_no_longer_use_implicit_local_datetime_now():
    # 代码扫描作防回归；提示词里的本地日期不是存储时间，刻意不纳入。
    root = Path(main.__file__).parent
    for path in [root / 'main.py', *sorted((root / 'layers').glob('*.py')),
                 root / 'utils' / 'observability.py']:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == 'datetime':
                    assert node.func.attr != 'utcnow', (path, node.lineno)
                    if node.func.attr == 'now':
                        assert node.args or node.keywords, (path, node.lineno)


def test_business_day_boundary_remains_beijing_four_am():
    local = timezone(timedelta(hours=8))
    now = datetime(2026, 9, 30, 3, 59, 59, tzinfo=local)
    assert enterprise_password.get_business_day(now).isoformat() == '2026-09-29'
    assert enterprise_password.get_business_day(now + timedelta(seconds=1)).isoformat() == '2026-09-30'
    assert enterprise_password.get_business_day_storage_range(now) == (
        datetime(2026, 9, 28, 20), datetime(2026, 9, 29, 20)
    )
