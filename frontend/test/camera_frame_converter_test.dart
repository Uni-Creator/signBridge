import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:camera_platform_interface/camera_platform_interface.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image/image.dart' as img;
import 'package:SignBridge/core/services/camera_frame_converter.dart';

CameraImage frame(ImageFormatGroup format, int width, int height,
        List<CameraImagePlane> planes) =>
    CameraImage.fromPlatformInterface(
      CameraImageData(
          format: CameraImageFormat(format, raw: 0),
          width: width,
          height: height,
          planes: planes),
    );

CameraImagePlane plane(List<int> bytes, int row, [int? pixel = 1]) =>
    CameraImagePlane(
        bytes: Uint8List.fromList(bytes),
        bytesPerRow: row,
        bytesPerPixel: pixel);

List<num> rgb(img.Image image, int x, int y) {
  final p = image.getPixel(x, y);
  return [p.r, p.g, p.b];
}

void main() {
  test('packed and independently padded YUV planes produce identical colours',
      () {
    final packed = frame(ImageFormatGroup.yuv420, 4, 4, [
      plane(List.filled(16, 100), 4),
      plane([128, 180, 90, 128], 2),
      plane([128, 90, 180, 128], 2),
    ]);
    final padded = frame(ImageFormatGroup.yuv420, 4, 4, [
      plane([
        100,
        100,
        100,
        100,
        0,
        0,
        100,
        100,
        100,
        100,
        0,
        0,
        100,
        100,
        100,
        100,
        0,
        0,
        100,
        100,
        100,
        100
      ], 6),
      // Padding bytes deliberately differ from actual chroma values.
      plane([128, 0, 180, 0, 0, 90, 0, 128], 5, 2),
      plane([128, 0, 0, 90, 0, 0, 0, 180, 0, 0, 128], 7, 3),
    ]);
    final expected = CameraFrameConverter.toRgb(packed);
    final actual = CameraFrameConverter.toRgb(padded);
    for (var y = 0; y < 4; y++) {
      for (var x = 0; x < 4; x++) {
        expect(rgb(actual, x, y), rgb(expected, x, y));
      }
    }
    expect(rgb(actual, 0, 0), [100, 100, 100]);
    expect(rgb(actual, 2, 0), [47, 109, 192]);
  });

  test('odd dimensions use rounded-up chroma sizes', () {
    final input = frame(ImageFormatGroup.yuv420, 3, 3, [
      plane(List.filled(9, 80), 3),
      plane(List.filled(4, 128), 2),
      plane(List.filled(4, 128), 2),
    ]);
    expect(rgb(CameraFrameConverter.toRgb(input), 2, 2), [80, 80, 80]);
  });

  test('BGRA preserves channel order, padding and typed-data view offset', () {
    final bytes = Uint8List.fromList([
      99, 99, 99, 99, // Prefix outside the view.
      0, 0, 255, 255, 0, 255, 0, 255, 99, 99, 99, 99,
      255, 0, 0, 255, 255, 255, 255, 255,
    ]);
    final input = frame(ImageFormatGroup.bgra8888, 2, 2, [
      CameraImagePlane(bytes: Uint8List.sublistView(bytes, 4), bytesPerRow: 12),
    ]);
    final actual = CameraFrameConverter.toRgb(input);
    expect(rgb(actual, 0, 0), [255, 0, 0]);
    expect(rgb(actual, 1, 0), [0, 255, 0]);
    expect(rgb(actual, 0, 1), [0, 0, 255]);
    expect(rgb(actual, 1, 1), [255, 255, 255]);
  });

  test('JPEG frames are decoded and encoded at the model input size', () {
    final original = img.Image(width: 8, height: 6);
    img.fill(original, color: img.ColorRgb8(200, 20, 30));
    final input = frame(ImageFormatGroup.jpeg, 8, 6, [
      plane(img.encodeJpg(original), 0),
    ]);
    final output = img.decodeJpg(CameraFrameConverter.toJpeg(input))!;
    expect([output.width, output.height], [224, 224]);
    expect(output.getPixel(100, 100).r, closeTo(200, 5));
  });

  test('truncated buffers and invalid strides fail explicitly', () {
    for (final badPlane in [
      plane([128], 2),
      plane([128, 128], 0),
      plane([128, 128], 2, 0)
    ]) {
      final input = frame(ImageFormatGroup.yuv420, 4, 2, [
        plane(List.filled(8, 100), 4),
        badPlane,
        plane([128, 128], 2),
      ]);
      expect(() => CameraFrameConverter.toRgb(input), throwsFormatException);
    }
  });

  test('unsupported formats and missing planes are reported', () {
    for (final format in [
      ImageFormatGroup.unknown,
      ImageFormatGroup.yuv420,
      ImageFormatGroup.bgra8888,
      ImageFormatGroup.jpeg
    ]) {
      expect(() => CameraFrameConverter.toRgb(frame(format, 2, 2, [])),
          throwsFormatException);
    }
  });

  test('invalid dimensions and corrupt JPEG data are rejected', () {
    expect(
        () => CameraFrameConverter.toRgb(
            frame(ImageFormatGroup.yuv420, 0, 2, [])),
        throwsFormatException);
    expect(
        () => CameraFrameConverter.toRgb(frame(ImageFormatGroup.jpeg, 2, 2, [
              plane([1, 2, 3], 0)
            ])),
        throwsFormatException);
  });
}
