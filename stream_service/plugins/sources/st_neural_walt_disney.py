"""Neural Walt Disney: wooo!

Source: https://www.shadertoy.com/view/3tKcDt
Author: yearofthewhopper
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.
// original shader here https://www.shadertoy.com/view/wtVyWK
vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(p,ax)*ax,p,cos(ro))+sin(ro)*cross(ax,p);
}

float scene(vec3 p) {
    if (length(p) > 1.) {
        return length(p)-.8;
    }
    
   vec4 f0_0=sin(p.y*vec4(-4.13,-.07,1.48,-.05)+p.z*vec4(1.65,-2.06,2.38,3.63)+p.x*vec4(-2.66,2.36,1.19,.64)+vec4(4.03,6.53,4.13,-6.25));
    vec4 f0_1=sin(p.y*vec4(-.14,.33,-.32,-3.74)+p.z*vec4(2.94,-3.61,4.17,-4.14)+p.x*vec4(4.89,-2.37,-2.05,-3.58)+vec4(1.02,-4.86,6.21,.72));
    vec4 f0_2=sin(p.y*vec4(.40,-3.00,.47,-1.69)+p.z*vec4(-3.62,.86,-.28,2.37)+p.x*vec4(-4.88,.51,2.60,.80)+vec4(-3.03,1.58,5.23,.17));
    vec4 f0_3=sin(p.y*vec4(2.48,4.37,-3.21,1.57)+p.z*vec4(.51,1.87,-3.12,-.36)+p.x*vec4(.41,-.48,-.66,-1.41)+vec4(-4.29,1.16,3.77,-5.30));
    vec4 f1_0=sin(mat4(-.17,.28,.10,.13,-.59,-.66,-.77,.08,-.24,-.22,-.40,.78,-.03,.04,.52,.00)*f0_0+
mat4(-.53,.26,-.14,.29,-.45,.13,-.30,.72,-.21,-.12,.02,.25,.05,-.10,.08,.26)*f0_1+
    mat4(-.02,-.14,-.66,-.08,.06,.46,.40,-.12,.42,.59,-.27,.50,-.52,-.09,.15,.20)*f0_2+
    mat4(.05,-.13,-1.07,1.10,.81,.24,.04,.16,-.41,.08,-.38,.43,.40,-.89,-.69,.90)*f0_3+
    vec4(-2.19,.66,-1.85,-.58))/1.0+f0_0;
vec4 f1_1=sin(mat4(-.04,.44,-.14,-.09,-.44,-.29,.18,.63,-.32,-.29,-.21,-.48,-.04,-.51,.24,.02)*f0_0+
    mat4(.54,.04,-.25,.37,-.05,-.42,.18,-.19,-.70,-.20,-.10,1.00,.04,-.12,-.07,.74)*f0_1+
    mat4(-.23,-.06,.44,-.31,.75,-.59,.11,.49,-.50,.56,.52,-.52,.14,.52,1.06,-.42)*f0_2+
    mat4(-.09,-.04,-.05,.63,.05,-.66,-.17,-.03,-.61,.21,-.56,.32,.25,-.07,-.30,-.08)*f0_3+
    vec4(2.77,-1.48,2.87,-3.56))/1.0+f0_1;
vec4 f1_2=sin(mat4(.44,-.21,.07,-.47,.60,-.92,-.26,-.50,.20,-.31,-.56,.11,.58,-.31,-.55,-.07)*f0_0+
    mat4(-.68,.03,.39,-.21,.03,-.13,-.21,.05,.19,.35,-.33,.03,-.15,-.09,-.12,-.01)*f0_1+
    mat4(.36,-.05,-.51,.28,.05,-.26,.11,.34,.02,.29,1.16,.68,-.60,.49,.18,.44)*f0_2+
    mat4(-.01,-.17,-1.25,-.26,.14,.63,.32,-.07,.42,.23,-.07,.25,.01,.22,.19,-.52)*f0_3+
    vec4(-.82,-1.84,-.12,-2.06))/1.0+f0_2;
vec4 f1_3=sin(mat4(.25,-.28,.06,-.60,-.29,.67,-.54,-.53,-.09,.01,-.25,-.68,-.39,-.19,.04,-.40)*f0_0+
    mat4(.25,.16,-.29,-.53,.08,.02,-.20,-.61,-.92,-.24,.26,.14,-.15,.12,-.07,.06)*f0_1+
    mat4(-.25,-.12,.56,-.36,.38,.62,-.39,-.32,-.89,-.45,1.01,.19,.74,.12,-.47,.07)*f0_2+
    mat4(.64,-.29,-.60,-.05,-.45,.80,.13,-.27,.20,-.12,-.34,-.34,.30,.06,-.91,.18)*f0_3+
    vec4(.90,-2.76,.73,2.84))/1.0+f0_3;
vec4 f2_0=sin(mat4(-.50,.78,.15,-.48,-1.01,.01,-1.07,1.10,-.55,.56,-.41,-.46,.09,-.01,-.14,.01)*f1_0+
    mat4(.97,.07,.62,-.14,-.26,-.19,-.67,.65,-.15,-.55,-.08,-1.22,.08,.24,.62,-.10)*f1_1+
    mat4(-.50,.18,-.21,-.58,.26,-.17,.12,.51,.11,-.18,.02,-1.04,.71,-.70,.79,-.72)*f1_2+
    mat4(.21,-.66,.56,.23,1.25,.59,-.20,.97,-.23,.03,-.40,.65,.25,.07,.04,.64)*f1_3+
    vec4(1.80,2.34,1.60,2.12))/1.4+f1_0;
vec4 f2_1=sin(mat4(.20,.36,.42,-.36,-.15,.32,-.44,-.41,-.25,-.43,-.49,-.12,.72,.52,.31,.77)*f1_0+
    mat4(-.46,-.68,.43,.50,.32,-.31,.35,-.05,-.45,.54,.75,1.16,-.03,.19,.56,.19)*f1_1+
    mat4(-.59,.59,-.81,1.23,-.29,-1.18,-.01,-.14,-1.21,.80,-.48,.78,-.49,-1.50,.03,.61)*f1_2+
    mat4(-.54,.06,-.06,.19,-.25,-.34,-1.38,-.99,.34,-1.49,.51,-.65,.15,.11,.41,-.40)*f1_3+
    vec4(-.86,2.45,-1.41,2.41))/1.4+f1_1;
vec4 f2_2=sin(mat4(.58,-.63,-.22,.58,-.68,.28,.91,.98,-.17,.10,.29,-.58,.20,.31,-.19,-.94)*f1_0+
    mat4(.38,.71,-.32,-.93,-1.24,-.51,-.23,.17,.26,-.52,.59,-.53,.67,.40,-.25,.21)*f1_1+
    mat4(-.25,.06,.23,-.28,1.08,.50,-.17,.39,.50,-.02,.28,-.15,-.51,.53,1.30,-.35)*f1_2+
    mat4(.29,.02,-.06,-.20,.05,.22,-.41,.82,.15,.33,.16,.62,-1.00,-.21,-.13,-.13)*f1_3+
    vec4(-2.04,-1.12,2.20,2.19))/1.4+f1_2;
vec4 f2_3=sin(mat4(-1.60,.59,.78,-.36,-.74,-.25,-.95,.29,1.02,.60,-.05,-.09,.05,.60,1.10,-.70)*f1_0+
    mat4(.87,.93,-.99,1.10,-.10,.76,-.92,-.52,-.59,.41,.06,-.49,-.14,.06,-.03,.77)*f1_1+
    mat4(.52,-.69,-.03,-.82,.31,.33,-1.26,.92,.00,-.35,.66,-.56,-.03,-1.56,-1.20,.85)*f1_2+
    mat4(.46,.72,.64,-.30,2.39,.11,-1.27,.25,.13,.54,.14,.38,.63,.43,-.05,.13)*f1_3+
    vec4(2.27,.73,.82,1.94))/1.4+f1_3;
return dot(f2_0,vec4(.05,-.04,.05,.04))+
    dot(f2_1,vec4(.06,-.04,-.04,.05))+
    dot(f2_2,vec4(-.05,-.06,.09,-.05))+
    dot(f2_3,vec4(-.02,.01,.05,-.06))+
    0.285;
}

vec3 norm(vec3 p) {
    mat3 k = mat3(p,p,p)-mat3(0.001);
    return normalize(scene(p) - vec3(scene(k[0]),scene(k[1]),scene(k[2])));
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = (fragCoord-0.5*iResolution.xy)/iResolution.y;
    vec2 mouse = (iMouse.xy-0.5*iResolution.xy)/iResolution.y;

    vec3 cam = normalize(vec3(1.5,uv));
    vec3 init = vec3(-3.,0,0);
    
    float yrot = 0.5;
    float zrot = iTime*.2;
    if (iMouse.z > 0.) {
        yrot += -4.*mouse.y;
        zrot = 4.*mouse.x;
    }
    cam = erot(cam, vec3(0,1,0), yrot);
    init = erot(init, vec3(0,1,0), yrot);
    cam = erot(cam, vec3(0,0,1), zrot);
    init = erot(init, vec3(0,0,1), zrot);
    
    vec3 p = init;
    bool hit = false;
    for (int i = 0; i < 150 && !hit; i++) {
        float dist = scene(p);
        hit = dist*dist < 1e-6;
        p+=dist*cam;
        if (distance(p,init)>5.) break;
    }
    vec3 n = norm(p);
    vec3 r = reflect(cam,n);
    vec3 nz = p - erot(p, vec3(1), 2.) + erot(p, vec3(1), 4.);
    float spec = length(sin(r*3.5+sin(nz*120.)*.15)*.4+.6)/sqrt(3.);
    spec *= smoothstep(-.3,.2,scene(p+r*.2));
    vec3 col = vec3(.3,.22,.2)*spec + pow(spec,8.);
    float bgdot = length(sin(cam*10.)*.4+.6)/2.;
    vec3 bg = vec3(.0,.4,.91) * bgdot + pow(bgdot, 5.);
    fragColor.xyz = hit ? col : bg;
    fragColor = smoothstep(-.02,1.05,sqrt(fragColor)) * (1.- dot(uv,uv)*.5);
}'''


class StNeuralWaltDisney(GlslSource):
    name = 'st_neural_walt_disney'
    label = 'Neural Walt Disney'
    description = 'wooo!'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
