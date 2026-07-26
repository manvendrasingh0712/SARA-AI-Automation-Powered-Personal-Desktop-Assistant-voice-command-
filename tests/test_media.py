# test_media.py -- run this directly: python test_media.py
import asyncio
from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager

async def main():
    mgr = await MediaManager.request_async()
    sessions = list(mgr.get_sessions())
    print(f"Total sessions found: {len(sessions)}")
    for s in sessions:
        pb = s.get_playback_info()
        props = await s.try_get_media_properties_async()
        print(f"  app={s.source_app_user_model_id}  status={pb.playback_status}  title={props.title!r}")

asyncio.run(main())