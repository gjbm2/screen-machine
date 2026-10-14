"""Lightning Fragment Shader: I am working on a potential fragment only lightning effect... Lots of different lines and experimentation, the possibilities are endless. I would love any...

Source: https://www.shadertoy.com/view/ts2SzR
Author: jjxtra
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: //License CC0 - http://creativecommons.org/publicdomain/zero/1.0/
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''//--------------------------------------------------------------------------------------
//License CC0 - http://creativecommons.org/publicdomain/zero/1.0/
//To the extent possible under law, the author(s) have dedicated all copyright and related and neighboring rights to this software to the public domain worldwide. This software is distributed without any warranty.
//----------------------------------------------------------------------------------------
//^ This means do ANYTHING YOU WANT with this code. Because we are programmers, not lawyers.
// - jjxtra
//
// lines can be used as great building blocks for many things:
// lines, curves, grid, shapes, lightning and other special effects
// mod and sin is cheap on modern GPU
// throw some of these in an fbm style loop and watch things get crazy
//
// I spent an afternoon making all types of lines
// I am trying them out in a fragment only lightning shader
// this is one of my hobby projects at the moment as I research where this can go
// I'm sharing my progress publicly in the hopes of getting feedback
// 
// * hit pause to see the structure
//
// TODO:
// - Make lightning more jagged and twisted
// - Add smaller lightnings shooting out from sides
// - Improve patchwork / foggy areas
// - Option to isolate to a single column or row
// - Optimize the code
// maybe a custom noise texture for distortion and more detail is unavoidable but I am trying to not go that route yet...

#define UVSCALE 8.0

// https://gamedev.stackexchange.com/questions/141916/antialiasing-shader-grid-lines
#define AA(v) abs(fract(v) - 0.5)

// take only values that are very close to 1.0
#define CCD 0.001
#define CC(v) clamp(floor(v + CCD), 0.0, 1.0)

vec2 getpos(vec2 uv)
{
    uv = UVSCALE * (uv / iResolution.xy);
    float aspect = iResolution.x / iResolution.y;
  	vec2 ratio = vec2(aspect, 1.0);
  	return (2.0 * uv - 1.0) * ratio;
}

// http://madebyevan.com/shaders/grid/
#define AAG(v) abs(fract(v - 0.5) - 0.5) / fwidth(v)
float grid(vec2 xy)
{
    vec2 gridLines = AAG(xy);
    return 1.0 - min(gridLines.x, gridLines.y);
}

float checkerboard(vec2 xy)
{
    const float scalar = 0.25;
    vec2 xyMod = mod(xy * scalar, 1.0);
    return float((xyMod.x < 0.5f) ^^ (xyMod.y < 0.5f));
}

// must be >= 2.0, change to modify the spacing between some of the spaced out line calls
#define MODP 3.0

float lightningNoise(vec2 detailPos, vec2 detailPos2, vec2 shapePos)
{
    const float offsetScalar = 0.91; // chaos factor
    const float offsetScalar2 = 0.213; // detail factor
    const float noiseMultiplier = 1.0;
    
    float t = iTime * 4.0;
    float t2 = t * 1.646579370;
    
    // detail #1 noise offset func
    vec2 noiseXY = AA(((detailPos + t)));
    float noiseZ = AA(detailPos.y - t2 + noiseXY.x);
    vec2 offset = vec2(noiseZ, noiseXY.x + noiseXY.y);
    
    // modify shape pos by detail #1 noise offset
    shapePos += (offset * offsetScalar);
    
    // detail #2 noise offset func
    noiseXY = AA(((detailPos2 - t2)));
    noiseZ = AA(detailPos2.y - t2 + noiseXY.y);
    offset = vec2(noiseXY.x + noiseXY.y, noiseZ);
    
    // modify shape pos by detail #2 noise offset
    shapePos += (offset * offsetScalar2);
    
    // shape pos noise func, swap the x and y for horizontal lightning
    noiseZ = AA((shapePos.x) + AA((shapePos.y)));
    
    // diagonal lightning
    //noiseZ = AA((shapePos.x) + ((shapePos.y)));
    
    return noiseZ * noiseMultiplier;
}

// simple fbm test with two positions in a loop that are scaled
float lightningNoiseFBM(vec2 p)
{
    p *= 0.15;

    const float intensity = 10.0;
    const float power = 4.0;
    const float detailScalar = 0.46213;
    const float detail2Scalar = 1.96213;
    const float shapeScalar = 1.06213;
    const float decay = 0.3;
    float amplitude = 0.8;    
    float noise = 0.0;
    vec2 detailPos = p;
    vec2 detailPos2 = p;
    vec2 shapePos = p;
    
    for (int i = 0; i < 5; i++)
    {
        detailPos *= detailScalar;
        detailPos2 *= detail2Scalar;
        shapePos *= shapeScalar;
        
		noise += (lightningNoise(detailPos, detailPos2, shapePos) * amplitude);

        amplitude *= decay;
    }
    
    return min(1.0, intensity * pow(noise, power));
}

vec4 lightningShader(vec2 p)
{
    float noise = lightningNoiseFBM(p);
    vec3 color3 = mix(vec3(0.2, 0.2, 0.8), vec3(1.0, 1.0, 1.0), noise);
    return vec4(color3 * noise, 1.0);
}

void mainImage(out vec4 fragColor, in vec2 fragCoord)
{
	float color = 0.0;
    vec2 p = getpos(fragCoord);
    fragColor = lightningShader(p);
    return;
    
    // line testing functions...
    
	float ax = AA(p.x); // anti alias
	float ay = AA(p.y);
	float sx = sin(p.x); // sin
	float sy = sin(p.y);
    float asx = max(0.0, sx); // sin max of 0
    float asy = max(0.0, sy); // 
    float mx = mod(p.x, MODP); // mod of p
    float my = mod(p.y, MODP);
    float _max = mod(p.x + ay, MODP); // mod of p + aa
    float may = mod(p.y + ax, MODP);
    float msx = mod(p.x + sy, MODP); // mod of p + sin
    float msy = mod(p.y + sx, MODP);
    float mxl = float(mx >= 0.5 && mx < 1.5); // if mod within a range (to spread out lines)
    float myl = float(my >= 0.5 && my < 1.5);
    float maxl = float(_max >= 0.5 && _max < 1.5); // if mod within a range (to spread out lines)
    float mayl = float(may >= 0.5 && may < 1.5);
    float msxl = float(msx >= 0.5 && msx < 1.5); // if mod within a range with sin (to spread out curved lines)
    float msyl = float(msy >= 0.5 && msy < 1.5);
    
    // most of these line functions below can be used inside the lightning noise function
    // in various ways...
    
    //color = checkerboard(vec2(p.x, p.y));
	//color = clamp((sx + sy), 0.0, 1.0); // iso checkerboard
	//color = ceil(min(1.0, (sx + sy))); // iso checkerboard hard
    //color = grid(vec2(p.x, p.y)); // grid lines
    //color = floor(min(1.0, max(ax, ay) + 0.53)); // grid lines without derivatives and division
    //color = ceil(ay * asx * asy);
	//color = ax; // horizontal smoothed line
	//color = ay; // vertical smoothed line
    //color = ay * myl; // horizontal lines spread out
    //color = ax * mxl; // vertical lines spread out
	//color = AA(p.x + p.y); // diagonal top left to bottom right lines
	//color = AA(p.x - p.y); // diagonal bottom left to top right lines
	//color = max(0.0, sin(p.x + p.y)); // more spread out diagonal top left to bottom right lines
	//color = max(0.0, sin(p.x - p.y)); // more spread out diagonal bottom left to top right lines
	//color = CC(abs(sin(p.x + p.y))); // more spread out thin hard diagonal top left to bottom right lines
	//color = CC(abs(sin(p.x - p.y))); // more spread out thin hard diagonal bottom left to top right lines
	//color = AA(ax + p.y); // horizontal jagged line
	//color = AA(ay + p.x); // vertical jagged line
    //color = mayl * AA(ax + p.y); // horizontal jagged line spread out more    
    //color = maxl * AA(ay + p.x); // vertical jagged line spread out more    
	//color = AA(ax + p.x); // thinner vertical line
	//color = AA(ay + p.y); // thinner horizontal line
	//color = AA(p.x + sy); // curved vertical line
	//color = AA(p.y + sx); // curved horizontal line
    //color = msxl * AA(p.x + sy); // curved vertical line spread out
    //color = msyl * AA(p.y + sx); // curved horizontal line spread out
	//color = max(0.0, sin(p.y + sx)); // wavy horizontal line
	//color = max(0.0, sin(p.x + sy)); // wavy vertical line
	//color = CC(sin(p.y + sx)); // wavy thin hard horizontal line
	//color = CC(sin(p.x + sy)); // wavy thin hard vertical line
	//color = CC(abs(sin(p.y + sx))); // thin hard horizontal line
	//color = CC(abs(sin(p.x + sy))); // thin hard vertical line
	//color = CC(abs(sin(p.x + ay))); // thin hard jagged vertical line
	//color = CC(abs(sin(p.y + ax))); // thin hard jagged horizontal line
    
	fragColor = vec4(color, color, color, 1.0);
}
'''


class StLightningFragmentShader(GlslSource):
    name = 'st_lightning_fragment_shader'
    label = 'Lightning Fragment Shader'
    description = 'I am working on a potential fragment only lightning effect... Lots of different lines and experimentation, the possibilities are endless. I would love any...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
