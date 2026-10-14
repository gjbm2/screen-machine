"""Colormap: Selection of colormaps, converted from <https://www.shadertoy.com/view/Nd3fR2>

Source: https://github.com/nwhetsell/isf-shaders/blob/HEAD/Colormap.fs
Author: GehtSieGarNixAn <https://www.shadertoy.com/user/gehtsiegarnixan>
License: Prosperity-3.0.0
license_class: noncommercial
Imported verbatim from GLSL (nwhetsell/isf-shaders); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Uses LYGIA (Prosperity licence, noncommercial).
"""
from stream_service.plugins._lib.glsl import GlslEffect, effect_params


GLSL = r'''/*{
    "CATEGORIES": [
        "Filter"
    ],
    "CREDIT": "GehtSieGarNixAn <https://www.shadertoy.com/user/gehtsiegarnixan>",
    "DESCRIPTION": "Selection of colormaps, converted from <https://www.shadertoy.com/view/Nd3fR2>",
    "INPUTS": [
        {
            "NAME": "inputImage",
            "TYPE": "image"
        },
        {
            "NAME": "id",
            "LABEL": "Colormap",
            "TYPE": "long",
            "DEFAULT" : 0,
            "VALUES": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
            "LABELS" : [
                "viridis",
                "plasma",
                "inferno",
                "magma",
                "cividis",
                "Blues",
                "YlGnBu",
                "bone",
                "afmhot",
                "gist_heat",
                "PuOr",
                "RdBu",
                "Spectral",
                "coolwarm",
                "berlin",
                "managua",
                "vanimo",
                "twilight",
                "twilight_shifted",
                "hsv",
                "cubehelix",
                "CMRmap",
                "rainbow",
                "turbo"
            ]
        }
    ],
    "ISFVSN": "2"
}*/
/*
contributor: nan
description: |
    Computes the luminance of the specified linear RGB color using the luminance coefficients from Rec. 709.
    Note, ThreeJS seems to inject this in all their shaders. Which could lead to issues
use: luminance(<vec3|vec4> color)
license:
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Prosperity License - https://prosperitylicense.com/versions/3.0.0
    - Copyright (c) 2021 Patricio Gonzalez Vivo under Patron License - https://lygia.xyz/license
*/
#define FNC_LUMINANCE 
float luminance(in vec3 linear) { return dot(linear, vec3(0.21250175, 0.71537574, 0.07212251)); }
float luminance(in vec4 linear) { return luminance( linear.rgb ); }
/*
contributors: Inigo Quiles
description: cubic polynomial https://iquilezles.org/articles/smoothsteps/
use: <float|vec2|vec3|vec4> cubic(<float|vec2|vec3|vec4> value[, <float> in, <float> out]);
examples:
    - https://raw.githubusercontent.com/patriciogonzalezvivo/lygia_examples/main/math_functions.frag
*/
#define FNC_CUBIC 
float cubic(const in float v) { return v*v*(3.0-2.0*v); }
vec2 cubic(const in vec2 v) { return v*v*(3.0-2.0*v); }
vec3 cubic(const in vec3 v) { return v*v*(3.0-2.0*v); }
vec4 cubic(const in vec4 v) { return v*v*(3.0-2.0*v); }
float cubic(const in float v, in float slope0, in float slope1) {
    float a = slope0 + slope1 - 2.;
    float b = -2. * slope0 - slope1 + 3.;
    float c = slope0;
    float v2 = v * v;
    float v3 = v * v2;
    return a * v3 + b * v2 + c * v;
}
vec2 cubic(const in vec2 v, in float slope0, in float slope1) {
    float a = slope0 + slope1 - 2.;
    float b = -2. * slope0 - slope1 + 3.;
    float c = slope0;
    vec2 v2 = v * v;
    vec2 v3 = v * v2;
    return a * v3 + b * v2 + c * v;
}
vec3 cubic(const in vec3 v, in float slope0, in float slope1) {
    float a = slope0 + slope1 - 2.;
    float b = -2. * slope0 - slope1 + 3.;
    float c = slope0;
    vec3 v2 = v * v;
    vec3 v3 = v * v2;
    return a * v3 + b * v2 + c * v;
}
vec4 cubic(const in vec4 v, in float slope0, in float slope1) {
    float a = slope0 + slope1 - 2.;
    float b = -2. * slope0 - slope1 + 3.;
    float c = slope0;
    vec4 v2 = v * v;
    vec4 v3 = v * v2;
    return a * v3 + b * v2 + c * v;
}
// Perceptually uniform __________________________________________
vec3 viridis(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.279996+t*(-0.134457+t*(2.122880+t*(-14.447920+t*(24.707924+t*-11.544445))))),
                      (0.010267+t*(1.338810+t*-0.424115)),
                      (0.305867+t*(2.558000+t*(-11.757977+t*(28.337753+t*(-32.838760+t*13.495362)))))), 0.0, 1.0);
}
vec3 plasma(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.057526+t*(2.058166+t*-1.141244)),
                      (-0.183275+t*(0.668964+t*0.479353)),
                      (0.525210+t*(1.351117+t*(-4.013494+t*2.284066)))), 0.0, 1.0);
}
vec3 inferno(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.015449+t*(0.816640+t*(3.399179+t*(-4.796465+t*1.530683)))),
                      (0.000619+t*(0.450682+t*(-1.556978+t*(3.904984+t*-1.764423)))),
                      (0.019123+t*(0.792737+t*(29.365333+t*(-210.608893+t*(622.120191+t*(-942.393021+t*(711.115854+t*-209.780428)))))))), 0.0, 1.0);
}
vec3 magma(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.023114+t*(0.883412+t*(2.280390+t*-2.164009))),
                      (-0.000931+t*(0.700294+t*(-3.639731+t*(14.399222+t*(-28.183967+t*(29.245012+t*-11.549071)))))),
                      (0.011971+t*(1.223232+t*(17.782054+t*(-111.294284+t*(282.340184+t*(-384.394777+t*(275.310307+t*-80.251736)))))))), 0.0, 1.0);
}
vec3 cividis(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.102167+t*(1.770817+t*(-1.648560+t*1.012380))),
                      (0.141992+t*(0.611236+t*0.144193)),
                      (0.322998+t*(1.722691+t*(-9.616038+t*(23.625510+t*(-25.027807+t*9.186618)))))), 0.0, 1.0);
}
// Sequential ____________________________________________________
vec3 Blues(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.943647+t*(-0.158191+t*(-2.714486+t*1.964217))),
                      (0.982284+t*(-0.392108+t*-0.418141)),
                      (1.030646+t*(-0.565756+t*(0.777663+t*-0.805330)))), 0.0, 1.0);
}
vec3 YlGnBu(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((1.016659+t*(-1.339936+t*(11.159728+t*(-66.134399+t*(133.543361+t*(-111.090359+t*32.864311)))))),
                      (1.092797+t*(-1.304413+t*(3.654078+t*(-7.031950+t*3.709013)))),
                      (0.872075+t*(-2.388615+t*(11.004279+t*(-20.407306+t*(16.944816+t*-5.679140)))))), 0.0, 1.0);
}
vec3 bone(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.011603+t*(1.066867+t*(-0.673604+t*0.623930))),
                      (0.018446+t*(0.524946+t*(1.185768+t*-0.732596))),
                      (0.004421+t*(1.254048+t*-0.275214))), 0.0, 1.0);
}
vec3 afmhot(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.000000+t*2.000000),
                      (-0.500000+t*2.000000),
                      (-1.000000+t*2.000000)), 0.0, 1.0);
}
vec3 gist_heat(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.000000+t*1.500000),
                      (-1.000000+t*2.000000),
                      (-3.000000+t*4.000000)), 0.0, 1.0);
}
// Diverging _____________________________________________________
vec3 PuOr(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.485986+t*(2.398392+t*(-1.584602+t*(-4.182565+t*3.070981)))),
                      (0.220676+t*(1.906193+t*(-13.800529+t*(103.351963+t*(-299.537970+t*(404.048990+t*(-266.347566+t*70.168210))))))),
                      (0.024838+t*(0.493868+t*(-9.557527+t*(33.521793+t*(212.783673+t*(-1058.728164+t*(1734.993580+t*(-1254.634328+t*341.401231))))))))), 0.0, 1.0);
}
vec3 RdBu(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.400723+t*(3.300744+t*(-2.935353+t*(-36.033738+t*(179.085501+t*(-359.201137+t*(319.560658+t*-104.166834))))))),
                      (0.029676+t*(-0.868288+t*(21.075603+t*(-48.522827+t*(39.917539+t*-11.429958))))),
                      (0.132201+t*(-0.606812+t*(22.258641+t*(-181.823725+t*(865.301855+t*(-2125.533421+t*(2717.103974+t*(-1733.825035+t*437.366994))))))))), 0.0, 1.0);
}
vec3 Spectral(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.598068+t*(3.431552+t*(-14.311445+t*(38.401818+t*(-53.239303+t*25.528697))))),
                      (-0.043709+t*(5.026604+t*(-38.474027+t*(213.061446+t*(-565.825422+t*(759.245726+t*(-506.842541+t*134.164400))))))),
                      (0.255686+t*(0.766670+t*(-2.127582+t*(6.353670+t*(-500.828959+t*(4409.519238+t*(-15659.320403+t*(28703.901573+t*(-28853.036957+t*(15161.909141+t*-3266.779653))))))))))), 0.0, 1.0);
}
vec3 coolwarm(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.225442+t*(1.146729+t*(1.200318+t*-1.892762))),
                      (0.315180+t*(1.157008+t*(4.283163+t*(-14.248873+t*(12.650405+t*-4.100613))))),
                      (0.720390+t*(2.313282+t*(-5.313037+t*2.424071)))), 0.0, 1.0);
}
vec3 berlin(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.630240+t*(-2.929230+t*(4.578328+t*(-3.491291+t*(5.297388+t*-3.104980))))),
                      (0.679282+t*(0.580242+t*(-10.625822+t*(17.900086+t*-7.866113)))),
                      (0.984720+t*(-0.252832+t*(-10.113057+t*(17.319873+t*-7.251983))))), 0.0, 1.0);
}
vec3 managua(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.984774+t*(-0.830482+t*(-1.358196+t*(-2.756390+t*(10.654418+t*-6.215052))))),
                      (0.822505+t*(-2.747429+t*(11.290731+t*(-51.828996+t*(114.971916+t*(-108.247989+t*36.660789)))))),
                      (0.412518+t*(-1.301498+t*(9.988984+t*(-54.168950+t*(131.803259+t*(-133.952881+t*48.270959))))))), 0.0, 1.0);
}
vec3 vanimo(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((1.076541+t*(-5.021458+t*(46.547306+t*(-258.120340+t*(656.546725+t*(-830.671854+t*(516.796354+t*-126.401696))))))),
                      (0.818465+t*(-3.846036+t*(13.183497+t*(-47.267003+t*(99.476683+t*(-93.181553+t*31.821216)))))),
                      (0.992319+t*(-2.527714+t*(10.402945+t*(-62.417875+t*(150.898725+t*(-151.735532+t*55.072110))))))), 0.0, 1.0);
}
// Cyclic ________________________________________________________
vec3 twilight(float t) {
    t = mod(t, 1.0);
    return clamp(vec3((0.897036+t*(-0.500906+t*(-32.924592+t*(39.298855+t*(1589.934161+t*(-10217.887694+t*(28758.002857+t*(-44189.416403+t*(38446.974582+t*(-17814.940348+t*3421.441781)))))))))),
                      (0.841983+t*(-0.113871+t*(-9.856402+t*(44.124719+t*(-264.651443+t*(971.953633+t*(-1863.347584+t*(1936.416945+t*(-1042.467545+t*227.948308))))))))),
                      (0.881555+t*(0.363768+t*(-26.854212+t*(119.942491+t*(728.943407+t*(-7512.839396+t*(25277.143665+t*(-43548.113954+t*(41353.616683+t*(-20609.435413+t*4217.245736))))))))))), 0.0, 1.0);
}
vec3 twilight_shifted(float t) {
    t = mod(t, 1.0);
    return clamp(vec3((0.200993+t*(0.292593+t*(22.446815+t*(-123.058691+t*(-483.491625+t*(5967.533229+t*(-20641.552934+t*(35802.041789+t*(-34002.974523+t*(16908.406704+t*-3449.658474)))))))))),
                      (0.053422+t*(-0.214573+t*(10.552073+t*(29.668454+t*(-514.853074+t*(2510.517871+t*(-6504.005148+t*(9621.147630+t*(-8127.837012+t*(3654.737017+t*-679.703387)))))))))),
                      (0.209803+t*(0.831414+t*(35.374212+t*(-72.687831+t*(-1594.210916+t*(11211.936434+t*(-33072.655328+t*(52618.418478+t*(-47096.550505+t*(22352.074259+t*-4382.518906))))))))))), 0.0, 1.0);
}
vec3 hsv(float t){
 vec3 rgb = clamp(abs(mod(t*6.0+vec3(0.0,4.0,2.0),6.0)-3.0)-1.0,0.0,1.0);
 return cubic(rgb);
}
// Miscellaneous _________________________________________________
vec3 cubehelix(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.013249+t*(2.275258+t*(-8.817343+t*(-53.364075+t*(404.975951+t*(-860.459961+t*(757.174851+t*-240.797852))))))),
                      (-0.005678+t*(0.984536+t*(-8.239465+t*(106.331710+t*(-417.363212+t*(713.067352+t*(-558.604911+t*164.851278))))))),
                      (0.031276+t*(-0.925911+t*(49.839030+t*(-323.015906+t*(847.188116+t*(-1048.463499+t*(606.532787+t*-130.108084)))))))), 0.0, 1.0);
}
vec3 CMRmap(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((-0.046034+t*(5.251972+t*(-112.270083+t*(1425.319733+t*(-9740.004846+t*(38734.298829+t*(-92945.535254+t*(136150.806610+t*(-119060.868530+t*(57107.235196+t*-11563.214938)))))))))),
                      (-0.014721+t*(2.075516+t*(-10.392436+t*(34.248094+t*(-91.997635+t*(175.584287+t*(-168.209540+t*59.761442))))))),
                      (-0.013214+t*(5.202008+t*(-27.327672+t*(281.810344+t*(-1607.776614+t*(4297.110208+t*(-5653.411710+t*(2848.115909+t*(1213.864752+t*(-1993.222861+t*636.661490))))))))))), 0.0, 1.0);
}
vec3 rainbow(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.521926+t*(-5.081361+t*(90.667146+t*(-1024.570176+t*(5466.954668+t*(-15142.356204+t*(22823.994490+t*(-17834.657381+t*5669.456533)))))))),
                      (0.001238+t*(3.087993+t*(0.533349+t*(-7.242684+t*3.621342)))),
                      (1.040332+t*(-0.285029+t*-0.775501))), 0.0, 1.0);
}
vec3 turbo(float t) {
    t = clamp(t, 0.0, 1.0);
    return clamp(vec3((0.192919+t*(1.618437+t*(-39.426098+t*(737.420549+t*(-6489.216487+t*(28921.755478+t*(-72384.553891+t*(107076.097978+t*(-93276.212113+t*(44337.286143+t*-8884.508085)))))))))),
                      (0.101988+t*(1.859131+t*(7.108520+t*(-20.179546+t*11.147684)))),
                      (0.253316+t*(4.858570+t*(55.191710+t*(-803.379980+t*(4477.461997+t*(-14496.039745+t*(28438.311669+t*(-32796.884355+t*(20328.068712+t*-5210.826342)))))))))), 0.0, 1.0);
}
void main()
{
    vec4 inputColor = IMG_THIS_PIXEL(inputImage);
    float t = luminance(inputColor);
    vec3 color;
    // Perceptually uniform
         if (id == 0) color = viridis(t);
    else if (id == 1) color = plasma(t);
    else if (id == 2) color = inferno(t);
    else if (id == 3) color = magma(t);
    else if (id == 4) color = cividis(t);
    // Sequential
    else if (id == 5) color = Blues(t);
    else if (id == 6) color = YlGnBu(t);
    else if (id == 7) color = bone(t);
    else if (id == 8) color = afmhot(t);
    else if (id == 9) color = gist_heat(t);
    // Diverging
    else if (id == 10) color = PuOr(t);
    else if (id == 11) color = RdBu(t);
    else if (id == 12) color = Spectral(t);
    else if (id == 13) color = coolwarm(t);
    else if (id == 14) color = berlin(t);
    else if (id == 15) color = managua(t);
    else if (id == 16) color = vanimo(t);
    // Cyclic
    else if (id == 17) color = twilight(t);
    else if (id == 18) color = twilight_shifted(t);
    else if (id == 19) color = hsv(t);
    // Miscellaneous
    else if (id == 20) color = cubehelix(t);
    else if (id == 21) color = CMRmap(t);
    else if (id == 22) color = rainbow(t);
    else if (id == 23) color = turbo(t);
    // Gray
    else color = vec3(clamp(t, 0.0, 1.0));
    gl_FragColor = vec4(color, inputColor.a);
}
'''


class IsfColormap(GlslEffect):
    name = 'isf_colormap'
    label = 'Colormap'
    description = 'Selection of colormaps, converted from <https://www.shadertoy.com/view/Nd3fR2>'
    dialect = 'isf'
    GLSL = GLSL
    stage = 'colour'
    params = effect_params({
        "id": {
            "type": "integer",
            "enum": [
                0,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
                11,
                12,
                13,
                14,
                15,
                16,
                17,
                18,
                19,
                20,
                21,
                22,
                23
            ],
            "default": 0,
            "x-ui": {
                "label": "Colormap",
                "widget": "select",
                "labels": [
                    "viridis",
                    "plasma",
                    "inferno",
                    "magma",
                    "cividis",
                    "Blues",
                    "YlGnBu",
                    "bone",
                    "afmhot",
                    "gist_heat",
                    "PuOr",
                    "RdBu",
                    "Spectral",
                    "coolwarm",
                    "berlin",
                    "managua",
                    "vanimo",
                    "twilight",
                    "twilight_shifted",
                    "hsv",
                    "cubehelix",
                    "CMRmap",
                    "rainbow",
                    "turbo"
                ]
            },
            "x-tier": "structural",
            "x-glsl": {
                "u": "id"
            }
        }
    })
