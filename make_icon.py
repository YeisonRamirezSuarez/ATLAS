# Draws atlas.ico (the HUD's neural core: celeste net on a dark puck) used by ui.py and Atlas.exe.
# Drawn at 4x and scaled down for smooth edges; small sizes drop the net, which would turn to noise.
from PIL import Image, ImageDraw, ImageFilter
import math

BG, RIM, ARC, ICE, CORE = (3, 8, 14, 255), (31, 139, 255, 255), (79, 209, 255, 255), (168, 236, 255, 255), (240, 253, 255, 255)


def sphere(n=22):
    """Fibonacci points on a unit sphere, tilted like the HUD core, and links to the 3 nearest."""
    pts = []
    for i in range(n):
        y = 1 - 2 * (i + 0.5) / n
        r, a = math.sqrt(1 - y * y), i * 2.39996
        x, z = r * math.cos(a), r * math.sin(a)
        ax = 0.45
        pts.append((x, y * math.cos(ax) - z * math.sin(ax), y * math.sin(ax) + z * math.cos(ax)))
    links = set()
    for i, p in enumerate(pts):
        near = sorted(range(n), key=lambda j: sum((p[k] - pts[j][k]) ** 2 for k in range(3)))[1:4]
        links |= {tuple(sorted((i, j))) for j in near}
    return pts, links


def draw(size: int, net: bool) -> Image.Image:
    S = size * 4
    c = S / 2
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    circ = lambda r, **kw: d.ellipse((c - r, c - r, c + r, c + r), **kw)
    circ(S * 0.48, fill=BG, outline=RIM, width=max(4, S // 40))
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((c - S * 0.3, c - S * 0.3, c + S * 0.3, c + S * 0.3), fill=(79, 209, 255, 110))
    im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.08)))
    d = ImageDraw.Draw(im)
    if net:
        pts, links = sphere()
        R = S * 0.34
        xy = lambda p: (c + p[0] * R, c + p[1] * R)
        for i, j in links:
            depth = (pts[i][2] + pts[j][2] + 2) / 4
            d.line((xy(pts[i]), xy(pts[j])), fill=ARC[:3] + (int(70 + 150 * depth),), width=max(2, S // 170))
        for p in pts:
            depth = (p[2] + 1) / 2
            r = S * (0.012 + 0.014 * depth)
            x, y = xy(p)
            d.ellipse((x - r, y - r, x + r, y + r), fill=ICE[:3] + (int(120 + 135 * depth),))
    orb = S * (0.11 if net else 0.17)
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((c - orb * 1.9, c - orb * 1.9, c + orb * 1.9, c + orb * 1.9), fill=(168, 236, 255, 170))
    im.alpha_composite(halo.filter(ImageFilter.GaussianBlur(orb * 0.6)))
    d = ImageDraw.Draw(im)
    d.ellipse((c - orb, c - orb, c + orb, c + orb), fill=CORE)
    return im.resize((size, size), Image.LANCZOS)


sizes = [16, 24, 32, 48, 64, 128, 256]
frames = [draw(s, net=s >= 48) for s in sizes]
frames[-1].save("atlas.ico", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
frames[-1].save("atlas_icon_preview.png")
