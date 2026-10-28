"""FFmpegger - converts movies and whole seasons to MP4 with English subtitles burnt in."""

from .convert_eng import (analyze_episodes, clean_name, convert_one, episode_of, filter_path,
                          parse_resolution, pick_audio, pick_subtitles, pick_video, scan_videos)

__version__ = "0.2.0"
__all__ = [
    'analyze_episodes',
    'clean_name',
    'convert_one',
    'episode_of',
    'filter_path',
    'parse_resolution',
    'pick_audio',
    'pick_subtitles',
    'pick_video',
    'scan_videos',
]
