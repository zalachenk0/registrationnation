# ============================================================================
# File:         check_license_focus_SL.py
# Written by:   SL (Seth Leija)
# What it does: Bench tool that checks the license barcode reads sharply at the
#               camera focus setting (run with the kiosk stopped).
# Team-written: Logic written by SL (Seth Leija). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  OpenCV; zxing-cpp; python-dotenv; Picamera2; libcamera; zxing-
#               cpp and OpenCV, through the scanner package.
# ============================================================================
"""Run on the Pi with the kiosk stopped; never prints decoded license contents."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import tempfile
import time


def focus_positions(low, high):
    # Start near the 9-inch document plane, then cover nearer/farther distances.
    values = [4.5, 4, 5, 3.5, 5.5, 3, 6, 2.5, 6.5, 2, 7, 1, 8, 0, 9, 10, 12, 15]
    return list(dict.fromkeys(max(low, min(high, value)) for value in values))


def save_settings(path, position):
    settings = {
        'KIOSK_CAMERA_BACKEND': 'picamera2',
        'KIOSK_CAMERA_WIDTH': '4608',
        'KIOSK_CAMERA_HEIGHT': '2592',
        'KIOSK_CAMERA_AUTOFOCUS': '0',
        'KIOSK_CAMERA_LENS_POSITION': str(position),
    }
    original = path.read_text(encoding='utf-8') if path.exists() else ''
    remaining = [line for line in original.splitlines()
                 if not any(re.match(r'^\s*(?:export\s+)?' + key + r'\s*=', line)
                            for key in settings)]
    content = '\n'.join(remaining + [f'{key}={value}' for key, value in settings.items()]) + '\n'
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.focus-', delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def find_license(frame):
    """Cheap center/whole-frame probes; no identity information leaves this function."""
    import cv2
    import zxingcpp
    from extraction_service_BV_SL import parse_license

    h, w = frame.shape[:2]
    center = frame[h // 4:3 * h // 4, w // 4:3 * w // 4]
    for candidate in (center, frame):
        for gray in (cv2.cvtColor(candidate, cv2.COLOR_BGR2GRAY), candidate[:, :, 0]):
            # Unlike the partner decoder, do not suppress decoder exceptions.
            results = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.PDF417)
            for result in results:
                if result.valid and result.text:
                    try:
                        parse_license(result.text)
                        return True
                    except ValueError:
                        continue
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Save focus only after the kiosk decoder succeeds.')
    args = parser.parse_args()
    import cv2
    from dotenv import load_dotenv
    from picamera2 import Picamera2
    from libcamera import controls
    from extraction_service_BV_SL import scan_license

    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    print('Keep the whole back of the license centered, flat, and still.', flush=True)
    print('Testing focus positions; this may take a few minutes. No license text is printed.', flush=True)
    camera = Picamera2(int(os.getenv('KIOSK_CAMERA_INDEX', '0')))
    best_score, best_focus = -1, None
    try:
        if 'AfMode' not in camera.camera_controls or 'LensPosition' not in camera.camera_controls:
            raise RuntimeError('This camera does not expose adjustable focus.')
        config = camera.create_video_configuration(
            main={'size': (4608, 2592), 'format': 'RGB888'}, buffer_count=3,
            controls={'FrameRate': 8, 'AfMode': controls.AfModeEnum.Manual})
        camera.configure(config)
        low, high, _ = camera.camera_controls['LensPosition']
        camera.start()
        time.sleep(2)
        for position in focus_positions(low, high):
            camera.set_controls({'AfMode': controls.AfModeEnum.Manual, 'LensPosition': position})
            time.sleep(1.5)
            # Drain buffered frames so this capture reflects the new lens setting.
            for _ in range(3):
                camera.capture_array('main')
            frame = camera.capture_array('main')
            h, w = frame.shape[:2]
            center = frame[h // 4:3 * h // 4, w // 4:3 * w // 4]
            score = float(cv2.Laplacian(cv2.cvtColor(center, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
            if score > best_score:
                best_score, best_focus = score, position
            print(f'Focus {position:g}; capture {w}x{h}; center sharpness {score:.1f}', flush=True)
            if not find_license(frame):
                continue
            # Confirm the existing application can decode this exact full frame.
            try:
                scan_license(frame)
            except ValueError:
                print('RESULT: center barcode decoded, but the kiosk full-frame decoder failed.', flush=True)
                print('Settings unchanged. Report this result; the crop path needs adjustment.')
                return 2
            print(f'RESULT: PASS. Kiosk decoded the license at focus {position:g}.', flush=True)
            if args.apply:
                save_settings(root / '.env', position)
                print('Saved camera settings to .env. Stripe and printer settings retained.')
            else:
                print(f'Tested setting: KIOSK_CAMERA_LENS_POSITION={position:g} (manual focus).')
            return 0
        print(f'RESULT: no barcode decoded. Highest center sharpness at focus {best_focus}; this is NOT a verified setting.')
        print('Settings unchanged. Report this result and whether the old project still reads this same license.')
        return 2
    finally:
        camera.close()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('Stopped. Restart the kiosk when ready.')
        raise SystemExit(130)
    except Exception as exc:
        print(f'Diagnostic error ({type(exc).__name__}): {exc}')
        print('Check that the kiosk backend and other camera applications are stopped.')
        raise SystemExit(1)
