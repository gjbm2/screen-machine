"""Neural clothes 3d: Neural clothes 3d

Source: https://www.shadertoy.com/view/wtcfz8
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
float smax( float d1, float d2, float k )
{
    float h = max(k-abs(-d1-d2),0.0);
    return max(-d1, d2) + h*h*0.25/k;
	//float h = clamp( 0.5 - 0.5*(d2+d1)/k, 0.0, 1.0 );
	//return mix( d2, -d1, h ) + k*h*(1.0-h);
}

float smin( float d1, float d2, float k )
{
    float h = max(k-abs(d1-d2),0.0);
    return min(d1, d2) - h*h*0.25/k;
	//float h = clamp( 0.5 + 0.5*(d2-d1)/k, 0.0, 1.0 );
	//return mix( d2, d1, h ) - k*h*(1.0-h);
}


float sdBox( vec3 p, vec3 b )
{
  vec3 q = abs(p) - b;
  return length(max(q,0.0)) + min(max(q.x,max(q.y,q.z)),0.0);
}

float scene(vec3 p) {
    //sdf is undefined outside the unit sphere, uncomment to witness the abominations
    if (length(p) > 1.) {
        return length(p)-0.9;
    }
    
    //neural networks can be really compact... when they want to be
   vec4 f0_0=sin(p.y*vec4(-3.043,-.393,.869,-1.995)+p.z*vec4(-.417,-1.409,-1.252,-3.029)+p.x*vec4(1.519,-.251,-2.510,1.232)+vec4(1.795,-5.318,4.642,-3.404));
vec4 f0_1=sin(p.y*vec4(-2.001,1.483,-1.080,.599)+p.z*vec4(-.516,-1.032,.658,2.989)+p.x*vec4(-3.106,1.587,1.558,.450)+vec4(-.764,6.521,-6.044,-2.375));
vec4 f0_2=sin(p.y*vec4(-1.168,3.040,-2.926,.911)+p.z*vec4(1.965,-3.790,1.906,3.849)+p.x*vec4(-1.151,-1.834,-2.789,.576)+vec4(.133,3.795,8.209,1.911));
vec4 f0_3=sin(p.y*vec4(-.366,-3.850,-.871,-2.136)+p.z*vec4(-.420,.204,-2.406,-3.245)+p.x*vec4(2.844,-1.442,-1.793,-3.235)+vec4(-1.792,4.750,6.307,-4.889));
vec4 f1_0=sin(mat4(.894,-.962,-.027,.033,-.170,-.654,-.789,-.188,-.364,.060,.099,-1.014,-.888,.508,.109,.096)*f0_0+
    mat4(-.124,.011,.568,-.203,-.588,-.483,.035,.088,.691,-.578,.003,1.285,-.194,.920,.306,-.004)*f0_1+
    mat4(-.360,.218,.074,.599,-.384,-.103,-.095,.110,-.247,-.342,.125,-.150,.218,.694,.570,1.176)*f0_2+
    mat4(-.186,1.158,.026,1.227,.036,.242,-.164,.161,1.029,.002,.037,-.363,.240,-.032,-.227,-.303)*f0_3+
    vec4(-3.052,.295,.903,-1.355))/1.0+f0_0;
vec4 f1_1=sin(mat4(-.037,.276,-.177,.360,-.419,.864,-.031,.820,-.040,-.088,-.101,.683,-.182,-.132,.269,-1.170)*f0_0+
    mat4(.110,-.151,.084,.196,-.866,-.053,-.199,-.822,.716,-.100,-.653,-.506,.922,-.344,.523,.646)*f0_1+
    mat4(.691,-.329,-.051,-.717,.063,-.422,-.246,-.913,-.351,-.008,-.180,-.335,.301,.372,.102,1.258)*f0_2+
    mat4(-.120,-.512,.398,-.329,-.243,-.466,-.058,-.596,.003,-.091,.517,.862,-.195,.290,.364,.299)*f0_3+
    vec4(-.411,-1.364,-2.518,2.061))/1.0+f0_1;
vec4 f1_2=sin(mat4(.125,-.243,-.454,-.469,-.054,.902,.018,.932,-.086,-.255,-.288,-.741,-.393,.118,.684,1.130)*f0_0+
    mat4(.573,.040,-.062,-.090,-.378,-.138,-.019,.999,.038,-.514,.307,.373,-.153,-1.723,-.179,-1.284)*f0_1+
    mat4(.516,-.259,.594,.794,-.208,.308,.397,1.036,.114,.404,-.330,.490,-.213,-1.270,-.403,-1.953)*f0_2+
    mat4(-.375,.158,.132,.431,-.181,.207,.067,.648,-.030,-.688,-.794,-1.129,.286,-.038,-.193,-.250)*f0_3+
    vec4(-.967,1.241,-1.499,-3.799))/1.0+f0_2;
vec4 f1_3=sin(mat4(.956,.964,-.010,.677,-1.148,-1.695,-.143,.175,.883,-.676,.122,1.453,-1.097,-.661,-.409,-.305)*f0_0+
    mat4(-.034,.760,.320,-.277,-1.062,.051,-.400,-.101,-.348,-.268,.626,.565,1.351,.062,.606,-1.338)*f0_1+
    mat4(-.634,-.408,.240,-.565,-.936,.066,-.050,.037,-.139,.440,-.108,.428,1.533,.297,.595,-1.483)*f0_2+
    mat4(-.588,.185,-.664,-.968,-.619,-1.090,-.044,.075,.760,.803,-.305,-.060,.019,.577,-.098,.056)*f0_3+
    vec4(2.337,-2.361,1.797,1.739))/1.0+f0_3;
vec4 f2_0=sin(mat4(.583,.025,.586,.709,.678,-.301,-.025,.455,-.316,.205,.437,-.708,.163,.226,.115,.037)*f1_0+
    mat4(.342,.263,.145,.387,.144,1.296,-.022,1.120,1.264,-1.575,.125,-.908,.517,-2.593,-1.753,-.810)*f1_1+
    mat4(.107,1.517,.437,.831,-.180,1.313,.588,.141,.244,.925,.311,.233,.482,-2.037,-1.796,-.708)*f1_2+
    mat4(.051,1.455,.833,.713,-.596,1.189,.318,.041,-.798,-.295,-.140,-.252,.236,-.802,-.165,-.151)*f1_3+
    vec4(2.588,-2.844,-1.694,-.162))/1.4+f1_0;
vec4 f2_1=sin(mat4(-.016,.088,-.334,.214,-.118,.743,-1.079,-.433,-.321,-1.362,-.664,.312,-1.385,-.062,.382,.004)*f1_0+
    mat4(-.134,.049,.657,-.248,-.254,.855,1.010,-.181,-.230,.605,-.805,.688,-1.079,-1.055,-.664,.215)*f1_1+
    mat4(-.241,-.151,.345,.927,.185,.120,.035,.013,-.756,.286,.414,-.188,-.630,-.985,-.422,.373)*f1_2+
    mat4(.847,.656,.694,-.151,.295,.316,.747,.171,.070,.098,.232,-.262,-.134,-.430,-.609,.071)*f1_3+
    vec4(-2.479,-1.096,2.595,-1.178))/1.4+f1_1;
vec4 f2_2=sin(mat4(.016,.732,-.014,-.308,.220,-1.114,-.252,-.178,-.692,-.690,.668,-.113,.112,-.209,.235,.464)*f1_0+
    mat4(.525,-.082,.614,.134,1.573,1.424,.137,-1.053,.161,-1.552,-.291,-.483,-4.279,-1.798,.686,1.084)*f1_1+
    mat4(.682,-.386,.089,.095,1.538,.430,-.987,-.427,1.281,.143,.190,-.005,-3.874,-1.463,.932,1.140)*f1_2+
    mat4(2.269,1.057,-.536,-.697,1.265,.597,-.126,-.388,.407,-.375,-.390,.629,-.609,-.812,.472,.102)*f1_3+
    vec4(.441,-3.570,-1.204,-.728))/1.4+f1_2;
vec4 f2_3=sin(mat4(-.538,.394,.429,.127,-.230,.070,.045,.641,.290,.200,-.361,-.181,.631,.963,-.040,.128)*f1_0+
    mat4(-.732,1.136,.296,.123,-.561,.083,.795,1.425,-.865,-.257,.182,.984,2.387,.772,-3.162,-.529)*f1_1+
    mat4(.282,-.102,.470,1.088,-.672,-.800,1.181,.897,-.412,.436,.958,-.449,2.201,.612,-3.065,-.547)*f1_2+
    mat4(-1.150,.335,1.710,-.334,-.419,.575,.850,.502,.892,1.301,.201,.509,.105,.340,-.475,-.104)*f1_3+
    vec4(3.366,-.390,.914,.320))/1.4+f1_3;


float corte1=sdBox(p+vec3(-0.2,0.0,0.0),vec3(0.4,0.2,0.9));



float d=dot(f2_0,vec4(.099,.105,.126,-.160))+
    dot(f2_1,vec4(-.064,-.100,.130,-.133))+
    dot(f2_2,vec4(-.097,.066,-.079,-.136))+
    dot(f2_3,vec4(.094,.050,.135,.042))+
    -0.092;
    
    
    d=smax(corte1,d,0.1);


return d;


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
        float dist = scene(p/vec3(1.0))-0.017;
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


class StNeuralClothes3d(GlslSource):
    name = 'st_neural_clothes_3d'
    label = 'Neural clothes 3d'
    description = 'Neural clothes 3d'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.3)
