#!/usr/bin/env python3
"""OneLap export helper for an agent Skill. Python 3.9+, standard library only.

No Strava requests, credential persistence, or background service.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

OTM = 'https://otm.onelap.cn/api/otm/ride_record'
FALLBACK = 'https://u.onelap.cn/api/otm/ride_record'
MAX_BYTES = 24 * 1024 * 1024


class SyncError(Exception):
    def __init__(self, message, *, stop=False):
        super().__init__(message)
        self.stop = stop


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(url, *, token=None, data=None, multipart=False):
    # Only constant hosts and path components constructed by this module are allowed.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.netloc not in ('www.onelap.cn', 'otm.onelap.cn', 'u.onelap.cn'):
        raise SyncError('拒绝向非顽鹿 HTTPS 地址发送请求', stop=True)
    headers = {'User-Agent': 'WanluStravaSkill/0.1'}
    if token:
        if '\r' in token or '\n' in token:
            raise SyncError('登录令牌包含非法换行', stop=True)
        headers['Authorization'] = token
    if data is not None:
        if multipart:
            boundary = '----wanlu-' + secrets.token_hex(16)
            data = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
                            for key, value in data.items()) + f'--{boundary}--\r\n'.encode()
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        else:
            data = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=40) as response:
            content = response.read(MAX_BYTES + 1)
            if len(content) > MAX_BYTES:
                raise SyncError('响应超过 24 MB 限制')
            return content
    except urllib.error.HTTPError as error:
        messages = {401: '登录失效，请重新登录', 403: '平台拒绝访问，已停止请求', 429: '请求过于频繁，已停止请求'}
        raise SyncError(messages.get(error.code, f'平台返回 HTTP {error.code}'),
                        stop=error.code in (401, 403, 429)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SyncError('无法连接顽鹿，请检查网络、DNS 或代理') from None


def json_request(url, **kwargs):
    try:
        return json.loads(request(url, **kwargs))
    except (ValueError, UnicodeError):
        raise SyncError('平台返回非 JSON 内容，登录状态或接口可能已变化') from None


def checked(payload):
    if not isinstance(payload, dict):
        raise SyncError('平台响应结构无法识别')
    code = payload.get('code')
    if code == -2:
        raise SyncError('顽鹿触发风控，已停止请求', stop=True)
    if code not in (0, 200):
        safe_code = str(code) if isinstance(code, int) else '未知'
        raise SyncError(f'顽鹿接口未成功（code={safe_code}），请重新验证登录', stop=True)
    return payload.get('data')


def credentials():
    token = os.environ.get('ONELAP_TOKEN', '').strip()
    if token:
        return {'token': token, 'uid': os.environ.get('ONELAP_UID', '').strip()}
    # getpass must not fall back to visible input in a tool without a terminal.
    account = os.environ.get('ONELAP_ACCOUNT', '')
    password = os.environ.get('ONELAP_PASSWORD', '')
    if (not account or not password) and not sys.stdin.isatty():
        raise SyncError('需要隐藏输入的终端，或由安全环境提供顽鹿凭证', stop=True)
    account = account or getpass.getpass('顽鹿账号（隐藏输入）: ')
    password = password or getpass.getpass('顽鹿密码（隐藏输入）: ')
    if not account.strip() or not password:
        raise SyncError('账号和密码不能为空', stop=True)
    hashed = hashlib.md5(password.encode()).hexdigest()
    password = ''
    data = checked(json_request('https://www.onelap.cn/api/login',
                               data={'account': account.strip(), 'password': hashed}, multipart=True))
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict) or not isinstance(data.get('token'), str) or not data['token']:
        raise SyncError('登录响应缺少令牌，可能需要验证码或接口已变化', stop=True)
    info = data.get('userinfo') or {}
    return {'token': data['token'], 'uid': str(info.get('uid', '')) if isinstance(info, dict) else ''}


def validate_dates(start, end):
    try:
        if date.fromisoformat(start) > date.fromisoformat(end):
            raise ValueError()
    except (ValueError, TypeError):
        raise SyncError('起止日期必须为 YYYY-MM-DD，且起始日期不能晚于结束日期') from None


def list_rides(auth, start, end, *, import_latest=True):
    validate_dates(start, end)
    imported, warning = 0, ''
    if import_latest:
        for _ in range(3):
            try:
                data = checked(json_request(OTM + '/capability_list', token=auth['token'], data={'uid': auth['uid']}))
                counts = data.get('sync', {}) if isinstance(data, dict) else {}
                inserted, total = counts.get('inserted', 0), counts.get('total', 0)
                imported += inserted if isinstance(inserted, int) else 0
                if not isinstance(inserted, int) or not isinstance(total, int) or inserted <= 0 or inserted < total:
                    break
            except SyncError as error:
                if error.stop:
                    raise
                warning = 'OTM 预导入未完成：' + str(error)
                break
    rows, seen, truncated = [], set(), False
    for page in range(1, 11):
        data = checked(json_request(OTM + '/list', token=auth['token'], data={
            'page': page, 'limit': 50, 'start_date': start, 'end_date': end}))
        if not isinstance(data, dict) or not isinstance(data.get('list'), list):
            raise SyncError('记录列表响应结构已变化')
        for row in data['list']:
            if isinstance(row, dict):
                item = normalize(row)
                if item['id'] and item['id'] not in seen:
                    seen.add(item['id'])
                    rows.append(item)
        more = bool((data.get('pagination') or {}).get('has_more'))
        if not more or not data['list']:
            break
        truncated = page == 10
    return {'records': rows, 'imported': imported, 'warning': warning, 'truncated': truncated}


def normalize(row):
    start = None
    for key in ('start_time', 'start_riding_time', 'startTime', 'created_at', 'timestamp'):
        value = row.get(key)
        if value is None or value == '':
            continue
        try:
            if isinstance(value, (int, float)) or re.fullmatch(r'\d{10,13}', str(value)):
                number = float(value)
                dt = datetime.fromtimestamp(number / 1000 if number >= 1e12 else number, timezone.utc)
            else:
                dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            if dt.year > 1989:
                start = dt.isoformat()  # Preserve a missing timezone rather than inventing one.
                break
        except (ValueError, OverflowError, OSError, TypeError):
            continue
    distance = row.get('distance_km')
    if distance is None and isinstance(row.get('totalDistance'), (int, float)):
        distance = row['totalDistance'] / 1000
    return {'id': str(row.get('id') or row.get('activity_id') or ''),
            'name': str(row.get('name') or row.get('title') or '顽鹿骑行'),
            'start': start, 'distanceKm': distance, 'durationSeconds': row.get('time', row.get('duration'))}


def crc16(content):
    crc = 0
    for value in content:
        crc ^= value
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def inspect_fit(content):
    if len(content) < 14 or content[8:12] != b'.FIT' or content[0] not in (12, 14):
        raise SyncError('下载内容不是支持的 FIT 文件')
    header = content[0]
    end = header + int.from_bytes(content[4:8], 'little')
    if end + 2 != len(content):
        raise SyncError('FIT 文件长度异常')
    if header == 14 and content[12:14] != b'\0\0' and crc16(content[:header]) != 0:
        raise SyncError('FIT 头部 CRC 校验失败')
    if crc16(content[:end]) != int.from_bytes(content[end:], 'little'):
        raise SyncError('FIT 文件 CRC 校验失败')
    return {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'crcValid': True}


def valid_id(record_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', record_id):
        raise SyncError('记录 ID 格式不合法')
    return record_id


def download(auth, record_id):
    valid_id(record_id)
    last = None
    for base in (OTM, FALLBACK):
        try:
            content = request(base + '/analysis/fit_content/' + record_id, token=auth['token'])
            inspect_fit(content)
            return content
        except SyncError as error:
            if error.stop:
                raise
            last = error
    try:
        detail = checked(json_request(OTM + '/analysis/' + record_id, token=auth['token']))
        record = detail.get('ridingRecord', detail) if isinstance(detail, dict) else {}
        key = record.get('fileKey') if isinstance(record, dict) else None
        if isinstance(key, str) and key:
            encoded = urllib.parse.quote(base64.b64encode(key.encode()).decode(), safe='')
            content = request(OTM + '/analysis/fit_content/' + encoded, token=auth['token'])
            inspect_fit(content)
            return content
    except SyncError as error:
        if error.stop:
            raise
        last = error
    raise last or SyncError('无法下载原始 FIT 文件')


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def export(auth, ids, output=None):
    ids = list(dict.fromkeys(valid_id(value) for value in ids))
    if output:
        directory = Path(output).expanduser().absolute()
        directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    else:
        directory = Path(tempfile.mkdtemp(prefix='wanlu-sync-'))
    manifest = {'version': 1, 'createdAt': datetime.now(timezone.utc).isoformat(),
                'records': [], 'errors': [], 'notAttempted': []}
    for index, record_id in enumerate(ids):
        try:
            content = download(auth, record_id)
            info = inspect_fit(content)
            path = directory / f'onelap-{record_id}.fit'
            private_write(path, content)
            manifest['records'].append({'id': record_id, 'file': str(path), **info, 'status': 'downloaded'})
        except SyncError as error:
            manifest['errors'].append({'id': record_id, 'error': str(error)})
            if error.stop:
                manifest['notAttempted'] = ids[index + 1:]
                break
    path = directory / 'manifest.json'
    private_write(path, json_bytes(manifest))
    return {'directory': str(directory), 'manifest': str(path), **manifest}


def receipt(path, record_id, status, url=None, uploaded_file=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise SyncError('manifest 必须是普通本地文件')
    if path.stat().st_size > 2 * 1024 * 1024:
        raise SyncError('manifest 超过大小限制')
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('records'), list):
        raise SyncError('无法识别 manifest')
    match = re.fullmatch(r'https://www\.strava\.com/activities/([1-9][0-9]*)/?', url or '')
    if status in ('success', 'existing') and not match:
        raise SyncError('成功或已存在状态需要最终 Strava 活动链接，不能使用上传队列 ID')
    if status not in ('success', 'existing', 'pending', 'failed'):
        raise SyncError('未知结果状态')
    item = next((item for item in data['records'] if item.get('id') == record_id), None)
    if item is None:
        raise SyncError('manifest 中没有该记录')
    if uploaded_file:
        uploaded = Path(uploaded_file)
        if uploaded.stat().st_size > MAX_BYTES:
            raise SyncError('上传副本超过大小限制')
        uploaded_info = inspect_fit(uploaded.read_bytes())
        conversion = None
        if uploaded_info['sha256'] != item['sha256']:
            audit_path = Path(str(uploaded) + '.coordinates.json')
            if not audit_path.is_file() or audit_path.stat().st_size > 2 * 1024 * 1024:
                raise SyncError('上传副本与原始 FIT 不同，缺少可核对的坐标转换记录')
            audit = json.loads(audit_path.read_text())
            if not isinstance(audit, dict) or audit.get('sourceSha256') != item['sha256'] or \
                    audit.get('outputSha256') != uploaded_info['sha256'] or audit.get('targetCrs') != 'wgs84':
                raise SyncError('上传副本的坐标转换记录与原始文件不匹配')
            conversion = {key: audit.get(key) for key in ('sourceCrs', 'targetCrs', 'method', 'evidence')}
        item.update(uploadedFile=str(uploaded.absolute()), uploadedSha256=uploaded_info['sha256'])
        item.pop('coordinateConversion', None)
        if conversion:
            item['coordinateConversion'] = conversion
    item.update(status=status, verifiedAt=datetime.now(timezone.utc).isoformat())
    for key in ('url', 'activityId'):
        item.pop(key, None)
    if match:
        item.update(url=f'https://www.strava.com/activities/{match[1]}', activityId=match[1])
    temp = None
    try:
        fd, temp = tempfile.mkstemp(prefix='.receipt-', dir=str(path.parent))
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json_bytes(data))
        os.replace(temp, path)
        temp = None
    finally:
        if temp:
            os.unlink(temp)
    return {'manifest': str(path.absolute()), 'record': item}


def main():
    parser = argparse.ArgumentParser(description='顽鹿原始 FIT 读取工具；上传由 agent 在 Strava 网页完成。')
    commands = parser.add_subparsers(dest='command', required=True)
    listing = commands.add_parser('list', help='列出指定日期范围内的骑行，不输出凭证')
    listing.add_argument('--start', required=True)
    listing.add_argument('--end', required=True)
    listing.add_argument('--no-import', action='store_true', help='不触发 OTM 最新记录预导入')
    exporting = commands.add_parser('export', help='下载明确指定的记录 ID，保留原始 FIT')
    exporting.add_argument('--ids', nargs='+', required=True)
    exporting.add_argument('--output', help='可选：尚不存在的输出目录；默认创建私有临时目录')
    inspecting = commands.add_parser('inspect', help='离线校验 FIT 容器长度与 CRC')
    inspecting.add_argument('file')
    recording = commands.add_parser('receipt', help='记录 agent 已在网页核实的上传结果，不访问 Strava')
    recording.add_argument('--manifest', required=True)
    recording.add_argument('--id', required=True)
    recording.add_argument('--status', required=True, choices=('success', 'existing', 'pending', 'failed'))
    recording.add_argument('--url')
    recording.add_argument('--uploaded-file', help='实际上传的原始 FIT 或带有匹配坐标审计记录的转换副本')
    args = parser.parse_args()
    try:
        if args.command == 'list':
            validate_dates(args.start, args.end)
            result = list_rides(credentials(), args.start, args.end, import_latest=not args.no_import)
        elif args.command == 'export':
            for value in args.ids:
                valid_id(value)
            result = export(credentials(), args.ids, args.output)
        elif args.command == 'inspect':
            path = Path(args.file)
            if path.stat().st_size > MAX_BYTES:
                raise SyncError('FIT 超过 24 MB 限制')
            result = inspect_fit(path.read_bytes())
        else:
            result = receipt(args.manifest, args.id, args.status, args.url, args.uploaded_file)
        print(json_bytes(result).decode(), end='')
        return 1 if args.command == 'export' and result['errors'] else 0
    except (SyncError, OSError, ValueError, EOFError) as error:
        # Never print OS exceptions (may contain private paths), upstream bodies or traceback.
        message = str(error) if isinstance(error, SyncError) else '本地文件或输入无法处理，请检查路径和格式'
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"error":"用户已取消"}', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
