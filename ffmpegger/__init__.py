"""FFmpegger - A tool for video conversion with English audio track selection."""

from .convert_eng import convert_one, parse_resolution, scan_eng_astream, fixup_names

__version__ = "0.1.0"
__all__ = [
    'convert_one',
    'parse_resolution',
    'scan_eng_astream',
    'fixup_names'
]
