"""One-shot ROS2 camera grasp. Run with the robot's ROS Python, not the agent venv."""
import argparse
import json
import math
import os
import signal
import time
from pathlib import Path
from grasp_config import validate, checked_joints


def run(config, preview=False):
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from rclpy.node import Node
    from sensor_msgs.msg import Image
    from arm_msgs.msg import ArmJoints, ArmJoint
    from arm_interface.srv import ArmKinemarics
    from message_filters import Subscriber, ApproximateTimeSynchronizer
    from cv_bridge import CvBridge
    from box_vision import detect_box, stable, target_in_base

    rclpy.init()
    node = Node('robot_agent_camera_grasp')
    bridge = CvBridge()
    pair = [None]
    def frames(rgb, depth):
        pair[0] = (time.monotonic(), rgb, depth)
    rgb_sub = Subscriber(node, Image, config['rgb_topic'], qos_profile=qos_profile_sensor_data)
    depth_sub = Subscriber(node, Image, config['depth_topic'], qos_profile=qos_profile_sensor_data)
    sync = ApproximateTimeSynchronizer([rgb_sub,depth_sub],10,.08)
    sync.registerCallback(frames)
    client = node.create_client(ArmKinemarics, 'get_kinemarics')
    six = node.create_publisher(ArmJoints, 'arm6_joints', 10)
    one = node.create_publisher(ArmJoint, 'arm_joint', 10)
    def spin_until(predicate, seconds, error):
        end = time.monotonic()+seconds
        while not predicate():
            if time.monotonic() >= end:
                raise RuntimeError(error)
            rclpy.spin_once(node, timeout_sec=.05)
    def pause(seconds):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=.05)
    def service(request):
        future = client.call_async(request)
        spin_until(future.done,5,'Kinematics service timed out.')
        result = future.result()
        if result is None:
            raise RuntimeError('No kinematics response.')
        return result
    def pose(joints):
        angles = checked_joints(joints, config)
        msg = ArmJoints()
        for i,angle in enumerate(angles,1):
            setattr(msg,'joint'+str(i),angle)
        msg.time = config['move_ms']
        six.publish(msg)
        pause(config['move_ms']/1000+config['settle_seconds'])
    try:
        if not client.wait_for_service(timeout_sec=5):
            raise RuntimeError('get_kinemarics unavailable.')
        if not preview:
            spin_until(lambda: six.get_subscription_count()>0 and one.get_subscription_count()>0,
                       5,'Arm controller unavailable.')
            # Persistent interlock: prevents reopening a potentially loaded gripper after restart.
            lock = Path(config['attempt_lock']).expanduser()
            with lock.open('x') as file:
                file.write('Inspect/reset the robot before manually removing this grasp interlock.\n')
            print('GRASP_MOTION_STARTED',flush=True)
            view = list(config['view_joints'])
            view[0] = 180-view[0]  # ShapeRecognizeNode.pubSixArm convention
            pose(view)
        # FK in the same logical convention used by the supplied Curjoints producer.
        request = ArmKinemarics.Request()
        request.kin_name = 'fk'
        for i,angle in enumerate(config['view_joints'][:5],1):
            setattr(request,'cur_joint'+str(i),float(angle))
        fk = service(request)
        end_pose = [float(getattr(fk,k)) for k in ('x','y','z','roll','pitch','yaw')]
        if not all(math.isfinite(v) for v in end_pose):
            raise ValueError('Invalid FK pose.')
        previous = None
        count = 0
        pair[0] = None
        target = None
        last_error = 'No synchronized RGB/depth frames.'
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            rclpy.spin_once(node,timeout_sec=.05)
            if pair[0] is None:
                continue
            received,rgb,depth = pair[0]
            pair[0] = None
            now_ns = node.get_clock().now().nanoseconds
            stamps = [m.header.stamp.sec*10**9+m.header.stamp.nanosec for m in (rgb,depth)]
            if (time.monotonic()-received > config['frame_max_age'] or
                    any(abs(now_ns-s)/1e9 > config['frame_max_age'] for s in stamps)):
                count=0
                continue
            if (rgb.width,rgb.height)!=(config['width'],config['height']):
                raise ValueError('RGB size differs from calibration; do not resize depth blindly.')
            if depth.encoding not in ('16UC1','32FC1'):
                raise ValueError('Unsupported depth encoding.')
            try:
                target = detect_box(bridge.imgmsg_to_cv2(depth,desired_encoding='passthrough'),config)
                xyz = target_in_base(target,end_pose,config)
            except ValueError as exc:
                count=0
                previous=None
                last_error=str(exc)
                continue
            count = count+1 if stable(previous,target) else 1
            previous=target
            if count>=3:
                break
        else:
            raise RuntimeError('No stable grasp target: '+last_error)
        evidence={'pixel':[target['x'],target['y']], 'depth_m':target['z'], 'base_xyz_m':xyz.tolist()}
        if preview:
            return {'status':'preview','target':evidence,'output':'Target detected. No motion commanded; preview assumes the arm is already at the configured viewing pose.'}
        request = ArmKinemarics.Request()
        request.kin_name='ik'
        request.tar_x,request.tar_y,request.tar_z=map(float,xyz)
        request.roll,request.pitch,request.yaw=0.,float(config['ik_pitch']),0.
        ik=service(request)
        joints=[180-float(ik.joint1),float(ik.joint2),float(ik.joint3),float(ik.joint4),90.,30.]
        angle=target['wrist']
        if angle < 0:
            wrist=abs(angle)
            wrist=180-joints[0]+wrist-90 if wrist<90 else wrist-joints[0]+90
        elif angle>0:
            wrist=180-abs(angle)
            wrist=wrist-joints[0] if angle<90 else wrist-(joints[0]-90)
        else:
            raise ValueError('Zero wrist angle is ambiguous in the supplied grasp implementation.')
        if wrist>135:
            wrist-=90
        elif wrist<45:
            wrist+=90
        joints[4]=wrist
        checked_joints(joints,config)
        lift=list(joints)
        lift[1],lift[5]=config['lift_joint2_deg'],config['closed_deg']
        checked_joints(lift,config)
        pose(joints)
        close=ArmJoint()
        close.id,close.joint,close.time=6,int(config['closed_deg']),config['move_ms']
        one.publish(close)
        pause(config['move_ms']/1000+config['settle_seconds'])
        pose(lift)
        return {'status':'completed','target':evidence,'object_verified':False,
                'output':'Camera-guided approach, close and lift sequence finished. Gripper remains closed. Actual box pickup is not sensor-verified.'}
    finally:
        node.destroy_node()
        rclpy.shutdown()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--preview',action='store_true')
    args=parser.parse_args()
    def stop(signum,frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM,stop)
    try:
        config=validate(json.loads(Path(args.config).read_text()))
        result=run(config,args.preview)
    except (Exception,KeyboardInterrupt) as exc:
        result={'status':'error','output':str(exc) or 'Interrupted; already-issued servo motion may continue.'}
    print('GRASP_RESULT '+json.dumps(result),flush=True)
    return 0 if result['status'] in ('completed','preview') else 1


if __name__=='__main__':
    raise SystemExit(main())
