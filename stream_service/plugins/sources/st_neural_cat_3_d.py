"""Neural cat 3 D: Neural cat 3D

Source: https://www.shadertoy.com/view/3tcBz8
Author: iuryBorgesRodrigues
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//CC0 1.0 Universal https://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, Blackle Mori has waived all copyright and related or neighboring rights to this work.

//like the last few shaders I've published, this is a neural network experiment.
//this time, the neural network reproduces an SDF given the x,y,z coordinates
//using siren networks with skip connections: https://vsitzmann.github.io/siren/

vec3 erot(vec3 p, vec3 ax, float ro) {
    return mix(dot(p,ax)*ax,p,cos(ro))+sin(ro)*cross(ax,p);
}

float scene(vec3 p) {
    //sdf is undefined outside the unit sphere, uncomment to witness the abominations
    if (length(p) > 1.) {
        return length(p)-.8;
    }
    //neural networks can be really compact... when they want to be
    vec4 f0_0=sin(p.y*vec4(1.502,-1.184,3.061,.227)+p.z*vec4(.000,-1.418,-2.788,-1.376)+p.x*vec4(1.857,3.782,2.983,-4.282)+vec4(-3.023,5.317,-5.555,-.046));
vec4 f0_1=sin(p.y*vec4(2.890,-.295,-3.610,2.310)+p.z*vec4(-1.802,-3.118,-2.365,-1.544)+p.x*vec4(1.200,1.345,-2.545,.916)+vec4(7.336,-6.956,-.152,-4.236));
vec4 f0_2=sin(p.y*vec4(-.374,.646,4.128,2.860)+p.z*vec4(-3.812,-.837,-1.259,1.401)+p.x*vec4(.961,-1.831,-1.602,1.766)+vec4(-4.958,3.274,3.618,1.220));
vec4 f0_3=sin(p.y*vec4(-.441,4.009,3.048,2.976)+p.z*vec4(.452,-1.903,-1.973,3.610)+p.x*vec4(.266,.127,-.481,-2.975)+vec4(5.864,2.952,-7.839,-5.747));
vec4 f1_0=sin(mat4(-.193,.063,-.129,-.036,-.214,-.700,.473,.520,.326,.289,-.305,.212,-.405,.545,.024,.094)*f0_0+
    mat4(-.001,-.020,.376,-1.006,-1.017,-.381,.729,.030,.280,.597,.559,-.431,-.148,-.269,-.285,-1.297)*f0_1+
    mat4(-.514,-.293,-.146,.769,.069,.792,-.344,.780,-.035,.001,.435,.196,.166,.468,-.697,.217)*f0_2+
    mat4(.135,.055,-.886,1.651,-.524,.554,-.153,-.358,-.382,-.117,-1.086,-.758,-.064,.213,.620,.459)*f0_3+
    vec4(-2.677,.044,-1.291,-.712))/1.0+f0_0;
vec4 f1_1=sin(mat4(-.587,-.047,-.527,-.206,.110,-.282,-.944,.255,.337,.072,.572,-.138,.530,.121,-.040,-.170)*f0_0+
    mat4(.038,.302,-.095,-.096,-.256,.368,-.923,.219,.279,.281,.028,-.081,-.377,.222,-.281,.247)*f0_1+
    mat4(-.339,.527,-.283,-.211,.070,-.534,-.119,-.447,-.751,-.126,.021,-.325,-.189,.087,.600,-.344)*f0_2+
    mat4(.343,-.563,.197,-.704,-.426,-.587,.103,-.430,.201,.186,-.344,.378,.791,.244,-.308,-.167)*f0_3+
    vec4(-2.308,-2.232,-.857,-2.855))/1.0+f0_1;
vec4 f1_2=sin(mat4(-.319,-.484,-.032,-.113,.307,-.238,.349,.408,.267,-.387,.430,.131,-.386,-.028,-.201,.492)*f0_0+
    mat4(.206,.207,-.351,.047,1.026,-.256,.875,.251,-.142,-.137,-.202,.261,-.515,-.068,-.383,.706)*f0_1+
    mat4(.046,-.215,-.645,-1.272,-.412,.731,-1.112,-.048,.145,.429,-.074,-.132,.425,.570,.752,-.621)*f0_2+
    mat4(-.660,.434,.224,.812,-.433,-.023,-.659,-.517,.150,-.005,-.196,.512,-.079,.054,.111,-.029)*f0_3+
    vec4(-1.488,2.032,-1.831,-.031))/1.0+f0_2;
vec4 f1_3=sin(mat4(-1.361,-1.373,-.623,-.686,-.163,-.643,-.374,.518,.025,-.008,-.122,-.138,-.323,-.177,-.324,.563)*f0_0+
    mat4(.289,.064,-.279,.869,.203,1.391,-.790,-.989,-.231,.192,.288,.186,.071,.078,.159,-1.050)*f0_1+
    mat4(.795,-.065,-.585,-.512,-.397,-.598,.370,.502,-.225,.315,.773,.162,.320,.498,-.613,.157)*f0_2+
    mat4(-.217,-.566,-.783,.548,-.683,-.097,.219,-.207,.617,.949,-.401,-.921,.392,.099,-.594,-.474)*f0_3+
    vec4(-1.889,-.884,-2.029,1.328))/1.0+f0_3;
vec4 f2_0=sin(mat4(.942,.177,-.155,-.237,-.632,1.088,.021,1.177,.060,.129,.499,.204,-.112,.049,.120,-.574)*f1_0+
    mat4(.328,.990,-.708,-.116,.199,-.353,1.515,-.681,-.624,.185,.692,-.817,.808,-.321,-.164,-.386)*f1_1+
    mat4(.368,.169,.637,-.681,.943,-1.384,1.048,-.876,-.390,-.028,1.109,-.901,-.718,.850,.263,-.034)*f1_2+
    mat4(-.200,.228,.077,1.050,-.298,-.563,.017,-.617,.782,.557,-.303,-.310,.085,-.072,-.486,-.531)*f1_3+
    vec4(.064,1.607,3.346,-2.004))/1.4+f1_0;
vec4 f2_1=sin(mat4(-.383,-.092,.159,-.262,-2.544,1.476,.812,.239,.437,.016,.372,.086,.364,-.415,-.396,-.578)*f1_0+
    mat4(.221,.019,-.557,-.060,.107,-.889,.562,-.535,.451,-.446,.459,-.138,-.163,.188,-1.000,.882)*f1_1+
    mat4(.144,-.461,.849,.169,-.172,-.000,.230,.625,.517,.043,.512,-.307,.604,.056,-.140,-.223)*f1_2+
    mat4(.097,.818,-.766,-.152,-.173,.173,-.359,-.351,-.526,.694,-1.364,.040,-.147,-.958,-.190,-.214)*f1_3+
    vec4(-1.796,-.087,-2.996,-2.751))/1.4+f1_1;
vec4 f2_2=sin(mat4(.609,.274,-.368,-.554,-.154,-.247,1.479,.328,-.179,.623,-.109,-.898,-.228,-.399,-.476,-.207)*f1_0+
    mat4(-.213,-.356,-.549,1.001,-.885,.178,.383,-.894,.245,-.311,-1.100,-.177,-.146,-.643,.025,-1.632)*f1_1+
    mat4(-.633,.567,.584,.272,.197,.327,.243,-.242,.212,-.028,-.087,.277,-.064,-.341,-.779,.671)*f1_2+
    mat4(-.588,-.377,.191,1.527,.121,-.120,-.885,-.863,-.025,-.164,.171,.740,.088,-.039,-.150,.035)*f1_3+
    vec4(-3.244,.714,.341,-.007))/1.4+f1_2;
vec4 f2_3=sin(mat4(.127,-.231,-.265,-.454,-.666,-.326,.326,-.894,-.034,.302,.122,-.790,.373,.858,-.778,.274)*f1_0+
    mat4(.689,.367,-.290,-.492,.216,-1.088,.431,-.385,.290,.222,-.536,-.088,.540,.812,.147,.954)*f1_1+
    mat4(1.184,-.418,-.105,.891,.356,.213,.498,.095,.458,.086,.176,.139,.866,-.951,.070,.332)*f1_2+
    mat4(-.112,.110,-.095,1.258,-.428,.318,.187,-.477,.190,-.109,.074,.650,-.026,-.522,-.040,.605)*f1_3+
    vec4(.449,1.689,1.221,.431))/1.4+f1_3;
return dot(f2_0,vec4(.062,-.055,.033,.031))+
    dot(f2_1,vec4(.044,-.060,-.034,-.092))+
    dot(f2_2,vec4(-.103,.072,.046,.019))+
    dot(f2_3,vec4(-.068,.061,.108,-.019))+
    0.265;

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
        float dist = scene(p/vec3(1.0));
        hit = dist*dist < 1e-6;
        p+=dist*cam;
        if (distance(p,init)>5.) break;
    }
    vec3 n = norm(p);
    vec3 r = reflect(cam,n);
    //don't ask how I stumbled on this texture
    vec3 nz = p - erot(p, vec3(1), 2.) + erot(p, vec3(1), 4.);
    float spec = length(sin(r*3.5+sin(nz*120.)*.15)*.4+.6)/sqrt(3.);
    spec *= smoothstep(-.3,.2,scene(p+r*.2));
    vec3 col = vec3(.1,.1,.12)*spec + pow(spec,8.);
    float bgdot = length(sin(cam*8.)*.4+.6)/2.;
    vec3 bg = vec3(.1,.1,.11) * bgdot + pow(bgdot, 10.);
    fragColor.xyz = hit ? col : bg;
    fragColor = smoothstep(-.02,1.05,sqrt(fragColor)) * (1.- dot(uv,uv)*.5);
}'''


class StNeuralCat3D(GlslSource):
    name = 'st_neural_cat_3_d'
    label = 'Neural cat 3 D'
    description = 'Neural cat 3D'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.45)
