#!/usr/bin/env python3
"""Offline FIT coordinate inspection and conservative GCJ-02 conversion.

Coordinate samples alone cannot identify their CRS. Evidence selection belongs
to the agent; this module never infers GCJ-02 from a mainland bounding box.
"""
import argparse
import json
import math
import struct
import sys
from pathlib import Path

from wanlu import MAX_BYTES, SyncError, crc16, inspect_fit, json_bytes, private_write

SCALE = 180 / 2**31
# FIT global message -> sint32 semicircle latitude/longitude field pairs.
PAIRS = {20: ((0, 1),), 18: ((3, 4), (29, 30), (31, 32)),
         19: ((3, 4), (5, 6), (27, 28), (29, 30))}


def coordinate_fields(content):
    inspect_fit(content)
    end, cursor, definitions, coordinates = len(content) - 2, content[0], {}, []

    def require(size):
        if cursor + size > end:
            raise SyncError('FIT 消息被截断，未继续分析或转换')

    while cursor < end:
        require(1)
        header = content[cursor]
        cursor += 1
        compressed = bool(header & 0x80)
        local = (header >> 5) & 3 if compressed else header & 15
        if not compressed and header & 0x40:
            require(5)
            architecture = content[cursor + 1]
            if architecture not in (0, 1):
                raise SyncError('FIT 字节序无效')
            endian = '<' if architecture == 0 else '>'
            global_id = struct.unpack_from(endian + 'H', content, cursor + 2)[0]
            count = content[cursor + 4]
            cursor += 5
            require(count * 3)
            fields = [tuple(content[pos:pos + 3]) for pos in range(cursor, cursor + count * 3, 3)]
            cursor += count * 3
            developer_size = 0
            if header & 0x20:
                require(1)
                count = content[cursor]
                cursor += 1
                require(count * 3)
                developer_size = sum(content[pos + 1] for pos in range(cursor, cursor + count * 3, 3))
                cursor += count * 3
            definitions[local] = (global_id, endian, fields, developer_size)
            continue
        if local not in definitions:
            raise SyncError('FIT 消息缺少定义')
        global_id, endian, fields, developer_size = definitions[local]
        offsets = {}
        for field_id, size, base_type in fields:
            if compressed and field_id == 253 and size == 4:
                continue  # Compressed record header replaces the timestamp field.
            require(size)
            offsets[field_id] = (cursor, size, base_type)
            cursor += size
        require(developer_size)
        cursor += developer_size
        for lat_id, lon_id in PAIRS.get(global_id, ()):
            lat_field, lon_field = offsets.get(lat_id), offsets.get(lon_id)
            if not lat_field or not lon_field:
                continue
            if lat_field[1:] != (4, 0x85) or lon_field[1:] != (4, 0x85):
                raise SyncError('坐标字段不是标准 sint32 半圆单位，不能猜测编码')
            lat_raw = struct.unpack_from(endian + 'i', content, lat_field[0])[0]
            lon_raw = struct.unpack_from(endian + 'i', content, lon_field[0])[0]
            if 0x7FFFFFFF in (lat_raw, lon_raw):
                continue
            lat, lon = lat_raw * SCALE, lon_raw * SCALE
            if abs(lat) > 90 or abs(lon) > 180:
                raise SyncError('经纬度超出范围，可能为字段或单位异常，不能自动纠偏')
            coordinates.append({'message': global_id, 'fields': (lat_id, lon_id), 'lat': lat, 'lon': lon,
                                'offsets': (lat_field[0], lon_field[0]), 'endian': endian})
    return coordinates


def in_conversion_bounds(lat, lon):
    # This is only an algorithm guard, NOT a mainland administrative boundary.
    return 0.8293 < lat < 55.8271 and 72.004 < lon < 137.8347


def gcj_to_wgs(lat, lon):
    def delta(la, lo):
        x, y, pi = lo - 105, la - 35, math.pi
        a = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
        a += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
        a += (20*math.sin(y*pi) + 40*math.sin(y*pi/3))*2/3
        a += (160*math.sin(y*pi/12) + 320*math.sin(y*pi/30))*2/3
        b = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
        b += (20*math.sin(6*x*pi) + 20*math.sin(2*x*pi))*2/3
        b += (20*math.sin(x*pi) + 40*math.sin(x*pi/3))*2/3
        b += (150*math.sin(x*pi/12) + 300*math.sin(x*pi/30))*2/3
        rad = la*pi/180
        m = 1 - .00669342162296594323*math.sin(rad)**2
        return (a*180 / (6378245*(1-.00669342162296594323)/(m*math.sqrt(m))*pi),
                b*180 / (6378245/math.sqrt(m)*math.cos(rad)*pi))
    a, b = lat, lon
    for _ in range(5):
        da, db = delta(a, b)
        a, b = lat - da, lon - db
    return a, b


def metres(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371000 * 2 * math.asin(min(1, math.sqrt(h)))


def analyze(content):
    fields = coordinate_fields(content)
    points = [item for item in fields if item['message'] == 20]
    result = {**inspect_fit(content), 'storage': 'FIT sint32 semicircles; degrees = raw * 180 / 2^31',
              'recordPoints': len(points), 'coordinatePairs': len(fields),
              'sourceCrs': 'unknown' if points else 'no_gps',
              'conversionEligible': False, 'samples': [],
              'note': '候选坐标不是识别结果，需要可信坐标系信息或独立地图参照。'}
    if not points:
        return result
    result['conversionEligible'] = all(in_conversion_bounds(item['lat'], item['lon']) for item in fields)
    result['bounds'] = {'south': min(p['lat'] for p in points), 'north': max(p['lat'] for p in points),
                        'west': min(p['lon'] for p in points), 'east': max(p['lon'] for p in points)}
    for index in sorted({round((len(points)-1)*i/8) for i in range(9)}):
        point = points[index]
        raw = point['lat'], point['lon']
        candidate = gcj_to_wgs(*raw) if result['conversionEligible'] else None
        result['samples'].append({'pointIndex': index, 'asWgs84': {'lat': raw[0], 'lon': raw[1]},
                                  'ifGcj02': {'lat': candidate[0], 'lon': candidate[1]} if candidate else None,
                                  'candidateShiftMetres': round(metres(raw, candidate), 1) if candidate else None})
    return result


def convert(content):
    fields = coordinate_fields(content)
    if not any(p['message'] == 20 for p in fields):
        raise SyncError('没有有效 GPS 轨迹点，不进行坐标转换')
    if not all(in_conversion_bounds(p['lat'], p['lon']) for p in fields):
        raise SyncError('坐标跨出转换边界，不支持自动整段转换')
    output = bytearray(content)
    for point in fields:
        lat, lon = gcj_to_wgs(point['lat'], point['lon'])
        for offset, value in zip(point['offsets'], (lat, lon)):
            struct.pack_into(point['endian'] + 'i', output, offset, round(value / SCALE))
    if output[0] == 14:
        struct.pack_into('<H', output, 12, crc16(output[:12]))
    struct.pack_into('<H', output, len(output)-2, crc16(output[:-2]))
    inspect_fit(output)
    return bytes(output), len(fields)


def load_fit(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise SyncError('FIT 超过 24 MB 限制')
    return path.read_bytes()


def convert_file(source, output, evidence):
    source, output = Path(source), Path(output)
    if not evidence.strip():
        raise SyncError('需要记录判断 GCJ-02 的具体证据')
    content = load_fit(source)
    # Detect another conversion made by this helper using its matching audit hash.
    prior = Path(str(source) + '.coordinates.json')
    if prior.is_file():
        if prior.stat().st_size > 2 * 1024 * 1024:
            raise SyncError('坐标审计文件超过大小限制')
        audit = json.loads(prior.read_text())
        if not isinstance(audit, dict):
            raise SyncError('无法识别已有坐标审计文件')
        if audit.get('outputSha256') == inspect_fit(content)['sha256'] and audit.get('targetCrs') == 'wgs84':
            raise SyncError('该文件已有匹配的 WGS-84 转换记录，拒绝二次纠偏')
    if source.resolve() == output.resolve() or output.exists():
        raise SyncError('转换必须写入新的副本，不能覆盖原文件或已有输出')
    audit_path = Path(str(output) + '.coordinates.json')
    if audit_path.exists():
        raise SyncError('输出的坐标审计文件已存在')
    converted, changed = convert(content)
    audit = {'sourceCrs': 'gcj02', 'targetCrs': 'wgs84', 'method': 'approximate iterative inverse',
             'sourceSha256': inspect_fit(content)['sha256'], 'outputSha256': inspect_fit(converted)['sha256'],
             'evidence': evidence, 'changedPairs': changed, 'output': str(output.absolute()), 'crcValid': True}
    # Save audit first: an interruption must not leave an unmarked converted FIT.
    private_write(audit_path, json_bytes(audit))
    try:
        private_write(output, converted)
    except BaseException:
        audit_path.unlink()
        raise
    return audit


def main():
    parser = argparse.ArgumentParser(description='离线解析 FIT 坐标候选；坐标系由 agent 按外部证据判定。')
    sub = parser.add_subparsers(dest='command', required=True)
    analyzing = sub.add_parser('analyze', help='输出原始坐标和假设 GCJ-02 的候选，不自动认定坐标系')
    analyzing.add_argument('file')
    converting = sub.add_parser('convert', help='已确认大陆 GCJ-02 后，生成 WGS-84 副本')
    converting.add_argument('file')
    converting.add_argument('--output', required=True)
    converting.add_argument('--source-crs', required=True, choices=['gcj02'])
    converting.add_argument('--mainland-confirmed', action='store_true', required=True,
                            help='agent 已确认整条轨迹适用大陆转换，不包含港澳台或跨境区段')
    converting.add_argument('--evidence', required=True, help='实际使用的坐标系或地图对照证据，不填笼统结论')
    args = parser.parse_args()
    try:
        result = analyze(load_fit(args.file)) if args.command == 'analyze' else convert_file(args.file, args.output, args.evidence)
        print(json_bytes(result).decode(), end='')
        return 0
    except (SyncError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, SyncError) else '无法处理本地 FIT 或坐标记录'
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
