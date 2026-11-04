import importlib.util
import json
import os
import stat
import struct
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wanlu_skill', ROOT / 'skills/wanlu-strava-sync/scripts/wanlu.py')
wanlu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wanlu)


def fit_fixture(header_crc=True):
    # Definition + data for file_id message, sufficient for a CRC container test.
    body = bytes([0x40, 0, 0, 0, 0, 1, 0, 1, 0, 0, 4])
    header = struct.pack('<BBHI4s', 14 if header_crc else 12, 16, 2100, len(body), b'.FIT')
    if header_crc:
        header += struct.pack('<H', wanlu.crc16(header))
    content = header + body
    return content + struct.pack('<H', wanlu.crc16(content))


class CredentialTests(unittest.TestCase):
    def test_login_hashes_password_never_returns_it(self):
        with patch.dict(os.environ, {'ONELAP_ACCOUNT': 'fixture-user', 'ONELAP_PASSWORD': 'fixture-password'}, clear=True), \
             patch.object(wanlu, 'json_request', return_value={'code': 0, 'data': [
                 {'token': 'fixture-token', 'userinfo': {'uid': 42}}]}) as call:
            result = wanlu.credentials()
        self.assertEqual(result, {'token': 'fixture-token', 'uid': '42'})
        sent = call.call_args.kwargs
        self.assertTrue(sent['multipart'])
        self.assertEqual(sent['data']['password'], wanlu.hashlib.md5(b'fixture-password').hexdigest())
        self.assertNotIn('password', result)

    def test_existing_token_does_not_login(self):
        with patch.dict(os.environ, {'ONELAP_TOKEN': 'literal fixture', 'ONELAP_UID': '42'}, clear=True), \
             patch.object(wanlu, 'json_request') as call:
            self.assertEqual(wanlu.credentials()['token'], 'literal fixture')
            call.assert_not_called()

    def test_missing_credentials_without_terminal_stops(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(wanlu.sys.stdin, 'isatty', return_value=False), \
             patch.object(wanlu.getpass, 'getpass') as prompt:
            with self.assertRaises(wanlu.SyncError):
                wanlu.credentials()
            prompt.assert_not_called()


class ProtocolTests(unittest.TestCase):
    auth = {'token': 'fixture', 'uid': '42'}

    def test_import_pagination_and_safe_output(self):
        replies = [
            {'code': 200, 'data': {'sync': {'inserted': 1, 'total': 20}}},
            {'code': 200, 'data': {'list': [{'id': 1, 'start_time': '1970-01-01',
                'start_riding_time': '2026-01-02 08:30:00', 'fileKey': 'private-key'}], 'pagination': {'has_more': True}}},
            {'code': 200, 'data': {'list': [{'id': 1}, {'id': 2}], 'pagination': {'has_more': False}}}]
        with patch.object(wanlu, 'json_request', side_effect=replies) as call:
            result = wanlu.list_rides(self.auth, '2026-01-01', '2026-01-02')
        self.assertEqual(result['imported'], 1)
        self.assertEqual([x['id'] for x in result['records']], ['1', '2'])
        self.assertEqual(result['records'][0]['start'], '2026-01-02T08:30:00')
        self.assertNotIn('private-key', json.dumps(result))
        self.assertEqual(call.call_args.kwargs['data']['page'], 2)

    def test_date_validation_before_network(self):
        with patch.object(wanlu, 'json_request') as call:
            for start, end in [('2026-01-02', '2026-01-01'), ('bad', '2026-01-01')]:
                with self.assertRaises(wanlu.SyncError):
                    wanlu.list_rides(self.auth, start, end)
            call.assert_not_called()

    def test_risk_control_prevents_list_request(self):
        with patch.object(wanlu, 'json_request', return_value={'code': -2}) as call:
            with self.assertRaises(wanlu.SyncError) as caught:
                wanlu.list_rides(self.auth, '2026-01-01', '2026-01-02')
            self.assertTrue(caught.exception.stop)
            self.assertEqual(call.call_count, 1)

    def test_no_import_and_page_cap(self):
        reply = {'code': 0, 'data': {'list': [{'id': 1}], 'pagination': {'has_more': True}}}
        with patch.object(wanlu, 'json_request', return_value=reply) as call:
            result = wanlu.list_rides(self.auth, '2026-01-01', '2026-01-02', import_latest=False)
        self.assertTrue(result['truncated'])
        self.assertEqual(call.call_count, 10)
        self.assertTrue(all(item.args[0] == wanlu.OTM + '/list' for item in call.call_args_list))

    def test_filekey_fallback_returns_exact_bytes(self):
        content = fit_fixture()
        with patch.object(wanlu, 'request', side_effect=[wanlu.SyncError('not found'), b'<html/>', content]) as call, \
             patch.object(wanlu, 'json_request', return_value={'code': 200, 'data': {'ridingRecord': {'fileKey': 'fixture-file-key'}}}):
            result = wanlu.download(self.auth, '123')
        self.assertEqual(result, content)
        expected = wanlu.urllib.parse.quote(wanlu.base64.b64encode(b'fixture-file-key').decode(), safe='')
        self.assertEqual(call.call_args.args[0], wanlu.OTM + '/analysis/fit_content/' + expected)

    def test_429_does_not_try_another_host(self):
        with patch.object(wanlu, 'request', side_effect=wanlu.SyncError('limited', stop=True)) as call:
            with self.assertRaises(wanlu.SyncError):
                wanlu.download(self.auth, '123')
        self.assertEqual(call.call_count, 1)

    def test_rejects_arbitrary_hosts_and_path_traversal(self):
        with self.assertRaises(wanlu.SyncError):
            wanlu.request('https://example.invalid/steal', token='fixture')
        with patch.object(wanlu, 'request') as call:
            with self.assertRaises(wanlu.SyncError):
                wanlu.download(self.auth, '../outside')
            call.assert_not_called()
        self.assertIsNone(wanlu.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.invalid'))


class FileTests(unittest.TestCase):
    def test_fit_crc_header_and_truncation(self):
        for header_crc in (True, False):
            content = fit_fixture(header_crc)
            self.assertTrue(wanlu.inspect_fit(content)['crcValid'])
            broken = bytearray(content)
            broken[-3] ^= 1
            for invalid in (bytes(broken), content[:-1], b'<html>login</html>'):
                with self.assertRaises(wanlu.SyncError):
                    wanlu.inspect_fit(invalid)
        broken = bytearray(fit_fixture())
        broken[12] ^= 1
        with self.assertRaises(wanlu.SyncError):
            wanlu.inspect_fit(bytes(broken))

    def test_partial_export_is_private_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / 'output'
            with patch.object(wanlu, 'download', side_effect=[fit_fixture(), wanlu.SyncError('risk', stop=True)]):
                result = wanlu.export({'token': 'never-output', 'uid': '42'}, ['1', '1', '2', '3'], output)
            self.assertEqual(result['notAttempted'], ['3'])
            self.assertEqual(result['errors'][0]['id'], '2')
            self.assertEqual(len(result['records']), 1)
            self.assertEqual(Path(result['records'][0]['file']).read_bytes(), fit_fixture())
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for path in output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn('never-output', Path(result['manifest']).read_text())
            with self.assertRaises(FileExistsError):
                wanlu.export({}, ['1'], output)

    def test_receipts_require_final_activity_url_and_preserve_other_records(self):
        with tempfile.TemporaryDirectory() as parent:
            with patch.object(wanlu, 'download', return_value=fit_fixture()):
                result = wanlu.export({}, ['1', '2'], Path(parent) / 'output')
            manifest = result['manifest']
            for url in (None, 'https://www.strava.com/upload/123', 'https://evil.invalid/activities/123'):
                with self.assertRaises(wanlu.SyncError):
                    wanlu.receipt(manifest, '1', 'success', url)
            receipt = wanlu.receipt(manifest, '1', 'success', 'https://www.strava.com/activities/123/')
            self.assertEqual(receipt['record']['activityId'], '123')
            data = json.loads(Path(manifest).read_text())
            self.assertEqual(data['records'][1]['status'], 'downloaded')
            self.assertEqual(stat.S_IMODE(Path(manifest).stat().st_mode), 0o600)
            wanlu.receipt(manifest, '2', 'pending')
            self.assertEqual(json.loads(Path(manifest).read_text())['records'][1]['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
