"""Blotting: neon blot filter. Click and drag your mouse vertically to change the color and smile type.

Source: https://www.shadertoy.com/view/ttGBWR
Author: bboettcher3
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Inigo Quilez
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// Signed distance to a 2D rounded X shape. It is exact in the
// exterior, and a bound in the interior.

// List of some other 2D distances:
//
// Circle:               https://www.shadertoy.com/view/3ltSW2
// Segment:              https://www.shadertoy.com/view/3tdSDj
// Triangle:             https://www.shadertoy.com/view/XsXSz4
// Isosceles Triangle:   https://www.shadertoy.com/view/MldcD7
// Regular Triangle:     https://www.shadertoy.com/view/Xl2yDW
// Regular Pentagon:     https://www.shadertoy.com/view/llVyWW
// Regular Octogon:      https://www.shadertoy.com/view/llGfDG
// Rounded Rectangle:    https://www.shadertoy.com/view/4llXD7
// Rhombus:              https://www.shadertoy.com/view/XdXcRB
// Trapezoid:            https://www.shadertoy.com/view/MlycD3
// Polygon:              https://www.shadertoy.com/view/wdBXRW
// Hexagram:             https://www.shadertoy.com/view/tt23RR
// Regular Star:         https://www.shadertoy.com/view/3tSGDy
// Star5:                https://www.shadertoy.com/view/wlcGzB
// Ellipse 1:            https://www.shadertoy.com/view/4sS3zz
// Ellipse 2:            https://www.shadertoy.com/view/4lsXDN
// Quadratic Bezier:     https://www.shadertoy.com/view/MlKcDD
// Uneven Capsule:       https://www.shadertoy.com/view/4lcBWn
// Vesica:               https://www.shadertoy.com/view/XtVfRW
// Cross:                https://www.shadertoy.com/view/XtGfzw
// Pie:                  https://www.shadertoy.com/view/3l23RK
// Arc:                  https://www.shadertoy.com/view/wl23RK
// Horseshoe:            https://www.shadertoy.com/view/WlSGW1
// Parabola:             https://www.shadertoy.com/view/ws3GD7
// Parabola Segment:     https://www.shadertoy.com/view/3lSczz
// Rounded X:            https://www.shadertoy.com/view/3dKSDc
// Joint:                https://www.shadertoy.com/view/WldGWM
// Simple Egg:           https://www.shadertoy.com/view/Wdjfz3
//
// and many more here:   http://www.iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm

float intensity = 2.;
float ra = 0.04;
float wi = .15;
vec2 faceCenter = vec2(0.5, .8);
vec2 eyeOffset = vec2(.15, 0.0);
vec2 mouthOffset = vec2(0.0, -0.5);
float smileLen = .79; /* Arc len */
float smileWidth = 0.05; /* Width */
float smileRa = .6;

float sdRoundedX( in vec2 p, in float w, in float r )
{
    p = abs(p);
    return length(p-min(p.x+p.y,w)*0.5);
}

float sdCircle( in vec2 p, in float r ) 
{
    return abs(length(p)-r);
}

float udSegment( in vec2 p, in vec2 a, in vec2 b )
{
    vec2 ba = b-a;
    vec2 pa = p-a;
    float h =clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
    return length(pa-h*ba);
}

// scb is the sin/cos of the aperture
float sdArc( in vec2 p, in vec2 scb, in float ra, in float rb )
{
    p *= mat2(1.,0.,0.,1.);
    p.x = abs(p.x);
    float k = (scb.y*p.x>scb.x*p.y) ? dot(p.xy,scb) : length(p);
    k *= 2.;
    return sqrt( dot(p,p) + ra * (ra - k) ) - rb;
}

float sdSmile(in vec2 p, in float radius) {
    return abs(sdArc(p,vec2(sin(smileLen),cos(smileLen)), radius, smileWidth));
}

float getGlow(float dist, float radius, float intensity){
    return pow(radius/dist, intensity);
}

/* Returns rgb vec from input 0-1 */
vec3 getRainbowColor(in float val) {
    /*convert to rainbow RGB*/
    float a = (1.0 - val) * 6.0;
    int X = int(floor(a));
    float Y = a - float(X);
    float r = 0.;
    float g = 0.;
    float b = 0.;
    if (X == 0) {
        r = 1.; g = Y; b = 0.;
    } else if (X == 1) {
        r = 1. - Y; g = 1.; b = 0.;
    } else if (X == 2) {
        r = 0.; g = 1.; b = Y;
    } else if (X == 3) {
        r = 0.; g = 1. - Y; b = 1.;
    } else if (X == 4) {
        r = Y; g = 0.; b = 1.;
    } else if (X == 5) {
        r = 1.; g = 0.; b = 1. - Y;
    } else {
        r = 0.; g = 0.; b = 0.;
    }
    return vec3(r, g, b);
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec2 uv = fragCoord/iResolution.xy;
    float widthHeightRatio = iResolution.x/iResolution.y;
    vec2 eye1Center = faceCenter - eyeOffset;
    vec2 eye2Center = faceCenter + eyeOffset;
    vec2 mouthCenter = faceCenter + mouthOffset;
    vec2 eye1Pos = eye1Center - uv;
    vec2 eye2Pos = eye2Center - uv;
    vec2 mouthPos = mouthCenter - uv;
    vec2 smilePos = faceCenter - uv;
    vec2 linePos1 = vec2(0.1, 0.1);
    vec2 linePos2 = vec2(0.1, 0.9);
    eye1Pos.y /= widthHeightRatio;
    eye2Pos.y /= widthHeightRatio;
    mouthPos.y /= widthHeightRatio;
    smilePos.y /= widthHeightRatio;
    //linePos1.y /= widthHeightRatio;
    //linePos2.y /= widthHeightRatio;

    float lineDist = udSegment(uv, linePos1, linePos2);
	float eye1Dist = sdRoundedX( eye1Pos, wi, ra );
    float eye2Dist = sdRoundedX( eye2Pos, wi, ra );
    float mouthDist = sdCircle(mouthPos, .1);
    float smileDist = sdSmile(smilePos, smileRa / widthHeightRatio);
    float glowLine = getGlow(lineDist, ra, intensity);
    float glowEye1 = getGlow(eye1Dist, ra, intensity);
    float glowEye2 = getGlow(eye2Dist, ra, intensity);
    float glowMouth = getGlow(mouthDist, ra, intensity);
    float glowSmile = getGlow(smileDist, ra, intensity);
    
    vec3 col = vec3(0.0);
    float timeNorm = mod(iTime, 5.) / 5.;
    vec3 glowCol = getRainbowColor(timeNorm);
    col += 10.0*vec3(smoothstep(0.006, 0.003, lineDist));
    col += glowLine * glowCol;
    col += 10.0*vec3(smoothstep(0.006, 0.003, eye1Dist));
    col += glowEye1 * glowCol;
    col += 10.0*vec3(smoothstep(0.006, 0.003, eye2Dist));
    col += glowEye2 * glowCol;
    if (timeNorm > 0.5) {
      col += 1.0*vec3(smoothstep(0.006, 0.003, mouthDist));
      col += glowMouth * glowCol;
    } else {
      col += 10.0*vec3(smoothstep(0.006, 0.003, smileDist));
      col += glowSmile * glowCol;
    }
    
    
    /*Tone mapping*/
    col = 1.0 - exp(-col);
    /*Gamma*/
    col = pow(col, vec3(0.6));

	fragColor = vec4(col,1.0);
}'''


class StBlotting(GlslSource):
    name = 'st_blotting'
    label = 'Blotting'
    description = 'neon blot filter. Click and drag your mouse vertically to change the color and smile type.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
