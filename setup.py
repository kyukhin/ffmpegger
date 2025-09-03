"""Setup configuration for the ffmpegger package."""

from setuptools import setup, find_packages

setup(
    name="ffmpegger",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "ffmpeg-python>=0.2.0",
    ],
    python_requires=">=3.8",
    author="Kirill Yukhin",
    author_email="kyukhin@tarantool.org",
    description="A tool for converting videos with English audio track selection",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/kyukhin/ffmpegger",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
) 