"""Test utilities for FFmpegger."""

import os
import subprocess
import tempfile
from pathlib import Path

def create_test_video(output_path: str, duration: int = 5) -> bool:
    """
    Create a test video file with English audio track.
    
    Args:
        output_path: Path where the test video should be created
        duration: Duration of the video in seconds (default: 5)
    
    Returns:
        bool: True if video was created successfully, False otherwise
    """
    try:
        # Create output directory if it doesn't exist
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # Generate a test video with English audio using ffmpeg
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi',
            '-i', f'testsrc=duration={duration}:size=1280x720:rate=30',
            '-f', 'lavfi',
            '-i', f'sine=frequency=440:duration={duration}',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-metadata', 'language=eng',
            output_path
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0 and os.path.exists(output_path)
    except Exception as e:
        print(f"Error creating test video: {e}")
        return False

def ensure_test_video(video_path: str) -> bool:
    """
    Ensure that a test video exists at the specified path.
    If it doesn't exist, create it.
    
    Args:
        video_path: Path where the test video should exist
    
    Returns:
        bool: True if video exists or was created successfully, False otherwise
    """
    if os.path.exists(video_path):
        return True
    return create_test_video(video_path) 