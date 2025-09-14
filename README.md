# Video Slicer

A minimal Flask web UI for slicing large videos into playable chunks (no re-encode) using `ffmpeg`.

- No re-encoding — segments stay playable and are keyframe-aligned
- Live progress via Server-Sent Events (SSE)
- Download individual parts or a single ZIP of all parts

## Requirements

- Python 3.9+
- ffmpeg and ffprobe available on your PATH

Install Python deps:

```bash
pip install -r requirements.txt
```

## Run locally

```bash
python app.py
```

Open the app at:

- http://127.0.0.1:5000

## Usage

1. Upload a video file
2. Choose a target chunk size in MB (default 500MB)
3. Optionally set an output filename pattern (e.g. `out_part%03d.mp4`)
4. Click Slice
5. Watch progress; upon completion, download individual files or use the "Download all (.zip)" action

All uploaded files are saved under `uploads/` and outputs under `outputs/` (both are ignored by git per `.gitignore`).

## How it works

- We estimate segment duration from the container bitrate (or size/duration fallback)
- We segment using `-c copy` to avoid re-encoding and keep streams intact
- Segmentation is time-based and keyframe-aligned via the ffmpeg segment muxer

Key command used:

```bash
ffmpeg -hide_banner -nostats -loglevel error \
  -i <input> -c copy -map 0 \
  -f segment -segment_time <secs> -reset_timestamps 1 \
  <out_pattern>
```

## API/Routes

- `GET /` — Index form and recent jobs
- `POST /start` — Start a slicing job (async)
- `GET /job/<job_id>` — Job view with live progress
- `GET /progress/<job_id>` — SSE stream of progress
- `GET /download/<job_id>?f=<filename>` — Download a single part
- `GET /download_all/<job_id>` — Download a ZIP of all parts

## Development Notes

- Templates live in `templates/index.html`
- Long-running ffmpeg process streams progress lines parsed server-side
- Recent jobs are listed on the index for quick access

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
