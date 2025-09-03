# FFmpegger

A tool for video conversion.

## Project Description

The primary goal of FFmpegger is to enable users to set preferred languages for audio and subtitle tracks in video files, particularly for playback on systems where language selection is not available. A common use case is BMW infotainment systems, which lack the ability to choose audio or subtitle languages.

Additionally, the tool provides functionality to resize video content to fit non-standard display resolutions. This is particularly useful for specialized displays, such as those found in BMW infotainment systems, which often have unusual aspect ratios or resolutions.

FFmpegger streamlines the process of preparing video content for devices with limited playback options by automatically selecting the appropriate audio and subtitle tracks based on user preferences. It handles the conversion process efficiently while maintaining video quality and ensuring compatibility with various playback systems.

### Auxiliary Features

FFmpegger also includes several auxiliary features to enhance your video management workflow:

- **Filename Cleanup**: Automatically removes problematic characters from filenames (spaces, parentheses, brackets, quotes, etc.) and replaces them with dots, making filenames more compatible with various systems and players.

- **Metadata Enhancement**: The companion script `inject-titles.sh` allows you to inject the filename into the video's title metadata, which can be useful for organizing and identifying content in media players that display metadata.

These features complement the core functionality and provide a more comprehensive solution for video file management and preparation.

## Dependencies

This package requires the following external dependencies:

- **ffmpeg**: For video processing and conversion
- **jq**: For JSON processing

These dependencies need to be installed separately on your system.

### Installing Dependencies

#### macOS (using Homebrew)

```bash
brew install ffmpeg jq
```

#### Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install ffmpeg jq
```

#### Fedora

```bash
sudo dnf install ffmpeg jq
```

## Installation

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

## Usage

```python
from ffmpegger.convert_eng import convert_one

# Example configuration
cfg = {
    "audio_enforce": 0,
    "fixup_names": False,
    "root_dir": "/path/to/videos",
    "out_dir": "/path/to/output",
    "overwrite": True,
    "verbose": False,
    "settings_video": None,
    "video_fit": None,
    "settings_subtitles": None,
    "subtitles_disabled": True,
    "test_mode": False,
    "a_stream": None,
    "quality": 16,
    "size_target": None
}

# Convert a single video
video_entry = (None, None, "/path/to/video.mp4", None)
result = convert_one(cfg, video_entry)
```

## Testing

```bash
python -m pytest tests/
```

## License

MIT License 