#version 440
// Holographic Earth for the "net" tool look (hud.qml): dot-matrix continents from land.png
// (Natural Earth 110m land, public domain), graticule, atmosphere rim and a scan sweep, all on the GPU.
// Build: pyside6-qsb --glsl "100es,120,150" --hlsl 50 --msl 12 -o globe.frag.qsb globe.frag
layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float rot;      // longitude turned to the front, radians
    float tilt;     // axis tilt toward the viewer, radians
    float time;     // seconds
    vec4 col;       // hologram colour
    vec4 hot;       // highlights: sweep, city lights
};
layout(binding = 1) uniform sampler2D land;

const float PI = 3.14159265;
const float R = 0.84;   // globe radius in the item (the rest is atmosphere)

void main() {
    vec2 p = (qt_TexCoord0 * 2.0 - 1.0) / R;
    p.y = -p.y;
    float r = length(p);
    if (r > 1.0) {   // atmosphere: a soft halo fading out
        float edge = 1.0 - smoothstep(0.86, 1.0, length(qt_TexCoord0 * 2.0 - 1.0));   // gone before the item's edge
        float h = exp(-(r - 1.0) * 9.0) * 0.55 * edge;
        fragColor = vec4(col.rgb, 1.0) * h * qt_Opacity;
        return;
    }
    vec3 n = vec3(p, sqrt(1.0 - r * r));
    float ct = cos(tilt), st = sin(tilt);
    vec3 w = vec3(n.x, n.y * ct + n.z * st, -n.y * st + n.z * ct);   // undo the tilt
    float lat = asin(clamp(w.y, -1.0, 1.0));
    float lon = atan(w.x, w.z) + rot;
    vec2 uv = vec2(fract(lon / (2.0 * PI) + 0.5), 0.5 - lat / PI);

    // dot matrix: rows of dots, fewer per row toward the poles so they stay evenly spaced
    float rows = 90.0;
    float row = floor(uv.y * rows);
    float rowLat = (0.5 - (row + 0.5) / rows) * PI;
    float perRow = max(6.0, floor(2.0 * rows * cos(rowLat)));
    vec2 cell = vec2(uv.x * perRow, uv.y * rows);
    float isLand = texture(land, vec2((floor(cell.x) + 0.5) / perRow, (row + 0.5) / rows)).r;
    vec2 f = fract(cell) - 0.5;
    float dotv = smoothstep(0.36, 0.18, length(f)) * isLand;

    // graticule every 15 degrees
    vec2 g = abs(fract(vec2(lon, lat) / (PI / 12.0) + 0.5) - 0.5);
    vec2 fw = fwidth(vec2(lon, lat) / (PI / 12.0));
    float lines = max(1.0 - smoothstep(0.0, fw.x * 1.2, g.x), 1.0 - smoothstep(0.0, fw.y * 1.2, g.y));

    float face = n.z;                         // 1 facing us, 0 at the limb
    float rim = pow(1.0 - face, 2.2);
    float sweep = exp(-pow((fract(uv.x - time * 0.09) - 0.5) * 18.0, 2.0));   // a bright meridian circling
    float scan = 0.5 + 0.5 * sin((p.y + time * 0.35) * 140.0);                 // fine scanlines

    vec3 c = col.rgb * (0.05 + 0.025 * scan)                    // ocean
           + col.rgb * dotv * (0.45 + 0.55 * face)               // continents
           + col.rgb * lines * 0.16 * (0.4 + 0.6 * face)
           + col.rgb * rim * 0.75
           + hot.rgb * sweep * (0.25 + 0.75 * dotv) * 0.8;
    float a = clamp(0.16 + 0.8 * dotv + rim * 0.6 + lines * 0.15 + sweep * 0.3, 0.0, 1.0);
    fragColor = vec4(c, a) * qt_Opacity;
}
