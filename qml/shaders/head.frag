// ATLAS's talking head: a wireframe hologram, softly key-lit. Per part (Head3D.qml): fill (the translucent skin),
// rim (fresnel edge glow), glow (solid light: the eyes) and line (the wireframe over the skin).
// Premultiplied colour, written straight into View3D's texture (see Head3D.qml).
VARYING vec3 vObj;
VARYING float vE;
VARYING float vRnd;
VARYING float vFacing;
VARYING vec3 vN;

void MAIN()
{
    float facing = clamp(vFacing, 0.0, 1.0);
    float fres = pow(1.0 - facing, 2.2);
    // the glowing edge belongs to the head's outline only: on the front of the face (nose wings, lips,
    // eye sockets) the creases also turn away from us and lit up as bright seams
    fres *= 1.0 - smoothstep(6.5, 10.0, vObj.z);
    float flicker = 0.96 + 0.04 * sin(time * 23.0 + vRnd * 6.0);
    // a soft key light from the upper left models the face (the nose by its sides, not a hot spot on
    // its tip); facing the camera adds nothing, which had lit the nose and the middle of the face
    float key = max(dot(normalize(vN), normalize(vec3(-0.45, 0.55, 0.7))), 0.0);
    // the nose keeps the key light soft: its sides would otherwise stand out as the brightest thing
    float nose = exp(-(pow(vObj.x / 2.2, 2.0) + pow((vObj.y - 0.8) / 2.4, 2.0))) * smoothstep(9.0, 11.0, vObj.z);
    key = mix(key, 0.45, 0.75 * nose);
    // inside the nostrils (surfaces facing down): dark and quiet, not a dense bright mesh
    float nostril = nose * smoothstep(0.15, 0.6, -normalize(vN).y);
    key = mix(key, 0.45, nostril);              // and not black holes either
    float light = (fill * (0.45 + 0.8 * key) + (rim * fres + line * (0.55 + 0.45 * key)) * (1.0 - 0.5 * nostril) + glow) * flicker;
    vec3 c = mix(col.rgb, hot.rgb, clamp(fres * 0.8 * rim + glow * 0.4 + line * 0.2, 0.0, 1.0));
    float low = smoothstep(-19.0, -10.0, vObj.y);
    float a = clamp(light, 0.0, 1.0) * vE * low * fade;
    FRAGCOLOR = vec4(c * a, a);
}
