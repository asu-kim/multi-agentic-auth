"""Launch a ROS2 camera-grasp child using the deployment's ROS Python interpreter."""
import asyncio
import json
import os
from pathlib import Path
from agents.robot_agent.grasp_config import validate


class CameraGraspTool:
    def __init__(self):
        self.lock=asyncio.Lock()
        self.attempted=False
        self.process=None

    async def execute(self):
        if self.lock.locked():
            return {'status':'error','output':'A camera grasp is already in progress.'}
        async with self.lock:
            if self.attempted:
                return {'status':'error','output':'A grasp was already attempted. Inspect/reset the robot before another attempt.'}
            if os.getenv('ROBOT_GRASP_ENABLED','false').lower()!='true':
                return {'status':'error','output':'Camera grasp disabled. Verify ROBOT_GRASP_CONFIG calibration before enabling ROBOT_GRASP_ENABLED.'}
            try:
                path=Path(os.environ['ROBOT_GRASP_CONFIG']).expanduser().resolve()
                validate(json.loads(path.read_text()))
                async with asyncio.timeout(75):
                    self.process=await asyncio.create_subprocess_exec(
                        os.getenv('ROBOT_ROS_PYTHON','/usr/bin/python3'),
                        str(Path(__file__).with_name('ros_camera_grasp.py')),'--config',str(path),
                        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
                    self.attempted=True
                    result=None
                    moved=False
                    async for line in self.process.stdout:
                        text=line.decode(errors='replace').strip()
                        if text=='GRASP_MOTION_STARTED':
                            moved=True
                        elif text.startswith('GRASP_RESULT '):
                            result=json.loads(text[len('GRASP_RESULT '):])
                    code=await self.process.wait()
                    if (not isinstance(result,dict) or result.get('status') not in ('completed','error')
                            or not isinstance(result.get('output'),str)):
                        raise ValueError('ROS bridge returned no valid result; check ROS Python and ROS dependencies.')
                    if result['status']=='completed' and (code!=0 or not moved):
                        raise ValueError('ROS bridge did not confirm completion.')
                    return result
            except (KeyError,TypeError,OSError,ValueError,TimeoutError) as exc:
                return {'status':'error','output':(str(exc) or 'Camera grasp timed out.')+
                        (' Motion may have started; inspect the robot before retrying.' if self.attempted else '')}
            finally:
                await self.close()

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(),3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        self.process=None
