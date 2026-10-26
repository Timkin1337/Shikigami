"""Что играет сейчас (любой плеер, который отдаёт сессию Windows: браузер, Spotify, Яндекс Музыка...)."""

_mgr = {"m": None}


async def _manager():
    if _mgr["m"] is None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        _mgr["m"] = await M.request_async()
    return _mgr["m"]


async def now_playing():
    try:
        m = await _manager()
        s = m.get_current_session()
        if not s:
            return None
        props = await s.try_get_media_properties_async()
        info = s.get_playback_info()
        tl = s.get_timeline_properties()
        status = int(info.playback_status)  # 4 = Playing, 5 = Paused
        return {"title": props.title, "artist": props.artist, "app": s.source_app_user_model_id,
                "playing": status == 4,
                "position": tl.position.total_seconds() if tl and tl.position else 0,
                "duration": tl.end_time.total_seconds() if tl and tl.end_time else 0}
    except Exception as e:
        return {"error": str(e)}


async def control(action):
    try:
        m = await _manager()
        s = m.get_current_session()
        if not s:
            return False
        fn = {"play_pause": s.try_toggle_play_pause_async, "next": s.try_skip_next_async,
              "prev": s.try_skip_previous_async, "stop": s.try_stop_async}[action]
        return bool(await fn())
    except Exception:
        return False
