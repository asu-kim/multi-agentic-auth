import asyncio
import sys

from agents.common.settings import Settings


async def run():
    settings = Settings.from_env()
    processes = []
    watchers = []
    try:
        for role in ("language", "analytics", "robot", "manager"):
            print(f"Starting {role}: {settings.urls[role]}", flush=True)
            processes.append(await asyncio.create_subprocess_exec(
                sys.executable, "-m", f"capability_team.{role}_agent",
            ))
        watchers = [asyncio.create_task(process.wait()) for process in processes]
        done, _ = await asyncio.wait(watchers, return_when=asyncio.FIRST_COMPLETED)
        code = next(iter(done)).result()
        print(f"An agent exited (code {code}); stopping the team.", file=sys.stderr)
        return code or 1
    finally:
        for process in processes:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        for watcher in watchers:
            watcher.cancel()
        await asyncio.gather(*watchers, return_exceptions=True)


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run()))
    except KeyboardInterrupt:
        print("Team stopped.")
