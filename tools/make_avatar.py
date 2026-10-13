# -*- coding: utf-8 -*-
"""用 assets/cea-logo.svg 生成 QQ 机器人方形头像 assets/cea-avatar.png。

设计：纯白背景 + 黑色 CEA 字标 + 底部「QQ BOT」标识（黑白配色）。
用法： .venv\\Scripts\\python tools\\make_avatar.py
"""
import tempfile
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "assets" / "cea-logo.svg"
OUT_PATH = ROOT / "assets" / "cea-avatar.png"

SIZE = 1024                # 头像边长（px）
LOGO_WIDTH_RATIO = 0.80    # 字标宽度占头像比例
LOGO_CENTER_Y = 0.42       # 字标中心垂直位置（比例）
LABEL_CENTER_Y = 0.72      # 「QQ BOT」中心垂直位置（比例）
BG = (255, 255, 255)       # 纯白背景（黑白配色）
FG = (0, 0, 0)             # 前景色（字标与文字）
LABEL = "QQ BOT"
FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]


def render_logo(target_width: int) -> Image.Image:
    """把 SVG 字标渲染成 FG 色透明底位图，等比缩放到 target_width。"""
    fg_hex = "%02X%02X%02X" % FG
    svg = SVG_PATH.read_text(encoding="utf-8").replace(
        'fill="currentColor"', f'fill="#{fg_hex}"'
    )
    with tempfile.NamedTemporaryFile("w", suffix=".svg", encoding="utf-8", delete=False) as f:
        f.write(svg)
        tmp_path = f.name
    try:
        drawing = svg2rlg(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # renderPM 后端对透明背景支持不稳：改为白底黑字渲染，再用「反亮度=alpha」抠出前景
    png = renderPM.drawToString(drawing, fmt="PNG", bg=0xFFFFFF)
    raw = Image.open(BytesIO(png)).convert("L")
    raw = ImageOps.invert(raw)
    raw = raw.crop(raw.getbbox())  # 裁掉四周空白边
    logo = Image.new("RGBA", raw.size, (*FG, 0))
    logo.putalpha(raw)

    scale = target_width / logo.width
    return logo.resize((target_width, round(logo.height * scale)), Image.LANCZOS)


def make_background() -> Image.Image:
    """生成纯色背景。"""
    return Image.new("RGB", (SIZE, SIZE), BG)


def load_font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def draw_label(canvas: Image.Image, center_y: int) -> None:
    """在 center_y 处居中绘制带字距的「QQ BOT」。"""
    draw = ImageDraw.Draw(canvas)
    font = load_font(round(SIZE * 0.066))
    tracking = round(SIZE * 0.018)  # 字距

    widths = [draw.textlength(ch, font=font) for ch in LABEL]
    total = sum(widths) + tracking * (len(LABEL) - 1)
    x = (SIZE - total) / 2
    for ch, w in zip(LABEL, widths):
        draw.text((x + w / 2, center_y), ch, font=font, fill=FG, anchor="mm")
        x += w + tracking


def main() -> None:
    canvas = make_background()

    logo_w = round(SIZE * LOGO_WIDTH_RATIO)
    logo = render_logo(logo_w)
    top = round(SIZE * LOGO_CENTER_Y - logo.height / 2)
    canvas.paste(logo, ((SIZE - logo.width) // 2, top), logo)

    draw_label(canvas, round(SIZE * LABEL_CENTER_Y))

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT_PATH, "PNG")
    print(f"头像已生成: {OUT_PATH} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main()
