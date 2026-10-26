import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from agents.common.task_models import TaskRequest
from agents.robot_agent.robot_agent import RobotAgent, RobotAction
from agents.robot_agent.camera_grasp_tool import CameraGraspTool
from agents.robot_agent.grasp_config import validate, checked_joints

PROFILE=Path(__file__).resolve().parents[1]/'config/camera_grasp.example.json'

def profile():
    c=json.loads(PROFILE.read_text())
    c['calibrated']=c['depth_registered_to_color']=True
    return c


class ConfigTests(unittest.TestCase):
    def test_requires_calibration(self):
        with self.assertRaises(ValueError):
            validate(json.loads(PROFILE.read_text()))
        validate(profile())

    def test_rejects_bad_calibration_and_unreachable_joints(self):
        for key,value in [('depth_scale',float('nan')),('roi',[0,0,1000,480]),('view_joints',[0]*5),
                          ('workspace_m',[[.4,.1],[-.1,.1],[0,.2]])]:
            c=profile(); c[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(c)
        with self.assertRaises(ValueError):
            checked_joints([90,80,20,100,90,30],profile())


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_and_missing_config_do_not_start_process(self):
        tool=CameraGraspTool()
        with patch.dict('os.environ',{'ROBOT_GRASP_ENABLED':'false'}), patch('asyncio.create_subprocess_exec',new_callable=AsyncMock) as spawn:
            self.assertEqual((await tool.execute())['status'],'error')
            spawn.assert_not_awaited()
        with patch.dict('os.environ',{'ROBOT_GRASP_ENABLED':'true','ROBOT_GRASP_CONFIG':'/not/a/profile'}):
            self.assertEqual((await tool.execute())['status'],'error')

    async def test_explicit_and_model_requests_dispatch_real_tool_result(self):
        llm=AsyncMock()
        llm.generate.return_value=RobotAction(status='completed',output='LLM claim',tool='grasp_box')
        robot=RobotAgent(llm)
        robot.grasp_tool=AsyncMock()
        robot.grasp_tool.execute.return_value={'status':'error','output':'No stable target'}
        result=await robot.execute_task(TaskRequest(query='grab box',requested_tool='grasp_box'))
        self.assertEqual(result.output,'No stable target')
        llm.generate.assert_not_awaited()
        result=await robot.execute_task(TaskRequest(query='grab box'))
        self.assertEqual(result.status,'error')
        self.assertEqual(robot.grasp_tool.execute.await_count,2)

    async def test_repeated_or_concurrent_grasp_rejected(self):
        tool=CameraGraspTool()
        tool.attempted=True
        self.assertEqual((await tool.execute())['status'],'error')
        tool.attempted=False
        async with tool.lock:
            self.assertIn('progress',(await tool.execute())['output'])


try:
    import numpy as np
    from agents.robot_agent.box_vision import detect_box,stable,target_in_base
    HAS_VISION=True
except ImportError:
    HAS_VISION=False


@unittest.skipUnless(HAS_VISION,'Install NumPy and OpenCV to run synthetic vision checks.')
class VisionTests(unittest.TestCase):
    def test_detects_one_rectangle_and_depth_units(self):
        depth=np.full((480,640),500,np.uint16)
        depth[210:270,285:355]=250
        c=profile()
        target=detect_box(depth,c)
        self.assertAlmostEqual(target['z'],.25)
        self.assertAlmostEqual(target['x'],319.5)
        c['depth_scale']=1
        other=detect_box(depth.astype(float)/1000,c)
        self.assertEqual(target,other)

    def test_rejects_empty_ambiguous_invalid_or_cropped_scene(self):
        empty=np.full((480,640),500.,np.float32)
        ambiguous=empty.copy()
        ambiguous[140:200,200:260]=250
        ambiguous[260:320,360:420]=250
        cropped=empty.copy(); cropped[60:140,100:180]=250
        for image in [empty,ambiguous,cropped,np.zeros_like(empty),np.full_like(empty,np.nan)]:
            with self.subTest(),self.assertRaises(ValueError):
                detect_box(image,profile())

    def test_stability_and_workspace(self):
        first={'x':320.,'y':240.,'z':.25,'wrist':90.}
        self.assertTrue(stable(first,dict(first)))
        self.assertFalse(stable(first,dict(first,z=.3)))
        c=profile()
        c['camera_to_end']=np.eye(4).tolist()
        c['camera_k']=[500,0,320,0,500,240,0,0,1]
        c['workspace_m']=[[-.1,.1],[-.1,.1],[.1,.3]]
        self.assertTrue(np.allclose(target_in_base(first,[0]*6,c),[0,0,.25]))
        with self.assertRaises(ValueError):
            target_in_base(dict(first,z=.5),[0]*6,c)
