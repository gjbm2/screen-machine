"""Raymarching Workshop   2 D Rings: SDF, simple, copied from https://github.com/ajweeks/RaymarchingWorkshop

Source: https://www.shadertoy.com/view/3s23Ry
Author: trevortheblack
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // MIT license
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// TREVOR BLACK
// LICENSE - There Ain't One
// This is my take on the 2D raymarching from the RaymarchingWorkshop


// Companion shader for Raymarching Workshop run at Electric Square
// Workshop site: https://github.com/ajweeks/RaymarchingWorkshop

// MIT license


#define M_PI 3.1415926535897932384626433832795028841971693993751058209749445923078164062

// -------------------------
// SDF FUNCTION DECLARATIONS
float sdSphere   (vec3 p, float s);
float sdBox      (vec3 p, vec3 b);
float sdPlane    (vec3 p, vec4 n);
float sdCylinder (vec3 p, vec3 c);
float sdTorus    (vec3 p, vec2 t);
float sdRoundBox (vec3 p, vec3 b, float r);
float sdCircle(vec2 p, vec2 pos, float radius);
float sdRing(vec2 p, vec2 pos, vec2 radii);

// -------------------------
// SDF UTILITY DECLARATIONS
float opU(float d1, float d2);
float opBlend(float d1, float d2);


// --------------------------
// MAIN FUNCTION DECLARATIONS
float sdf(vec2 p);
vec2 screenToWorld(vec2 screen);
vec3 palette(in float t, in vec3 a, in vec3 b, in vec3 c, in vec3 d);
vec3 shade(float sd);

// compute pixel colour
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    // project screen coordinate into world
	vec2 p = screenToWorld(fragCoord);
    
    // signed distance for scene
    float sd = sdf(p);
    
    // compute signed distance to a colour
    vec3 col = shade(sd);
    
    fragColor = vec4(col, 1.0);
}


vec2 screenToWorld(vec2 screen)
{
    vec2 result = 2.0 * (screen/iResolution.xy - 0.5);
    result.x *= iResolution.x/iResolution.y;
    return result;
}
vec3 palette(in float t, in vec3 a, in vec3 b, in vec3 c, in vec3 d)
{
    t = clamp(t, 0., 1.);
    return a + b*cos(6.28318*(c*t+d));
}
vec3 shade(float sd)
{
    float maxDist = 2.0;
    vec3 palCol = palette(clamp(0.5-sd*0.4, -maxDist,maxDist), 
                      vec3(0.3,0.3,0.0),vec3(0.8,0.8,0.1),vec3(0.9,0.7,0.0),vec3(0.3,0.9,0.8));

    vec3 col = palCol;
    
    // Darken around surface
	col = mix(col, col*1.0-exp(-10.0*abs(sd)), 0.4);
	// repeating lines
    col *= 0.8 - 0.2*cos(150.0*sd - sign(sd)*M_PI*5.0*iTime);
    // White outline at surface
    col = mix(col, vec3(1.0), 1.0-smoothstep(0.0,0.01,abs(sd)));
    
    return col;
}

// SDF 
float sdf(vec2 p)
{
	float d = 1000.0;
    
    d = opBlend(d, sdRing(p, vec2(-0.15, -0.3), vec2(0.2, 0.55)));
    d = opBlend(d, sdRing(p, vec2(1.6, -0.1), vec2(1.0,1.3)));
    d = opBlend(d, sdRing(p, vec2(1.6, -0.1), vec2(0.4,0.7)));
    d = opBlend(d, sdCircle(p, vec2(1.6, -0.1), 0.1));
    
    //d = opBlend(d, sdCircle(p, vec2(1.6, -0.1), 1.3));
    
    return d;
}



// -------------------------
// SDF FUNCTION DEFINITIONS
// -------------------------
float sdSphere(vec3 p, float s)
{
	return length(p) - s;   
}

float sdBox(vec3 p, vec3 b)
{
    vec3 d = abs(p) - b;
    return length(max(d, 0.0)) + 
        min(max(d.x, max(d.y,d.z)), 0.0);
}

float sdPlane    (vec3 p, vec4 n)
{ // n must be normalized
    return dot(p, n.xyz) + n.w;
}

float sdCylinder (vec3 p, vec3 c)
{
    return length(p.xz-c.xy) - c.z;
}

float sdTorus    (vec3 p, vec2 t)
{
    vec2 q = vec2(length(p.xz) - t.x, p.y);
    return length(q) - t.y;
}

float sdRoundBox (vec3 p, vec3 b, float r)
{
    vec3 d = abs(p) - b;
    return length(max(d, 0.0)) - r +
        min(max(d.x, max(d.y,d.z)), 0.0);                 
}

float sdCircle(vec2 p, vec2 pos, float radius)
{
    return length(p-pos)-radius;
}

float sdRing(vec2 p, vec2 pos, vec2 radii)
{
    vec2 pRingSpace = p - pos;
    float dInner = radii.x - length(pRingSpace);
    float dOuter = length(pRingSpace) - radii.y;
    //return max(dInner, dOuter);
    // smooth out sd in body of the ring
    float dMax = max(dInner, dOuter);
    /*
    if(dMax < 0.0)
    {
        float dCenterBodyRing = length(pRingSpace) - 0.5*(radii.x+radii.y);
        dCenterBodyRing = sqrt(dCenterBodyRing*dCenterBodyRing + 0.001); // 0.1 is an eps
        dCenterBodyRing = dCenterBodyRing - 0.5*(radii.y-radii.x);
        return dCenterBodyRing;   
    }
    */
    return dMax;

    
    
    //float dMax = max(dInner, dOuter);
    //float width = radii.y - radii.x;
    //float dMaxCenterAtOrigin = 
    //return sqrt( (dMax-width)*() + 0.0001f);
    
}
// -------------------------

// -------------------------
// SDF Utility Library

// polynomial smooth min (k = 0.1);
float sminCubic(float a, float b, float k)
{
    float h = max(k-abs(a-b), 0.0);
    return min(a, b) - h*h*h/(6.0*k*k);
}

float opU(float d1, float d2)
{
    return min(d1, d2);
}

float opBlend(float d1, float d2)
{
    float k = 0.2;
    return sminCubic(d1, d2, k);
}
// --------------------------'''


class StRaymarchingWorkshop2DRings(GlslSource):
    name = 'st_raymarching_workshop_2_d_rings'
    label = 'Raymarching Workshop   2 D Rings'
    description = 'SDF, simple, copied from https://github.com/ajweeks/RaymarchingWorkshop'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
