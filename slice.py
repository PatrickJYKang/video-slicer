#!/usr/bin/env python3
import argparse
import math
import os
import shutil
import subprocess
import sys

def cmd_ok(name):
    return shutil.which(name) is not None

def ffprobe_value(path, key):
    # key in format fields: e.g. format=bit_rate or format=duration or format=size
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", key, "-of", "default=nw=1:nk=1", path],
        text=True
    ).strip()
    return out

def compute_segment_time_secs(input_file, chunk_mb):
    # Prefer container-reported bitrate; fall back to size / duration.
    try:
        bit_rate_str = ffprobe_value(input_file, "format=bit_rate")
        bit_rate = int(bit_rate_str) if bit_rate_str else 0
    except subprocess.CalledProcessError:
        bit_rate = 0

    if bit_rate <= 0:
        # Fallback: bytes / seconds * 8
        dur = float(ffprobe_value(input_file, "format=duration"))
        size_bytes = int(ffprobe_value(input_file, "format=size"))
        if dur <= 0:
            raise RuntimeError("Could not determine duration.")
        bit_rate = (size_bytes * 8) / dur  # bits per second

    target_bits = chunk_mb * 1024 * 1024 * 8
    seg_time = target_bits / bit_rate  # seconds
    # Keep sane bounds
    seg_time = max(1.0, seg_time)
    return seg_time

def main():
    parser = argparse.ArgumentParser(description="Slice a video into ~X MB playable chunks without re-encoding.")
    parser.add_argument("input_file", help="Path to input video")
    parser.add_argument("chunk_mb", type=int, help="Target chunk size in MB")
    parser.add_argument("-o", "--output-pattern", default=None,
                        help="Output pattern, e.g. out_part%03d.mp4 (default derives from input)")
    args = parser.parse_args()

    if not cmd_ok("ffmpeg") or not cmd_ok("ffprobe"):
        print("ffmpeg and ffprobe are required on PATH.", file=sys.stderr)
        sys.exit(1)

    if not os.path.isfile(args.input_file):
        print("Input file not found.", file=sys.stderr)
        sys.exit(1)

    base, ext = os.path.splitext(args.input_file)
    out_pattern = args.output_pattern or f"{base}_part%03d{ext or '.mp4'}"

    try:
        seg_time = compute_segment_time_secs(args.input_file, args.chunk_mb)
    except Exception as e:
        print(f"Failed to estimate segment time: {e}", file=sys.stderr)
        sys.exit(1)

    # Copy streams, segment on nearest keyframes; reset timestamps for each chunk.
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", args.input_file,
        "-c", "copy", "-map", "0",
        "-f", "segment",
        "-segment_time", f"{seg_time:.3f}",
        "-reset_timestamps", "1",
        out_pattern
    ]
    try:
        subprocess.check_call(cmd)
    except subprocess.CalledProcessError as e:
        print(f"ffmpeg failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()