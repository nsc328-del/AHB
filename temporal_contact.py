"""Keep same-shot frames adjacent so a visual model can compare subject positions."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def build(asset, windows, images):
    directory = Path(asset["_analysis_cache_dir"]) / "temporal-contacts"
    directory.mkdir(parents=True, exist_ok=True)
    output = list(images)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18)
    except OSError:
        font = ImageFont.load_default(size=18)
    for window in windows:
        selected = sorted(
            [image for image in images if any(abs(image["time"] - t) < .0006 for t in window["frame_times"])],
            key=lambda frame: frame["time"],
        )
        identity = {
            "version": 1, "window": window["id"], "cut_times": window["cut_times"],
            "frames": [(f["time"], hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest()) for f in selected],
        }
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        path = directory / (key + ".jpg")
        if not path.exists():
            # Each row belongs to one detected shot; no adjacent cross-cut pair
            # is presented as a continuous movement.
            bounds = [window["start"], *window["cut_times"], window["end"]]
            rows = []
            for group, (start, end) in enumerate(zip(bounds, bounds[1:])):
                frames = [f for f in selected if start <= f["time"] < end]
                for offset in range(0, len(frames), 4):
                    rows.append((group + 1, frames[offset:offset + 4]))
            if not rows:
                continue
            tile_w, tile_h, label_h = 320, 240, 34
            canvas = Image.new("RGB", (tile_w * 4, (tile_h + label_h) * len(rows)), "#e9edf3")
            draw = ImageDraw.Draw(canvas)
            for row_index, (group, frames) in enumerate(rows):
                y = row_index * (tile_h + label_h)
                for column, frame in enumerate(frames):
                    x = column * tile_w
                    with Image.open(frame["path"]) as original:
                        image = original.convert("RGB")
                        image.thumbnail((tile_w - 6, tile_h))
                        canvas.paste(image, (x + (tile_w - image.width) // 2, y + label_h))
                    draw.text((x + 8, y + 7), f"Shot {group} | {frame['time']:.3f}s", fill="#182438", font=font)
            canvas.save(path, quality=94)
        output.append({
            "time": window["start"], "path": str(path),
            "label": (
                f"窗口 {window['id']} 的同镜头顺序联系表。只重复本次已提供的原帧，"
                "按每格绝对时间先后对照；Shot编号来自已有检测切点，不是新增切点。"
                "比较同一Shot内人物身体朝向、脚的接触位置及相对固定背景的位置。"
                "整幅背景位移不等于人物行走；脚不在画面内时不能声称看到了步态。"
                "不同Shot的姿态差异不能作为连续动作证据。"
            ),
        })
    return output
