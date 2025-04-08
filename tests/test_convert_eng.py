"""Tests for the video conversion functionality with English audio track selection."""

import os
import pytest
import argparse
from ffmpegger.convert_eng import parse_resolution, convert_one, scan_eng_astream, fixup_names
from .test_utils import ensure_test_video

TEST_VIDEO = "tests/sample_with_eng.mp4"

@pytest.fixture(scope="session", autouse=True)
def setup_test_video():
    """Ensure test video exists before running tests."""
    if not ensure_test_video(TEST_VIDEO):
        pytest.skip("Failed to create test video")

def test_parse_resolution():
    """Test resolution string parsing."""
    assert parse_resolution("1920x1080") == (1920, 1080)
    assert parse_resolution("1280x720") == (1280, 720)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_resolution("invalid")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_resolution("1920")

def test_basic_conversion(tmp_path):
    """Test basic video conversion functionality."""
    input_file = TEST_VIDEO
    
    cfg = {
        "root_dir": os.path.dirname(input_file),
        "out_dir": str(tmp_path),
        "a_stream": "0:1",  # Explicitly set audio stream to first available
        "subtitles_disabled": True,
        "video_fit": None,
        "settings_video": None,
        "verbose": False,
        "size_target": None,
        "quality": 23,
        "overwrite": True,
        "audio_enforce": 0
    }
    
    result = convert_one(cfg, [None, None, input_file, None])
    assert result is True
    assert os.path.exists(os.path.join(str(tmp_path), os.path.basename(input_file)))

def test_audio_track_selection(tmp_path):
    """Test English audio track selection."""
    input_file = TEST_VIDEO
    
    cfg = {
        "root_dir": os.path.dirname(input_file),
        "audio_enforce": 1,  # Use first audio track if English not found
        "verbose": False
    }
    
    track_info = scan_eng_astream(cfg, input_file)
    assert isinstance(track_info, str)
    assert track_info == "0:1"  # Should use first audio track

def test_resolution_adjustment(tmp_path):
    """Test video resolution adjustment."""
    input_file = TEST_VIDEO
    
    cfg = {
        "root_dir": os.path.dirname(input_file),
        "out_dir": str(tmp_path),
        "a_stream": "0:1",  # Explicitly set audio stream to first available
        "subtitles_disabled": True,
        "video_fit": (1280, 720),  # Set desired resolution
        "settings_video": None,
        "verbose": False,
        "size_target": None,
        "quality": 23,
        "overwrite": True,
        "audio_enforce": 0
    }
    
    result = convert_one(cfg, [None, None, input_file, None])
    assert result is True
    assert os.path.exists(os.path.join(str(tmp_path), os.path.basename(input_file)))

def test_filename_cleanup(tmp_path):
    """Test filename cleanup functionality."""
    test_dir = str(tmp_path)
    test_files = [
        "My Movie (2024).mp4",
        "Test [Series] Episode.mp4",
        "Sample «Movie» 2024.mp4"
    ]
    
    # Create test files
    for filename in test_files:
        with open(os.path.join(test_dir, filename), 'w') as f:
            f.write("dummy content")
    
    cfg = {
        "root_dir": test_dir,
        "fixup_names": True
    }
    
    # Run filename cleanup
    fixup_names(cfg)
    
    # Check results
    cleaned_files = os.listdir(test_dir)
    assert "My.Movie.2024.mp4" in cleaned_files
    assert "Test.Series.Episode.mp4" in cleaned_files
    assert "Sample.Movie.2024.mp4" in cleaned_files
