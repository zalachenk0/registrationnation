# ============================================================================
# File:         test_monday_CG.py
# Written by:   CG (Cristian Gonzalez)
# What it does: Tests for the Monday.com record.
# Team-written: Logic written by CG (Cristian Gonzalez). Outside code is used
#               only through the third-party sources listed below.
# Third-party:  pytest; Stripe, Monday.com and CUPS are replaced by stand-ins in
#               these tests.
# ============================================================================
"""Monday.com CRM: payload shapes, privacy, error handling, and every mapped column checked against the real board."""
import json
from unittest.mock import Mock

import pytest

import monday_service_CG as ms
import scanner_api_BV_CG as api

# the columns we write to, copied from the real board ("UTSA TTT Online Registration Renewal Duplicate"): id -> (type, title, status labels)
BOARD = {
    'text09__1': ('text', 'First Name', None),
    'text1__1': ('text', 'Last Name', None),
    'text016__1': ('text', 'Street Address', None),
    'text4__1': ('text', 'City', None),
    'text3__1': ('text', 'State / Province', None),
    'text33__1': ('text', 'Postal / Zip Code', None),
    'phone__1': ('phone', 'Phone Number', None),
    'email__1': ('email', 'Email', None),
    'text60__1': ('text', 'License Plate', None),
    'text9__1': ('text', 'Vehicle VIN (Last 6)', None),
    'text39__1': ('text', 'Vehicle Year', None),
    'text96__1': ('text', 'Vehicle Make', None),
    'text2__1': ('text', 'Vehicle Model', None),
    'text_mm88wyt8': ('text', 'Insurance Provider', None),
    'text_mm88cmrd': ('text', 'Policy Number', None),
    'date4__1': ('date', 'Vehicle Insurance Expiration', None),
    'date__1': ('date', 'Submission Date', None),
    'numbers5__1': ('numbers', 'Total Cost', None),
    'text32__1': ('text', 'Invoice Number', None),
    'text_mm88bzfy': ('text', 'Stripe Payment ID', None),
    'long_text0__1': ('long_text', 'Admin Notes', None),
    'text20__1': ('text', 'Source', None),
    'status9__1': ('status', 'Transaction Status', ['New Submission', 'Received']),
    'status59__1': ('status', 'Registration Renewal Store*', ['Working on it', 'Done', 'Stuck', 'Citation', 'Registration Renewal', 'Registration Renewal, Citation']),
    'status0__1': ('status', 'Renewal Status', ['Pending', 'Denied', 'Archived', 'Approved', 'Requested', 'Completed at kiosk']),
}

FIELDS = dict(ms.SAMPLE_FIELDS, phone='(361) 555-0188', amountPaid='114.25', insuranceExpiration='2027-06-30',
              vin='1HGCM82633A123456', paymentIntentId='pi_test_999', confirmationNumber='REG-ABC123')


@pytest.fixture
def monday(monkeypatch):
    monkeypatch.setenv('MONDAY_API_TOKEN', 'test-token')
    monkeypatch.setenv('MONDAY_BOARD_ID', '111')
    monkeypatch.delenv('MONDAY_GROUP_ID', raising=False)
    sent = []

    def post(url, headers=None, json=None, timeout=None):
        sent.append(dict(headers=headers, **json))
        response = Mock(status_code=200)
        response.json.return_value = dict(data=dict(create_item=dict(id='987654321')))
        return response

    monkeypatch.setattr(ms.requests, 'post', post)
    return sent


def columns(sent):
    return json.loads(sent[-1]['variables']['columnValues'])


def create(fields=None, name='Doe, Jane - KGP-4821'):
    return api.app.test_client().post('/api/monday/create_record', json=dict(itemName=name, fields=FIELDS if fields is None else fields))


def test_not_set_up_is_503_and_a_name_is_required(monkeypatch):
    monkeypatch.delenv('MONDAY_API_TOKEN', raising=False)
    refused = create()
    assert refused.status_code == 503 and 'MONDAY_API_TOKEN' in refused.json['error']
    monkeypatch.setenv('MONDAY_API_TOKEN', 'x')
    monkeypatch.setenv('MONDAY_BOARD_ID', '1')
    assert api.app.test_client().post('/api/monday/create_record', json=dict(fields=FIELDS)).status_code == 400


def test_record_is_created_with_the_right_shapes(monday):
    reply = create()
    assert reply.status_code == 200 and reply.json['itemId'] == '987654321' and reply.json['warnings'] == []
    cols, call = columns(monday), monday[-1]
    assert call['variables']['boardId'] == '111' and call['variables']['itemName'] == 'Doe, Jane - KGP-4821'
    assert call['headers']['Authorization'] == 'test-token' and isinstance(call['variables']['columnValues'], str)
    assert cols['email__1'] == dict(email='sample.customer@example.com', text='sample.customer@example.com')
    assert cols['phone__1'] == dict(phone='3615550188', countryShortName='US')
    assert cols['numbers5__1'] == '114.25'
    assert cols['status9__1'] == dict(label='Received') and cols['status0__1'] == dict(label='Completed at kiosk')
    assert set(cols['date__1']) == {'date', 'time'}                                # submission date carries the time
    assert 'pi_test_999' in cols['long_text0__1']['text'] and cols['text_mm88bzfy'] == 'pi_test_999'
    assert cols['text_mm88wyt8'] == FIELDS['insuranceProvider'] and cols['text_mm88cmrd'] == FIELDS['policyNumber']


def test_group_is_only_sent_when_set(monday, monkeypatch):
    create()
    assert 'groupId' not in monday[-1]['variables'] and 'group_id' not in monday[-1]['query']
    monkeypatch.setenv('MONDAY_GROUP_ID', 'group_mm88283z')
    create()
    assert monday[-1]['variables']['groupId'] == 'group_mm88283z' and 'group_id: $groupId' in monday[-1]['query']


def test_sensitive_fields_and_the_full_vin_never_leave_the_kiosk(monday):
    create()
    blob = json.dumps(columns(monday))
    assert not any(secret in blob for secret in ('00000000', '1990-01-15', '1HGCM82633A123456'))
    assert columns(monday)['text9__1'] == '123456'                                 # only the last 6 of the VIN


def test_bad_values_are_skipped_with_a_warning_not_a_failure(monday):
    reply = create(dict(FIELDS, email='nope', phone='12', insuranceExpiration='whenever'), name='x')
    assert reply.status_code == 200 and len(reply.json['warnings']) == 3
    cols = columns(monday)
    assert 'email__1' not in cols and 'phone__1' not in cols and 'date4__1' not in cols and 'text60__1' in cols


@pytest.mark.parametrize('message,hint', [('Not Authenticated', 'MONDAY_API_TOKEN'), ("Column 'x' rejected the value", 'COLUMN_MAP')])
def test_monday_errors_become_a_502_with_a_hint(monday, monkeypatch, message, hint):
    def post(*args, **kwargs):
        response = Mock(status_code=200)
        response.json.return_value = dict(errors=[dict(message=message, extensions=dict(code='X'))])
        return response

    monkeypatch.setattr(ms.requests, 'post', post)
    reply = create()
    assert reply.status_code == 502 and hint in reply.json['error']


def test_no_internet_is_a_502_not_a_crash(monday, monkeypatch):
    monkeypatch.setattr(ms.requests, 'post', Mock(side_effect=ms.requests.exceptions.ConnectionError('down')))
    reply = create()
    assert reply.status_code == 502 and 'Could not reach Monday.com' in reply.json['error']


def test_every_mapped_column_exists_on_the_real_board_with_the_right_type():
    for field, spec in ms.COLUMN_MAP.items():
        assert spec['column'] in BOARD, f"{field}: column {spec['column']} is not on the board"
        board_type, title, labels = BOARD[spec['column']]
        assert board_type == ('date' if spec['type'] == 'datetime' else spec['type']), f"{field} -> {title}"
        if spec['type'] == 'status':
            assert spec['value'] in labels, f"{field}: label {spec['value']!r} is not a label on {title}"
    assert len({spec['column'] for spec in ms.COLUMN_MAP.values()}) == len(ms.COLUMN_MAP)   # no two fields share a column
