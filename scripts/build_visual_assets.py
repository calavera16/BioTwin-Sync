"""Deterministically supersample the offline navigation silhouette (build only)."""
from pathlib import Path
import base64
import hashlib
import io
import json
from PIL import Image, ImageDraw


def silhouette(height):
    scale = height / 340 * 6
    size = (round(150 * scale), round(340 * scale))
    mask = Image.new('L', size)
    draw = ImageDraw.Draw(mask)
    def xy(points):
        return [(round((x + 75) * scale), round(y * scale)) for x, y in points]
    # Cubic contours preserve shoulders, relaxed arms, waist and leg separation.
    points = [(10, 46)]
    def curve(c1, c2, end):
        a = points[-1]
        for i in range(1, 25):
            t = i / 24; u = 1 - t
            points.append(tuple(u**3*a[k]+3*u*u*t*c1[k]+3*u*t*t*c2[k]+t**3*end[k] for k in (0, 1)))
    for segment in (
        ((10,57),(18,56),(34,62)), ((44,65),(48,74),(50,87)),
        ((55,116),(61,153),(65,177)), ((69,192),(68,202),(61,202)),
        ((55,203),(54,192),(53,184)), ((48,163),(41,133),(36,112)),
        ((33,134),(28,147),(31,165)), ((37,183),(34,203),(32,224)),
        ((31,257),(29,291),(29,320)), ((30,328),(39,332),(37,338)),
        ((31,341),(20,340),(17,336)), ((13,308),(13,280),(10,255)),
        ((7,230),(4,208),(0,200)),
    ): curve(*segment)
    right = list(points)
    points.extend([(-x,y) for x,y in reversed(right[:-1])])
    draw.polygon(xy(points), fill=255)
    draw.ellipse(xy([(-19, 1),(19, 46)]), fill=255)
    # Quiet directional shading; never associated with evidence or clinical state.
    image = Image.new('RGBA', size)
    pixels = image.load()
    for x in range(size[0]):
        f = x / max(1,size[0]-1)
        color = tuple(round(a+(b-a)*f) for a,b in zip((82,111,135),(153,178,196)))
        for y in range(size[1]): pixels[x,y] = (*color,255)
    image.putalpha(mask)
    edge = ImageDraw.Draw(image)
    edge.line(xy(points+[points[0]]), fill=(135,159,179,255), width=max(1,round(scale*.5)), joint='curve')
    edge.arc(xy([(-19,1),(19,46)]),0,360,fill=(135,159,179,255),width=max(1,round(scale*.5)))
    return image.resize((round(150*height/340),height), Image.Resampling.LANCZOS)


def main():
    images = {}
    for height in range(64, 261, 4):
        buffer=io.BytesIO(); silhouette(height).save(buffer,format='PNG',optimize=False)
        images[str(height)] = base64.b64encode(buffer.getvalue()).decode('ascii')
    payload = json.dumps(images,sort_keys=True,separators=(',',':'))
    document = {'format':'biotwin-crew-visuals','version':1,
                'sha256':hashlib.sha256(payload.encode('ascii')).hexdigest(),'images':images}
    path=Path(__file__).resolve().parents[1]/'assets'/'desktop'/'crew-visuals-v1.json'
    path.write_text(json.dumps(document,sort_keys=True,separators=(',',':'))+'\n',encoding='ascii')


if __name__=='__main__': main()
