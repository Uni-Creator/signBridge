import 'dart:typed_data';

import 'package:camera/camera.dart';
import 'package:image/image.dart' as img;

/// Converts the camera's actual buffer layout, not the requested format.
/// Keeps the existing model input size and colour conversion coefficients.
class CameraFrameConverter {
  static Uint8List toJpeg(CameraImage frame) {
    final rgb = toRgb(frame);
    final resized = img.copyResize(rgb, width: 224, height: 224);
    return Uint8List.fromList(img.encodeJpg(resized, quality: 85));
  }

  static img.Image toRgb(CameraImage frame) {
    if (frame.width <= 0 || frame.height <= 0) {
      throw const FormatException('Invalid camera frame dimensions');
    }
    switch (frame.format.group) {
      case ImageFormatGroup.jpeg:
        if (frame.planes.length != 1) {
          throw const FormatException('Expected one JPEG plane');
        }
        img.Image? decoded;
        try {
          decoded = img.decodeJpg(frame.planes.single.bytes);
        } on img.ImageException {
          throw const FormatException('Invalid JPEG camera frame');
        }
        if (decoded == null) {
          throw const FormatException('Invalid JPEG camera frame');
        }
        return decoded;
      case ImageFormatGroup.yuv420:
        return _yuv420(frame);
      case ImageFormatGroup.bgra8888:
        return _bgra(frame);
      default:
        throw FormatException(
            'Unsupported camera format: ${frame.format.group}');
    }
  }

  static void _validatePlane(Plane plane, int width, int height, int stride,
      {int channels = 1}) {
    final rowBytes = (width - 1) * stride + channels;
    final requiredBytes = (height - 1) * plane.bytesPerRow + rowBytes;
    if (stride < channels ||
        plane.bytesPerRow < rowBytes ||
        plane.bytes.length < requiredBytes) {
      throw const FormatException(
          'Invalid camera plane stride or buffer length');
    }
  }

  static img.Image _yuv420(CameraImage frame) {
    if (frame.planes.length != 3) {
      throw const FormatException('Expected three YUV420 planes');
    }
    final y = frame.planes[0];
    final u = frame.planes[1];
    final v = frame.planes[2];
    final yStride = y.bytesPerPixel ?? 1;
    final uStride = u.bytesPerPixel ?? 1;
    final vStride = v.bytesPerPixel ?? 1;
    final chromaWidth = (frame.width + 1) ~/ 2;
    final chromaHeight = (frame.height + 1) ~/ 2;
    _validatePlane(y, frame.width, frame.height, yStride);
    _validatePlane(u, chromaWidth, chromaHeight, uStride);
    _validatePlane(v, chromaWidth, chromaHeight, vStride);
    final rgb = img.Image(width: frame.width, height: frame.height);
    for (var row = 0; row < frame.height; row++) {
      for (var col = 0; col < frame.width; col++) {
        final yp = y.bytes[row * y.bytesPerRow + col * yStride];
        final up = u.bytes[(row ~/ 2) * u.bytesPerRow + (col ~/ 2) * uStride];
        final vp = v.bytes[(row ~/ 2) * v.bytesPerRow + (col ~/ 2) * vStride];
        rgb.setPixelRgb(
          col,
          row,
          (yp + 1.402 * (vp - 128)).round().clamp(0, 255),
          (yp - 0.344136 * (up - 128) - 0.714136 * (vp - 128))
              .round()
              .clamp(0, 255),
          (yp + 1.772 * (up - 128)).round().clamp(0, 255),
        );
      }
    }
    return rgb;
  }

  static img.Image _bgra(CameraImage frame) {
    if (frame.planes.length != 1) {
      throw const FormatException('Expected one BGRA plane');
    }
    final plane = frame.planes.single;
    final stride = plane.bytesPerPixel ?? 4;
    _validatePlane(plane, frame.width, frame.height, stride, channels: 4);
    final rgb = img.Image(width: frame.width, height: frame.height);
    for (var row = 0; row < frame.height; row++) {
      for (var col = 0; col < frame.width; col++) {
        final offset = row * plane.bytesPerRow + col * stride;
        // Index the Uint8List view itself so its byte offset is respected.
        rgb.setPixelRgb(col, row, plane.bytes[offset + 2],
            plane.bytes[offset + 1], plane.bytes[offset]);
      }
    }
    return rgb;
  }
}
