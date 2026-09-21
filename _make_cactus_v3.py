"""仙人掌图标生成器（v3）—— 高对比、实心底座、小尺寸可读。

问题背景（用户反复投诉"图标是空的/错的"）：
  旧图是细长线描仙人掌，32px 下有效像素仅 20%、且底部花盆过窄，
  在深色任务栏/白色资源管理器背景上都"发飘"，看起来像空图标。

本脚本生成：
  · 主体：圆润饱满的三柱仙人掌（主干 + 左右两臂），高饱和绿 + 深绿描边
  · 顶部：醒目的粉色花朵（深粉外圈 + 亮黄花心），保证 16px 也能看出是仙人掌
  · 底座：宽厚的陶土色花盆，带高光/暗面，给图标一个稳定的视觉"落点"
  · 背景：无（透明），但主体铺满 ~88% 画布，小尺寸不缩水

输出：__xianrenzhang_icon_v3.png（1024 母图）
"""

import os
from PIL import Image, ImageDraw

APP = os.path.dirname(os.path.abspath(__file__))
S = 1024                      # 母图边长
OUT = os.path.join(APP, "__xianrenzhang_icon_v3.png")


def rr(d, box, r, fill, outline=None, width=0):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    OUTLINE = (26, 94, 44, 255)          # 深绿描边
    GREEN = (58, 190, 92, 255)           # 主体绿
    GREEN_HI = (124, 226, 140, 255)      # 高光绿
    GREEN_SH = (36, 150, 72, 255)        # 暗面绿

    # ── 画布利用：主体占 8%~92% ──
    L, R = int(S * 0.10), int(S * 0.90)
    # 花盆
    pot_top = int(S * 0.735)
    pot_bot = int(S * 0.935)
    pot_out = int(S * 0.075)

    # ── 花盆 ──
    # 盆沿（略宽的横条）
    rr(d, [L, pot_top, R, pot_top + int(S * 0.055)], int(S * 0.018),
       (214, 122, 62, 255), outline=(120, 60, 26, 255), width=int(S * 0.012))
    # 盆身（梯形收窄）
    body = [(L + pot_out, pot_top + int(S * 0.045)),
            (R - pot_out, pot_top + int(S * 0.045)),
            (R - pot_out * 2, pot_bot),
            (L + pot_out * 2, pot_bot)]
    d.polygon(body, fill=(196, 106, 52, 255), outline=(120, 60, 26, 255))
    # 盆身高光（左侧竖条）
    d.polygon([(L + pot_out + 10, pot_top + int(S * 0.06)),
               (L + pot_out + int(S * 0.05), pot_top + int(S * 0.06)),
               (L + pot_out * 2 + int(S * 0.03), pot_bot - 8),
               (L + pot_out * 2 + 6, pot_bot - 8)],
              fill=(232, 152, 96, 255))

    # ── 主干（从盆里长出来，向上到接近顶端）──
    trunk_w = int(S * 0.175)
    cx = S // 2
    trunk_top = int(S * 0.245)
    trunk_bot = pot_top + int(S * 0.045)
    rr(d, [cx - trunk_w // 2, trunk_top, cx + trunk_w // 2, trunk_bot],
       int(trunk_w * 0.36), fill=GREEN, outline=OUTLINE, width=int(S * 0.016))
    # 主干高光
    rr(d, [cx - trunk_w // 2 + int(S * 0.030), trunk_top + int(S * 0.045),
           cx - trunk_w // 2 + int(S * 0.058), trunk_bot - int(S * 0.02)],
       int(S * 0.012), fill=GREEN_HI)

    # ── 左右两臂（先从主干伸出横向短臂，再向上）──
    arm_w = int(S * 0.115)
    joint = int(trunk_w * 0.30)

    def arm(sign):
        # 横向连接段
        y0 = int(S * 0.415)
        y1 = y0 + arm_w
        x_in = cx + sign * (trunk_w // 2 - joint)
        x_out = cx + sign * int(S * 0.245)
        rr(d, [min(x_in, x_out), y0, max(x_in, x_out), y1],
           int(arm_w * 0.42), fill=GREEN, outline=OUTLINE, width=int(S * 0.016))
        # 竖直上举段
        xa, xb = x_out - arm_w // 2, x_out + arm_w // 2
        top = int(S * 0.305) if sign < 0 else int(S * 0.335)
        rr(d, [xa, top, xb, y1], int(arm_w * 0.42), fill=GREEN,
           outline=OUTLINE, width=int(S * 0.016))
        # 暗面（提示圆柱感）
        if sign > 0:
            rr(d, [xb - int(S * 0.022), top + int(S * 0.02), xb - int(S * 0.006), y1],
               int(S * 0.010), fill=GREEN_SH)

    arm(-1)
    arm(1)

    # ── 顶部花朵（16px 下最重要的辨识特征）──
    fx, fy = cx, int(S * 0.175)
    petal = int(S * 0.115)
    # 深红外圈花瓣
    for ang in range(0, 360, 45):
        import math
        rad = math.radians(ang)
        px = fx + int(math.cos(rad) * petal * 0.62)
        py = fy + int(math.sin(rad) * petal * 0.62)
        r = int(petal * 0.46)
        d.ellipse([px - r, py - r, px + r, py + r], fill=(232, 48, 78, 255))
    # 中心亮黄花心
    r2 = int(petal * 0.56)
    d.ellipse([fx - r2, fy - r2, fx + r2, fy + r2],
              fill=(255, 206, 64, 255), outline=(196, 118, 10, 255),
              width=int(S * 0.010))
    r3 = int(petal * 0.26)
    d.ellipse([fx - r3, fy - r3, fx + r3, fy + r3], fill=(255, 240, 170, 255))

    img.save(OUT)
    print("saved", OUT, img.size)
    return OUT


if __name__ == "__main__":
    build()
