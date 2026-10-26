"""Exercise the real bridge sequencing with fake ROS publishers and camera messages."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

try:
    import numpy as np
    import cv2
    VISION=True
except ImportError:
    VISION=False

BASE=Path(__file__).resolve().parents[1]/'agents/robot_agent'

@unittest.skipUnless(VISION,'Requires OpenCV and NumPy')
class BridgeTests(unittest.TestCase):
    def run_bridge(self, preview=False, invalid_ik=False):
        config=json.loads((BASE.parents[1]/'config/camera_grasp.example.json').read_text())
        config.update(calibrated=True,depth_registered_to_color=True,
                      camera_k=[500,0,320,0,500,240,0,0,1],camera_to_end=np.eye(4).tolist(),
                      workspace_m=[[-.1,.1],[-.1,.1],[.1,.3]])
        recorded=[]
        handlers=[]
        clock=[1000.]
        def tick():
            clock[0]+=.01
            return clock[0]
        class Message:
            pass
        class Publisher:
            def __init__(self,topic): self.topic=topic
            def get_subscription_count(self): return 1
            def publish(self,msg): recorded.append((self.topic,dict(vars(msg))))
        class Client:
            def wait_for_service(self,**kwargs): return True
            def call_async(self,msg):
                if msg.kin_name=='fk':
                    result=types.SimpleNamespace(x=0,y=0,z=0,roll=0,pitch=0,yaw=0)
                else:
                    result=types.SimpleNamespace(joint1=90,joint2=80,joint3=20,joint4=120 if invalid_ik else 30)
                return types.SimpleNamespace(done=lambda:True,result=lambda:result)
        class Node:
            def __init__(self,name): pass
            def create_client(self,*args): return Client()
            def create_publisher(self,kind,topic,*args): return Publisher(topic)
            def get_clock(self): return types.SimpleNamespace(now=lambda:types.SimpleNamespace(nanoseconds=int(clock[0]*1e9)))
            def destroy_node(self): pass
        class Sync:
            def __init__(self,*args): pass
            def registerCallback(self,callback): handlers.append(callback)
        depth=np.full((480,640),500.,np.float32)
        depth[210:270,285:355]=250
        def spin(node,**kwargs):
            tick()
            ns=int(clock[0]*1e9)
            msg=types.SimpleNamespace(width=640,height=480,encoding='32FC1',
                  header=types.SimpleNamespace(stamp=types.SimpleNamespace(sec=ns//10**9,nanosec=ns%10**9)))
            for callback in handlers: callback(msg,msg)
        modules={
            'rclpy':dict(init=lambda:None,shutdown=lambda:None,spin_once=spin),
            'rclpy.node':dict(Node=Node), 'rclpy.qos':dict(qos_profile_sensor_data=object()),
            'sensor_msgs.msg':dict(Image=Message),'arm_msgs.msg':dict(ArmJoints=Message,ArmJoint=Message),
            'arm_interface.srv':dict(ArmKinemarics=types.SimpleNamespace(Request=Message)),
            'message_filters':dict(Subscriber=lambda *a,**kw:object(),ApproximateTimeSynchronizer=Sync),
            'cv_bridge':dict(CvBridge=lambda:types.SimpleNamespace(imgmsg_to_cv2=lambda *a,**kw:depth)),
        }
        fake={}
        for name,attrs in modules.items():
            module=types.ModuleType(name); module.__dict__.update(attrs); fake[name]=module
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules,fake), patch.object(sys,'path',[str(BASE),*sys.path]):
            config['attempt_lock']=str(Path(temp)/'lock')
            spec=importlib.util.spec_from_file_location('test_bridge_module',BASE/'ros_camera_grasp.py')
            bridge=importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
            with patch.object(bridge.time,'monotonic',tick):
                if invalid_ik:
                    with self.assertRaises(ValueError): bridge.run(config,preview)
                    self.assertEqual(len(recorded),1)  # viewing pose only; no approach or close
                    return
                result=bridge.run(config,preview)
                self.assertEqual(Path(config['attempt_lock']).exists(),not preview)
                if not preview:
                    with self.assertRaises(FileExistsError): bridge.run(config,False)
        return result,recorded

    def test_preview_does_not_publish_arm_motion(self):
        result,messages=self.run_bridge(preview=True)
        self.assertEqual(result['status'],'preview')
        self.assertEqual(messages,[])

    def test_one_shot_camera_grasp_sequence_and_persistent_interlock(self):
        result,messages=self.run_bridge()
        self.assertEqual(result['status'],'completed')
        self.assertFalse(result['object_verified'])
        self.assertEqual([topic for topic,_ in messages],['arm6_joints','arm6_joints','arm_joint','arm6_joints'])
        self.assertEqual(messages[2][1]['id'],6)
        self.assertEqual(messages[-1][1]['joint6'],135)
        self.assertEqual(messages[-1][1]['joint2'],120)

    def test_invalid_ik_does_not_close_gripper(self):
        self.run_bridge(invalid_ik=True)
