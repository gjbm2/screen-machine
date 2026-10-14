"""Neural Teddy Bear   Yes!: Well this one worked quite well :)

Source: https://www.shadertoy.com/view/tlcBRr
Author: oneshade
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: Mesh source: https://groups.csail.mit.edu/graphics/classes/6.837/F03/models/teddy.obj
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
Another test of blackle's mesh to SDF siren network notebook.
Mesh source: https://groups.csail.mit.edu/graphics/classes/6.837/F03/models/teddy.obj

The siren network liked this one better (it even made it smoother!) :)

I also optimized by analytically doing an intersection test with the model's bounding
sphere to avoid running the neural network too many times.

Here's my copy of the notebook:
https://colab.research.google.com/drive/1zdP-LymbnhIzhxOEdZx39C0GBr4AzbFU?authuser=1#scrollTo=O2uKIwI5chcp
*/

// ?!?
//#define SPIKY

vec3 IntersectSphere(in vec3 ro, in vec3 rd, in float r) {
    float a = dot(rd, rd);
    float b = dot(2.0 * ro, rd);
    float c = dot(ro, ro) - r * r;

    vec3 intersect = vec3(0.0);

    float discr = b * b - 4.0 * a * c;
    if (discr > 0.0) {
        float root = sqrt(discr);
        float denom = 2.0 * a;
        float t1 =  (root - b) / denom; // 1st root
        float t2 = -(root + b) / denom; // 2nd root
        intersect = vec3(t1, t2, 1.0);
    }

    return intersect;
}

float sdTeddyBear(in vec3 p) {
    if (length(p) > 1.2) return length(p) - 1.0;
    p = vec3(p.z, p.x, p.y);
    vec4 f0_0=sin(p.y*vec4(-3.54,.16,-3.85,-3.46)+p.z*vec4(-4.11,1.46,3.18,-1.08)+p.x*vec4(.87,2.37,2.00,-.06)+vec4(-4.84,6.35,4.67,1.28));
    vec4 f0_1=sin(p.y*vec4(-.79,-2.43,1.17,.08)+p.z*vec4(2.96,4.06,-.50,2.33)+p.x*vec4(2.18,1.64,-3.70,-2.06)+vec4(2.49,-2.94,-1.55,-4.40));
    vec4 f0_2=sin(p.y*vec4(-2.82,-1.15,-1.02,-2.78)+p.z*vec4(.61,-2.66,-1.37,2.07)+p.x*vec4(-3.03,1.62,-2.73,2.17)+vec4(1.85,6.48,2.10,-8.41));
    vec4 f0_3=sin(p.y*vec4(1.13,-1.73,.30,-.71)+p.z*vec4(2.68,3.20,-3.55,3.69)+p.x*vec4(-2.77,.68,3.42,-2.48)+vec4(2.86,-.73,-.16,-2.31));
    vec4 f1_0=sin(mat4(.71,.16,.40,-.11,-.07,-.53,-.52,-.30,.39,.16,.25,-.29,-.38,-.15,-.24,.24)*f0_0+
        mat4(-1.10,-.65,.12,-.36,-.03,-.40,-.34,.64,.18,.11,.13,.68,1.15,.81,.17,.01)*f0_1+
        mat4(.25,.05,.65,-.42,-.81,.22,-.35,.55,.45,-.11,-.42,.25,-.77,.68,.13,.18)*f0_2+
        mat4(.77,-.04,-.00,-.32,-.56,-.92,-.23,-.20,-.78,.24,-.69,-.19,.94,.18,.51,.51)*f0_3+
        vec4(-2.07,-2.21,-2.80,1.04))/1.0+f0_0;
    vec4 f1_1=sin(mat4(.39,.31,.25,.08,.52,-.71,.79,1.11,.16,.16,-.58,.04,.73,.24,.42,-.43)*f0_0+
        mat4(.52,.25,-.04,.49,-.22,.75,-.06,-.03,.16,-.11,-.13,-.07,.42,.21,-.31,.54)*f0_1+
        mat4(.33,-.33,-.62,-.35,.02,.31,.29,.30,.35,-.26,.20,-1.30,.31,.07,1.34,.07)*f0_2+
        mat4(-.04,.22,-.42,-.32,-.03,.40,.30,.07,.25,-.47,-.02,.62,-.09,1.04,-.28,-.46)*f0_3+
        vec4(2.91,-3.76,-.66,2.25))/1.0+f0_1;
    vec4 f1_2=sin(mat4(-.05,.69,.54,.50,1.04,-.74,-.04,-.61,-.24,-.46,-.08,-.04,.41,.24,.43,.69)*f0_0+
        mat4(-.04,.07,-.14,-.04,.47,.84,.41,-.45,.09,.11,-.10,.20,.10,-.26,-.15,.86)*f0_1+
        mat4(.70,1.12,.49,.18,-.25,1.15,.54,-.11,-.77,-.40,.76,.56,.38,-.47,-.92,.27)*f0_2+
        mat4(-.25,-.72,-.11,-.33,1.01,.68,.08,-.76,-.19,.45,-.31,-.48,.31,-.21,.05,.66)*f0_3+
        vec4(-2.16,.49,-2.85,-1.92))/1.0+f0_2;
    vec4 f1_3=sin(mat4(.55,.17,.17,.47,-.46,.91,.81,1.00,.08,.30,.28,.01,-.31,-.58,.39,-.31)*f0_0+
        mat4(.58,.46,-.13,.75,-.17,-.14,-.21,-.19,-.11,.37,.47,.35,.77,-.42,.11,.01)*f0_1+
        mat4(-.50,.03,-.61,.79,.04,-.54,.39,-.45,.69,-.43,-.04,-1.36,-.06,-.00,.08,1.19)*f0_2+
        mat4(-.55,.87,-.21,.29,-.26,-.10,.02,.71,-.28,-.47,.62,-.24,.21,.14,-1.35,-.25)*f0_3+
        vec4(.41,.31,1.27,3.67))/1.0+f0_3;
    vec4 f2_0=sin(mat4(.05,-.62,.18,.13,1.28,-.06,-.32,.05,1.17,-.09,.00,1.21,-.92,1.33,-1.33,.75)*f1_0+
        mat4(2.55,.44,-1.25,.31,-.87,-.11,.27,-1.17,.81,.29,.68,.73,.73,.02,-.16,.51)*f1_1+
        mat4(.56,-.56,-.05,1.78,1.40,.15,-.90,-.86,.02,.91,.66,.14,.81,-1.19,-.74,.50)*f1_2+
        mat4(.88,.59,-.45,.10,.05,.00,-.94,.24,.49,-.29,-.50,.75,-.22,-.25,.40,-.03)*f1_3+
        vec4(-.41,-2.09,2.22,-.79))/1.4+f1_0;
    vec4 f2_1=sin(mat4(.06,.37,.57,-.46,.47,.97,-.03,.98,-.30,-.73,.13,-.62,-.38,-.49,.23,-.11)*f1_0+
        mat4(-.59,.02,-.25,.32,-.50,.80,-.38,.40,.06,.09,-.74,.05,-1.98,-.87,-.49,-.25)*f1_1+
        mat4(.86,-.97,.09,.09,-.91,.05,1.04,-.09,.01,-1.03,.10,-.10,1.25,1.01,-.79,.27)*f1_2+
        mat4(-.06,-.11,-.10,-.32,-.25,-.58,-.30,1.49,-.72,.10,-.50,-1.04,.22,.31,-.44,.38)*f1_3+
        vec4(2.22,1.33,-3.42,.01))/1.4+f1_1;
    vec4 f2_2=sin(mat4(-.67,-.19,-.86,.11,-.73,-.33,.89,.59,-.28,-.15,-.72,-.18,.39,1.06,.28,.65)*f1_0+
        mat4(.16,.20,.41,-.03,.18,.20,-.10,-.05,-.33,-1.03,-.06,-.94,.35,.18,.54,.32)*f1_1+
        mat4(-.07,-.11,.32,.26,.13,-.48,-.74,.97,-.84,.19,.74,.91,-.15,.07,.34,-.09)*f1_2+
        mat4(.02,.33,.90,-.43,-.35,.97,1.09,-.03,.87,-.29,-.26,.11,-.36,.65,.71,.05)*f1_3+
        vec4(2.32,1.86,2.46,-.34))/1.4+f1_2;
    vec4 f2_3=sin(mat4(-.16,.74,.45,.23,-.68,-.06,-.63,1.13,.09,.48,.42,-1.22,.08,.13,.64,.55)*f1_0+
        mat4(.36,.41,.50,.01,.42,-.13,-.86,-.88,-.43,-.22,-.77,-.18,1.05,.25,.87,-1.13)*f1_1+
        mat4(.11,-.18,.34,.54,-.53,.63,-.11,-.55,-.41,.78,-.46,-.41,.23,-.31,.16,.20)*f1_2+
        mat4(.50,-.89,-.30,-.24,.81,-.96,.50,-.25,.31,-.32,.20,.37,.36,-.25,-.52,-.86)*f1_3+
        vec4(1.63,-3.71,2.15,.50))/1.4+f1_3;
    float d=(dot(f2_0,vec4(.02,-.07,-.06,.03))+
        dot(f2_1,vec4(.07,.05,.05,-.04))+
        dot(f2_2,vec4(-.09,.09,.05,-.06))+
        dot(f2_3,vec4(-.12,.11,.07,-.03))+
        0.177)*.8;
   #ifdef SPIKY
   p*=150.;
   if (d<.03) d+=.01*dot(sin(p), cos(p.zxy));
   #endif
   return d;
}

vec3 getTeddyNormal(in vec3 p) {
    vec3 e = vec3(0.01, 0.0, 0.0);
    return normalize(vec3(sdTeddyBear(p + e.xyy) - sdTeddyBear(p - e.xyy),
                          sdTeddyBear(p + e.yxy) - sdTeddyBear(p - e.yxy),
                          sdTeddyBear(p + e.yyx) - sdTeddyBear(p - e.yyx)));
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 screenCenter = 0.5 * iResolution.xy;

    vec2 mouse = vec2(iTime, 0.0);
    if (iMouse.z > 0.0) {
        mouse = (iMouse.xy - screenCenter) / iResolution.y * 3.14;
    }

    vec2 uv = (fragCoord - screenCenter) / iResolution.y;
    fragColor = vec4(0.0, 0.0, 0.0, 1.0);

    vec3 ro = vec3(0.0, 0.0, 2.5);
    vec3 rd = normalize(vec3(uv, -1.0));

    float cy = cos(mouse.x), sy = sin(mouse.x);
    float cp = cos(mouse.y), sp = sin(mouse.y);
    ro.yz *= mat2(cp, -sp, sp, cp);
    ro.xz *= mat2(cy, -sy, sy, cy);
    rd.yz *= mat2(cp, -sp, sp, cp);
    rd.xz *= mat2(cy, -sy, sy, cy);

    vec3 bSphereIntersect = IntersectSphere(ro, rd, 1.0);
    if (bSphereIntersect.z > 0.5) {
        float tMin = min(bSphereIntersect.x, bSphereIntersect.y);
        float tMax = max(bSphereIntersect.x, bSphereIntersect.y);
        fragColor.rgb += tMin / 10.0;

        vec3 p;
        bool hitModel = false;
        float t = tMin;
        for (int i=0; i < 100; i++) {
            p = ro + rd * t;
            float d = sdTeddyBear(p);
            if (d < 0.001) {
                hitModel = true;
                break;
            }

            if (t > tMax) {
                break;
            }

            t += d;
        }

        if (hitModel) {
            vec3 n = getTeddyNormal(p);
            vec3 l = vec3(-0.58, 0.58, 0.58);

            float tex = 1.0;

            #ifdef SPIKY
            p *= 150.0;
            tex = 1.0 + 0.5 * dot(sin(p), cos(p.zxy));
            #endif

            fragColor.rgb += vec3(0.6, 0.4, 0.2) * tex;
            fragColor.rgb *= max(0.2, dot(n, l));
        }

        else {
            fragColor.rgb += mix(vec3(0.25, 0.25, 1.0), vec3(1.0), 0.5 + 0.5 * rd.y);
        }
    }

    else {
        fragColor.rgb += mix(vec3(0.25, 0.25, 1.0), vec3(1.0), 0.5 + 0.5 * rd.y);
    }
}'''


class StNeuralTeddyBearYes(GlslSource):
    name = 'st_neural_teddy_bear_yes'
    label = 'Neural Teddy Bear   Yes!'
    description = 'Well this one worked quite well :)'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
