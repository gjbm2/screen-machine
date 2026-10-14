"""RYMRCH WKSP   3 D Sphere Shadows: The 3D section of Electric Square's raymarching workshop https://github.com/ajweeks/RaymarchingWorkshop

Source: https://www.shadertoy.com/view/wsS3z3
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
#define SHADOW_FALLOFF 0.1

// -------------------------
// SDF FUNCTION DECLARATIONS
float sdSphere   (vec3 p, float s);
float sdBox      (vec3 p, vec3 b);
float sdPlane    (vec3 p, vec4 n);
float sdCylinder (vec3 p, vec3 c);
float sdTorus    (vec3 p, vec2 t);
float sdRoundBox (vec3 p, vec3 b, float r);

// -------------------------
// SDF UTILITY DECLARATIONS
float opU(float d1, float d2);
float opBlend(float d1, float d2);

// --------------
// NOISE AND RAND
float rand(vec2 co);
float goldNoise(in vec2 co, in float seed);


// --------------------------
// MAIN FUNCTION DECLARATIONS
float sdf(vec3 pos);
vec2 normalizeScreenCoords(vec2 screenCoord);
vec3 getCameraRayDir(vec2 uv, vec3 camPos, vec3 camTarget);
vec3 render(vec3 camPos, vec3 rayDir);
vec3 calcNormal(vec3 pos);

// compute pixel colour
void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
    vec3 camPos = vec3(0.0, 0.0, -1.0);
    vec3 camTarget = vec3(0.0, 0.0, 0.0);
    
    vec2 uv = normalizeScreenCoords(fragCoord);
    vec3 rayDir = getCameraRayDir(uv, camPos, camTarget);
    
    vec3 col = render(camPos, rayDir);
    
    fragColor = vec4(col, 1.0);
}


vec2 normalizeScreenCoords(vec2 screenCoords)
{
    vec2 result = 2.0 * (screenCoords/iResolution.xy - 0.5);
    result.x *= iResolution.x/iResolution.y;
    return result;
}

vec3 getCameraRayDir(vec2 uv, vec3 camPos, vec3 camTarget)
{
    vec3 camForward = normalize(camTarget - camPos);
    vec3 camRight = normalize(cross(vec3(0.0,1.0,0.0), camForward));
    vec3 camUp = normalize(cross(camForward, camRight));
    
    float fPersp = 2.0;
    vec3 vDir = normalize((uv.x * camRight) + (uv.y * camUp) + (camForward * fPersp));
    return vDir;
}

float castRay(vec3 rayOrigin, vec3 rayDir)
{
    float t = 0.0;
    for(int i = 0; i < 64; i++)
    {
        float res = sdf(rayOrigin + rayDir * t);
        if(res < (0.0001*t))
            return t;
        t += res;
    }
    return -1.0;
}

vec3 render(vec3 rayOrigin, vec3 rayDir)
{
    vec3 col;
    float t = castRay(rayOrigin, rayDir);
    
    vec3 L = normalize(vec3(sin(iTime)*1.0, cos(iTime*0.5)+0.5, -0.5));
    
    if (t == -1.0)
    {
        // Skybox color
        col = vec3(0.30, 0.36, 0.60) - (rayDir.y * 0.4);
    }
    else
    {
        vec3 pos = rayOrigin + t*rayDir;
        vec3 N = calcNormal(pos);
        
        vec3 objectSurfaceColor = vec3(0.4, 0.8, 0.1);
        float NDotL = max(dot(N,L), 0.0);
        vec3 LDir = vec3(1.80, 1.27, 0.99) * NDotL;
        vec3 LAmb = vec3(0.03, 0.04, 0.1);
        vec3 diffuse = objectSurfaceColor * (LDir + LAmb);
        
        col = diffuse;
        
        
        // Add shadows
        
        // HARD SHADOWS
        float shadow = 0.0;
        vec3 shadowRayOrigin = pos + N * 0.01;
        vec3 shadowRayDir = L;
        float shadowRayIntersection = castRay(shadowRayOrigin, shadowRayDir);
        if(shadowRayIntersection != -1.0)
            shadow = 1.0;
        
        col = mix(col, col*0.2, shadow);
        
        /* SOFT SHADOWS
        float shadowRayCount = 128.0;        
        float shadow = 0.0;
        for(float s = 0.0; s < shadowRayCount; s++)
        {
            vec3 shadowRayOrigin = pos + N * 0.01;
            float rX = goldNoise(vec2(rayDir.xy), 0.0) * 2.0 - 1.0;
            float rY = goldNoise(vec2(rayDir.xy), 1.0) * 2.0 - 1.0;
            float rZ = goldNoise(vec2(rayDir.xy), 2.0) * 2.0 - 1.0;
            vec3 shadowRayDir = L + SHADOW_FALLOFF * vec3(rX, rY, rZ);
            float shadowRayIntersection = castRay(shadowRayOrigin, shadowRayDir);
            if(shadowRayIntersection != -1.0)
                shadow += 1.0;
        }
        
        col = mix(col, col*0.2, shadow/shadowRayCount);
        */     

        // Gamma Correction
        col = pow(col, vec3(0.4545));
    }
    
    return col;
}

vec3 calcNormal(vec3 pos)
{
    // Center sample
    float c = sdf(pos);
    // Use offset samples to compute gradient / normal
    vec2 eps_zero = vec2(0.001, 0.0);
    return normalize(vec3(sdf(pos + eps_zero.xyy),
        sdf(pos + eps_zero.yxy), sdf(pos + eps_zero.yyx)) - c);
}


// SDF 
float sdf(vec3 pos)
{
    float t = sdSphere(pos - vec3(0.0, 0.0, 10.0), 3.0);
    return t;
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
// --------------------------

// -------------------------
// NOISE and RAND
float rand(vec2 co)
{
    return fract(sin(dot(co.xy, vec2(12.9898, 78.233))) * 43758.5453);    
}

float PHI = 1.61803398874989484820459 * 00000.1; // Golden Ratio   
float PI  = 3.14159265358979323846264 * 00000.1; // PI
float SQ2 = 1.41421356237309504880169 * 10000.0; // Square Root of Two

float goldNoise(in vec2 co, in float seed)
{
    return fract(tan(distance(co*(seed+PHI), vec2(PHI,PI)))*SQ2);
}

// --------------------------
'''


class StRymrchWksp3DSphereShadows(GlslSource):
    name = 'st_rymrch_wksp_3_d_sphere_shadows'
    label = 'RYMRCH WKSP   3 D Sphere Shadows'
    description = "The 3D section of Electric Square's raymarching workshop https://github.com/ajweeks/RaymarchingWorkshop"
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
