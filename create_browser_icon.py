"""
create_browser_icon.py — 生成「经典圆形浏览器(圈圈) + 仙人掌 + 暗色调」图标。

设计意图：
- 外圈：经典浏览器 logo 那种「彩色环/轨道」的圆形轮廓（暗色调：青→蓝→紫渐变）
- 中心：仙人掌剪影（亮绿）+ 顶部粉花（与桌面 app 仙人掌同源），作为品牌元素
- 整体深色底圆，透明外缘 → 像正经浏览器图标，但色调偏暗、带 cactus 标识

只依赖 Pillow，输出多分辨率 .ico（256/128/64/48/32/16）+ 一张预览 png。
"""
import math
from PIL import Image, ImageDraw


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_color(c1, c2, t):
    return tuple(int(round(lerp(c1[i], c2[i], t))) for i in range(3))


def conic_gradient_color(angle01, stops):
    """按角度在多个色标间插值，做出环的彩色轨道渐变。"""
    seg = angle01 * (len(stops) - 1)
    i = int(seg)
    if i >= len(stops) - 1:
        return stops[-1]
    return lerp_color(stops[i], stops[i + 1], seg - i)


def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = size / 2.0

    # 缩放系数（基于 256 设计稿）
    s = size / 256.0

    def R(x):
        return x * s

    # ── 外圆底盘（暗色）──
    disc_r = R(108)
    # 径向暗渐变：中心稍亮，边缘更暗
    steps = 60
    for i in range(steps, 0, -1):
        t = i / steps
        r = disc_r * t
        # 中心 #1b2c44 -> 边缘 #0c1726
        col = lerp_color((27, 44, 68), (12, 23, 38), 1 - t)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
    # 顶部高光弧
    d.arc([cx - disc_r, cy - disc_r, cx + disc_r, cy + disc_r],
          start=200, end=340, fill=(120, 160, 210, 90), width=max(1, int(R(4))))

    # ── 彩色环（浏览器轨道，暗色调渐变：青→蓝→紫→青）──
    outer_r = R(100)
    inner_r = R(66)
    mid_r = (outer_r + inner_r) / 2
    ring_w = outer_r - inner_r
    ring_bbox = [cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r]
    stops = [
        (31, 182, 166),   # 青
        (47, 155, 255),   # 蓝
        (95, 119, 200),   # 靛
        (31, 182, 166),   # 回到青
    ]
    # 用细弧段拼出渐变环
    seg = 3
    for a in range(0, 360, seg):
        col = conic_gradient_color(a / 360.0, stops)
        d.arc(ring_bbox, a, a + seg + 1, fill=col + (255,), width=int(ring_w))
    # 环内高光（让轨道有光泽）
    d.arc([cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r],
          start=250, end=320, fill=(255, 255, 255, 70), width=max(1, int(ring_w * 0.25)))

    # ── 中心仙人掌剪影（亮绿）──
    green = (63, 207, 107)
    green_dk = (40, 160, 80)

    def rrect(x0, y0, x1, y1, r, fill):
        d.rounded_rectangle([R(x0), R(y0), R(x1), R(y1)], radius=R(r), fill=fill)

    # 主干
    rrect(112, 96, 144, 170, 17, green)
    # 左臂（横段穿入主干，再加向上短段）
    rrect(92, 124, 118, 144, 10, green)        # 横臂（与主干重叠 112..118）
    rrect(100, 112, 118, 138, 9, green)        # 向上短臂
    # 右臂
    rrect(138, 118, 166, 138, 10, green)       # 横臂（与主干重叠 138..144）
    rrect(150, 108, 168, 132, 9, green)        # 向上短臂
    # 主干带描边
    d.rounded_rectangle([R(112), R(96), R(144), R(170)], radius=R(17),
                        fill=green, outline=green_dk, width=max(1, int(R(2))))

    # 顶部粉花（与桌面仙人掌同源）
    flower_c = (233, 96, 160)
    d.ellipse([R(116), R(76), R(140), R(100)], fill=flower_c + (255,))
    d.ellipse([R(122), R(82), R(134), R(94)], fill=(255, 214, 120, 255))

    # 轨道小绿点（呼应 cactus 主题，像浏览器彩色球）
    d.ellipse([cx + R(88) - R(7), cy - R(18) - R(7), cx + R(88) + R(7), cy - R(18) + R(7)],
              fill=(63, 207, 107, 255))

    return img


def save_ico(images, path):
    # 用最大那张交给 Pillow 生成多分辨率 ICO（最稳的写法）
    big = images[0]
    big.save(path, format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


def main():
    sizes = [256, 128, 64, 48, 32, 16]
    imgs = [make_icon(s) for s in sizes]
    out_ico = "__browser_cactus_icon.ico"
    save_ico(imgs, out_ico)
    # 预览 png
    preview = make_icon(256)
    preview.save("__browser_cactus_preview.png")
    print("WROTE", out_ico, "and __browser_cactus_preview.png")
    print("sizes:", sizes)


if __name__ == "__main__":
    main()
