// ATLAS's talking head (Head3D.qml, geometry from head3d.py). Expressions are the model's morph
// targets: a custom vertex shader gets them unblended, so they are blended here (absolute targets).
// Each vertex flies in from its place on the neural sphere after its own delay (morph).
// Model units: cm, y up, facing +z, head centre ~(0, -1, 0).
VARYING vec3 vObj;
VARYING float vE;
VARYING float vRnd;
VARYING float vFacing;
VARYING vec3 vN;

float hash(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }

void MAIN()
{
    vec3 base = VERTEX;
    vec3 p = base;
    p += MORPH_WEIGHTS[0] * (MORPH_POSITION(0) - base);
    p += MORPH_WEIGHTS[1] * (MORPH_POSITION(1) - base);
    p += MORPH_WEIGHTS[2] * (MORPH_POSITION(2) - base);
    p += MORPH_WEIGHTS[3] * (MORPH_POSITION(3) - base);
    p += MORPH_WEIGHTS[4] * (MORPH_POSITION(4) - base);
    p += MORPH_WEIGHTS[5] * (MORPH_POSITION(5) - base);
    p += MORPH_WEIGHTS[6] * (MORPH_POSITION(6) - base);
    p += MORPH_WEIGHTS[7] * (MORPH_POSITION(7) - base);
    p += MORPH_WEIGHTS[8] * (MORPH_POSITION(8) - base);
    p += MORPH_WEIGHTS[9] * (MORPH_POSITION(9) - base);
    p += MORPH_WEIGHTS[10] * (MORPH_POSITION(10) - base);
    p += MORPH_WEIGHTS[11] * (MORPH_POSITION(11) - base);
    p += MORPH_WEIGHTS[12] * (MORPH_POSITION(12) - base);
    float rnd = hash(floor(base * 4.0));
    vec3 d = p - vec3(0.0, -1.0, 0.0);
    float delay = 0.15 + 0.45 * smoothstep(0.0, 22.0, length(d)) + 0.1 * rnd;   // face first, neck last
    float e = clamp(morph * 1.8 - delay, 0.0, 1.0);
    e = e * e * (3.0 - 2.0 * e);
    vec3 sp = vec3(0.0, -1.0, 0.0) + normalize(d) * (13.0 + 4.0 * rnd);
    VERTEX = mix(sp, p, e);
    vObj = VERTEX;
    vE = e;
    vRnd = rnd;
    vec3 wp = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz;
    vN = normalize(NORMAL_MATRIX * NORMAL);
    vFacing = dot(vN, normalize(CAMERA_POSITION - wp));
    // the wireframe sits `lift` toward the camera (world units), so it wins the depth test over the
    // surface it outlines but never over anything a few mm in front (lips over teeth, lids over eyes)
    wp += normalize(CAMERA_POSITION - wp) * lift;
    POSITION = VIEWPROJECTION_MATRIX * vec4(wp, 1.0);
}
