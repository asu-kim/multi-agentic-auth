"""Depth-based rectangular target detection. Imported only in the ROS environment."""
import math
import cv2
import numpy as np


def detect_box(depth, config):
    """Return one rectangular foreground target, or reject missing/ambiguous scenes.

    Depth is registered to color and explicitly scaled by the deployment profile.
    No hue filtering, GUI input, or automatic chassis movement.
    """
    if depth.ndim != 2 or depth.shape != (config['height'], config['width']):
        raise ValueError('Depth dimensions do not match calibrated camera dimensions.')
    x0, y0, x1, y1 = config['roi']
    meters = depth.astype(np.float64) * config['depth_scale']
    crop = meters[y0:y1, x0:x1]
    valid = np.isfinite(crop) & (crop >= config['depth_min_m']) & (crop <= config['depth_max_m'])
    if np.count_nonzero(valid) < config['min_area_px']:
        raise ValueError('No usable depth in pickup region.')
    near = np.percentile(crop[valid], .2)
    mask = (valid & (crop <= near + config['surface_band_m'])).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    targets = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not config['min_area_px'] <= area <= config['max_area_px']:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if x <= 1 or y <= 1 or x+w >= crop.shape[1]-1 or y+h >= crop.shape[0]-1:
            continue  # cropped objects/table surface are not grasp targets
        approx = cv2.approxPolyDP(contour, .03 * cv2.arcLength(contour, True), True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        rect = cv2.minAreaRect(contour)
        rw, rh = rect[1]
        if min(rw, rh) <= 0 or max(rw, rh)/min(rw, rh) > 3 or area/(rw*rh) < .8:
            continue
        cx, cy = rect[0]
        inside = np.zeros(crop.shape, np.uint8)
        cv2.drawContours(inside, [contour], -1, 255, -1)
        inside = cv2.erode(inside, np.ones((5, 5), np.uint8))
        samples = crop[(inside > 0) & valid]
        if len(samples) < 20 or np.percentile(samples, 90)-np.percentile(samples, 10) > config['surface_band_m']:
            continue
        z = float(np.median(samples))
        fx, fy = config['camera_k'][0], config['camera_k'][4]
        # Approximate footprint bounds, conservative for elongated targets.
        side_m = max(rw, rh) * z / min(fx, fy)
        if not config['box_min_m'] <= side_m <= config['box_max_m']:
            continue
        corners = cv2.boxPoints(rect)
        vector = corners[0] - corners[1]
        # Supplied compute_joint5(vy,vx) is called as compute_joint5(vx,vy).
        wrist = math.degrees(math.atan2(float(vector[0]), float(vector[1])))
        targets.append({'x': float(cx+x0), 'y': float(cy+y0), 'z': z, 'wrist': wrist})
    if len(targets) != 1:
        raise ValueError('Expected exactly one reachable rectangular foreground box; found %d.' % len(targets))
    return targets[0]


def stable(previous, current):
    if previous is None:
        return False
    angle = abs((current['wrist']-previous['wrist']+90) % 180 - 90)
    return (math.hypot(current['x']-previous['x'], current['y']-previous['y']) <= 5
            and abs(current['z']-previous['z']) <= .005 and angle <= 5)


def target_in_base(target, pose, config):
    fx, fy, cx, cy = (config['camera_k'][i] for i in (0, 4, 2, 5))
    camera = np.array([(target['x']-cx)*target['z']/fx,
                       (target['y']-cy)*target['z']/fy, target['z'], 1.])
    roll, pitch, yaw = pose[3:]
    cr, sr, cp, sp, cyaw, syaw = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    rx = np.array([[1,0,0],[0,cr,-sr],[0,sr,cr]])
    ry = np.array([[cp,0,sp],[0,1,0],[-sp,0,cp]])
    rz = np.array([[cyaw,-syaw,0],[syaw,cyaw,0],[0,0,1]])
    transform = np.eye(4)
    transform[:3,:3] = rz @ ry @ rx
    transform[:3,3] = pose[:3]
    xyz = (transform @ np.array(config['camera_to_end']) @ camera)[:3] + config['offset_m']
    for value, bounds in zip(xyz, config['workspace_m']):
        if not math.isfinite(value) or not bounds[0] <= value <= bounds[1]:
            raise ValueError('Detected target is outside the calibrated arm workspace; reposition the box.')
    return xyz
