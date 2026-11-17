"""Рисует icon.png и icon.ico для Rust Live Map (нужен Pillow). Запуск: python make_icon.py"""
import math
from PIL import Image, ImageDraw, ImageFilter

S = 1024
RUST = (205, 65, 43)
GLOW = (255, 120, 80)


def draw():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # фон: скруглённый квадрат с вертикальным градиентом
    bg = Image.new("RGBA", (S, S))
    px = bg.load()
    for y in range(S):
        t = y / (S - 1)
        c = (int(44 - 20 * t), int(44 - 20 * t), int(48 - 20 * t), 255)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((24, 24, S - 24, S - 24), radius=210, fill=255)
    img.paste(bg, (0, 0), mask)
    d = ImageDraw.Draw(img)
    cx = cy = S // 2
    # сетка карты
    for k in (-1, 0, 1):
        d.line((cx + k * 250, 150, cx + k * 250, S - 150), fill=(255, 255, 255, 22), width=6)
        d.line((150, cy + k * 250, S - 150, cy + k * 250), fill=(255, 255, 255, 22), width=6)
    # радар: кольца
    for r, a, w in ((330, 70, 14), (220, 110, 14)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=RUST + (a,), width=w)
    # перекрестие
    for ang in (0, 90, 180, 270):
        dx, dz = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        d.line((cx + dx * 250, cy + dz * 250, cx + dx * 390, cy + dz * 390), fill=RUST + (255,), width=26)
    # метка игрока со свечением
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((cx - 150, cy - 150, cx + 150, cy + 150), fill=GLOW + (190,))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(45)))
    d = ImageDraw.Draw(img)
    d.ellipse((cx - 118, cy - 118, cx + 118, cy + 118), fill=RUST + (255,))
    d.ellipse((cx - 118, cy - 118, cx + 118, cy + 118), outline=(255, 235, 225, 255), width=22)
    d.ellipse((cx - 38, cy - 38, cx + 38, cy + 38), fill=(255, 245, 238, 255))
    return img


if __name__ == "__main__":
    im = draw()
    im.resize((256, 256), Image.LANCZOS).save("icon.png")
    im.save("icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icon.png, icon.ico готовы")
