# Draws jarvis.ico (arc reactor) used by ui.py and Jarvis.exe.
from PIL import Image, ImageDraw
import math
S = 256
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
c = S / 2
def circ(r, **kw): d.ellipse((c - r, c - r, c + r, c + r), **kw)
circ(124, fill=(16, 19, 23, 255), outline=(255, 182, 39, 255), width=8)
circ(104, fill=(11, 93, 110, 255))
circ(96, fill=(20, 24, 30, 255))
for i in range(10):
    a = math.radians(i * 36)
    x, y = c + 72 * math.cos(a), c + 72 * math.sin(a)
    d.ellipse((x - 14, y - 14, x + 14, y + 14), fill=(208, 138, 74, 255), outline=(74, 42, 20, 255), width=3)
for r, col in ((52, (111, 243, 255, 255)), (40, (180, 250, 255, 255)), (26, (255, 255, 255, 255))):
    circ(r, fill=col)
tri = [(c + 46 * math.cos(math.radians(a)), c + 46 * math.sin(math.radians(a))) for a in (-90, 30, 150)]
d.polygon(tri, outline=(11, 93, 110, 255), width=6)
im.save("jarvis.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
im.save("jarvis_icon_preview.png")
