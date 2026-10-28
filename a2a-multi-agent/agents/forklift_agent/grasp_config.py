"""Shared validation; usable without ROS, OpenCV or NumPy."""
import math


def validate(config):
    if not isinstance(config, dict):
        raise ValueError('Grasp profile must be an object.')
    for key in ('rgb_topic','depth_topic','attempt_lock'):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ValueError('Missing '+key)
    if not config['attempt_lock'].startswith('/'):
        raise ValueError('attempt_lock must be an absolute persistent path.')
    if config.get('calibrated') is not True or config.get('depth_registered_to_color') is not True:
        raise ValueError('Confirm camera/arm calibration and registered depth in the grasp profile first.')
    def vector(value, n, low, high):
        if not isinstance(value, list) or len(value) != n or any(
            type(v) not in (int, float) or not math.isfinite(v) or not low <= v <= high for v in value
        ):
            raise ValueError('Invalid numeric calibration vector.')
    vector(config['camera_k'], 9, -10000, 10000)
    if config['camera_k'][0] <= 0 or config['camera_k'][4] <= 0:
        raise ValueError('Invalid camera focal lengths.')
    matrix = config['camera_to_end']
    if not isinstance(matrix, list) or len(matrix) != 4:
        raise ValueError('camera_to_end must be a 4x4 rigid transform.')
    for row in matrix:
        vector(row, 4, -10, 10)
    if matrix[3] != [0,0,0,1]:
        raise ValueError('Invalid homogeneous transform.')
    for i in range(3):
        for j in range(3):
            dot = sum(matrix[i][k]*matrix[j][k] for k in range(3))
            if abs(dot - (1 if i == j else 0)) > .001:
                raise ValueError('Camera rotation is not orthonormal.')
    vector(config['offset_m'], 3, -.1, .1)
    vector(config['view_joints'], 6, 0, 180)
    limits = config['joint_limits_deg']
    if len(limits) != 6:
        raise ValueError('Six joint limits required.')
    for pair in limits:
        vector(pair, 2, 0, 180)
        if pair[0] >= pair[1]:
            raise ValueError('Invalid joint bounds.')
    for key in ('width', 'height'):
        if type(config[key]) is not int or not 32 <= config[key] <= 4096:
            raise ValueError('Invalid image dimensions.')
    vector(config['roi'], 4, 0, max(config['width'],config['height']))
    x0,y0,x1,y1 = config['roi']
    if any(type(v) is not int for v in config['roi']) or not (0 <= x0 < x1 <= config['width'] and 0 <= y0 < y1 <= config['height']):
        raise ValueError('Invalid pickup ROI.')
    for key, low, high in [('depth_scale',.000001,1), ('depth_min_m',.01,2), ('depth_max_m',.02,3),
            ('surface_band_m',.001,.05), ('box_min_m',.005,.1), ('box_max_m',.01,.15),
            ('closed_deg',0,180), ('lift_joint2_deg',0,180), ('ik_pitch',0,math.pi),
            ('settle_seconds',.5,5), ('frame_max_age',.05,1)]:
        vector([config[key]],1,low,high)
    for pair in [('depth_min_m','depth_max_m'),('box_min_m','box_max_m'),('min_area_px','max_area_px')]:
        if not config[pair[0]] < config[pair[1]]:
            raise ValueError('Invalid ordered bounds: '+str(pair))
    for key in ('min_area_px','max_area_px'):
        if type(config[key]) is not int or not 20 <= config[key] <= config['width']*config['height']:
            raise ValueError('Invalid contour area bounds.')
    if type(config['move_ms']) is not int or not 1000 <= config['move_ms'] <= 5000:
        raise ValueError('Invalid servo timing.')
    if len(config['workspace_m']) != 3:
        raise ValueError('Three workspace bounds required.')
    for pair in config['workspace_m']:
        vector(pair,2,-.5,.5)
        if pair[0] >= pair[1]:
            raise ValueError('Invalid workspace bounds.')
    checked_joints(config['view_joints'], config)
    return config


def checked_joints(joints, config):
    if len(joints) != 6:
        raise ValueError('Six joint angles required.')
    for value, (low,high) in zip(joints, config['joint_limits_deg']):
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError('IK or configured pose is outside joint limits; no clamping is performed.')
    return [int(v) for v in joints]
