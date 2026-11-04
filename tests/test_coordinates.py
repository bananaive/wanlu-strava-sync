import importlib.util
import json
import stat
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/wanlu-strava-sync/scripts'
sys.path.insert(0, str(SCRIPTS))
import fit_coordinates as coords
import wanlu
sys.path.pop(0)


def fit_points(points, endian='<', developer=False, compressed=False, summaries=False):
    fields = [(253, 4, 0x86), (0, 4, 0x85), (1, 4, 0x85), (7, 2, 0x84), (3, 1, 2)]
    body = bytearray([0x60 if developer else 0x40, 0, 0 if endian == '<' else 1])
    body += struct.pack(endian + 'H', 20) + bytes([len(fields)])
    body += bytes(value for field in fields for value in field)
    if developer:
        body += bytes([1, 42, 3, 0])
    for index, (lat, lon) in enumerate(points):
        is_compressed = compressed and index > 0
        body += bytes([0x80 | index % 32 if is_compressed else 0])
        if not is_compressed:
            body += struct.pack(endian + 'I', 1000000000 + index)
        body += struct.pack(endian + 'iiHB', round(lat / coords.SCALE), round(lon / coords.SCALE), 250, 140)
        if developer:
            body += b'\x91\x92\x93'
    if summaries:
        for global_id, local in [(18, 1), (19, 2)]:
            pairs = coords.PAIRS[global_id]
            fields = [(field, 4, 0x85) for pair in pairs for field in pair] + [(8, 4, 0x86)]
            body += bytes([0x40 | local, 0, 0 if endian == '<' else 1])
            body += struct.pack(endian + 'H', global_id) + bytes([len(fields)])
            body += bytes(value for field in fields for value in field)
            body += bytes([local])
            for _ in pairs:
                body += struct.pack(endian + 'ii', round(points[0][0] / coords.SCALE), round(points[0][1] / coords.SCALE))
            body += struct.pack(endian + 'I', 123456)
    header = struct.pack('<BBHI4s', 14, 16, 2100, len(body), b'.FIT')
    header += struct.pack('<H', wanlu.crc16(header))
    content = header + body
    return bytes(content + struct.pack('<H', wanlu.crc16(content)))


class CoordinateTests(unittest.TestCase):
    points = [(39.9, 116.4), (39.91, 116.41), (39.92, 116.42)]

    def test_decode_semicircles_and_do_not_infer_crs_from_region(self):
        content = fit_points(self.points)
        report = coords.analyze(content)
        self.assertEqual(report['sourceCrs'], 'unknown')
        self.assertTrue(report['conversionEligible'])
        self.assertEqual(report['recordPoints'], 3)
        self.assertAlmostEqual(report['samples'][0]['asWgs84']['lat'], 39.9, places=6)
        self.assertGreater(report['samples'][0]['candidateShiftMetres'], 100)
        self.assertEqual(content, fit_points(self.points))

    def test_conversion_preserves_every_noncoordinate_field_and_crc(self):
        for endian in ('<', '>'):
            content = fit_points(self.points, endian, developer=True, compressed=True, summaries=True)
            fields = coords.coordinate_fields(content)
            output, changed = coords.convert(content)
            self.assertEqual(changed, len(fields))
            self.assertEqual(len(content), len(output))
            self.assertEqual(wanlu.crc16(output), 0)
            self.assertEqual(wanlu.crc16(output[:14]), 0)
            allowed = set(range(12, 14)) | set(range(len(content)-2, len(content)))
            for item in fields:
                for offset in item['offsets']:
                    allowed.update(range(offset, offset+4))
            for index, (before, after) in enumerate(zip(content, output)):
                if index not in allowed:
                    self.assertEqual(before, after)
            positions = [p for p in coords.coordinate_fields(output) if p['message'] == 20]
            expected_lat, expected_lon = coords.gcj_to_wgs(*self.points[0])
            self.assertAlmostEqual(positions[0]['lat'], expected_lat, places=6)
            self.assertAlmostEqual(positions[0]['lon'], expected_lon, places=6)
            self.assertLess(positions[0]['lon'], self.points[0][1])
            self.assertEqual(content, fit_points(self.points, endian, developer=True, compressed=True, summaries=True))

    def test_outside_region_is_not_converted(self):
        content = fit_points([(37.7749, -122.4194)])
        report = coords.analyze(content)
        self.assertFalse(report['conversionEligible'])
        self.assertIsNone(report['samples'][0]['ifGcj02'])
        with self.assertRaises(wanlu.SyncError):
            coords.convert(content)

    def test_no_gps_file_remains_unchanged(self):
        content = fit_points([])
        self.assertEqual(coords.analyze(content)['sourceCrs'], 'no_gps')
        with self.assertRaises(wanlu.SyncError):
            coords.convert(content)

    def test_invalid_sentinel_not_changed(self):
        content = bytearray(fit_points(self.points))
        field = coords.coordinate_fields(content)[0]
        struct.pack_into('<i', content, field['offsets'][0], 0x7FFFFFFF)
        struct.pack_into('<H', content, len(content)-2, wanlu.crc16(content[:-2]))
        output, _ = coords.convert(bytes(content))
        self.assertEqual(output[field['offsets'][0]:field['offsets'][0]+4], b'\xff\xff\xff\x7f')

    def test_wrong_field_encoding_and_broken_messages_stop(self):
        content = bytearray(fit_points(self.points))
        # First latitude definition base type is at 14 + 6 + 3 + 2.
        content[25] = 0x88
        struct.pack_into('<H', content, len(content)-2, wanlu.crc16(content[:-2]))
        with self.assertRaises(wanlu.SyncError):
            coords.analyze(bytes(content))
        content = bytearray(fit_points(self.points))
        content[19] = 255  # Claims far more definition fields than the body contains.
        struct.pack_into('<H', content, len(content)-2, wanlu.crc16(content[:-2]))
        with self.assertRaises(wanlu.SyncError):
            coords.analyze(bytes(content))

    def test_new_copy_audit_and_double_conversion_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'original.fit'
            output = Path(directory) / 'converted.fit'
            source.write_bytes(fit_points(self.points))
            original = source.read_bytes()
            with self.assertRaises(wanlu.SyncError):
                coords.convert_file(source, source, 'fixture evidence')
            with self.assertRaises(wanlu.SyncError):
                coords.convert_file(source, output, ' ')
            audit = coords.convert_file(source, output, 'fixture: source declares gcj02')
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(audit['sourceSha256'], wanlu.inspect_fit(original)['sha256'])
            self.assertEqual(audit['outputSha256'], wanlu.inspect_fit(output.read_bytes())['sha256'])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            audit_path = Path(str(output) + '.coordinates.json')
            self.assertEqual(stat.S_IMODE(audit_path.stat().st_mode), 0o600)
            with self.assertRaises(wanlu.SyncError):
                coords.convert_file(output, Path(directory) / 'twice.fit', 'fixture evidence')

    def test_receipt_tracks_actual_upload_and_rejects_unrelated_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(wanlu, 'download', return_value=fit_points(self.points)):
                manifest = wanlu.export({}, ['123'], Path(directory) / 'export')
            source = Path(manifest['records'][0]['file'])
            output = source.with_name('converted.fit')
            coords.convert_file(source, output, 'fixture: source declares gcj02')
            result = wanlu.receipt(manifest['manifest'], '123', 'success',
                                   'https://www.strava.com/activities/456', output)
            self.assertEqual(result['record']['coordinateConversion']['sourceCrs'], 'gcj02')
            self.assertNotEqual(result['record']['sha256'], result['record']['uploadedSha256'])
            audit_path = Path(str(output) + '.coordinates.json')
            audit = json.loads(audit_path.read_text())
            audit['sourceSha256'] = 'unrelated'
            audit_path.write_text(json.dumps(audit))
            with self.assertRaises(wanlu.SyncError):
                wanlu.receipt(manifest['manifest'], '123', 'success',
                              'https://www.strava.com/activities/456', output)


if __name__ == '__main__':
    unittest.main()
