"""Raymarcher Template: This is a template I created for my future raymarching projects. You can use it if you want but I would suggest getting familiarized with the raymarching...

Source: https://www.shadertoy.com/view/3styDs
Author: oneshade
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/*
Based on Michael Walczyk's blog on raymarching at https://michaelwalczyk.com/blog-ray-marching.html

The MIT License
Copyright © 2020 Elijah Bevers
Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction, including
without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the
following conditions: The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY
KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR
ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Update information:
* Future updates:


* Added features (in order):
  - Original version
  - Added new SDFs for plane and torus
  - Added a background variable
  - Added individual shape coloring
  - Added structures to allow CSG operators to work alongside coloring
  - Put the surface shading code into a seperate function
  - Fixed plane SDF
  - Added clamping of colors to prevent weird issues
  - Added shadows
  - Simplified plane SDF
  - Added antialiasing
  - Added cone SDF
  - Added soft shadows
  - Added a sky function
  - Added fog
*/

// Post process settings:
vec2 antialiasing = vec2(1.0, 1.0); // Set to 2x2 if your computer is fast enough.

// Light settings:
vec3 lightDirection = vec3(-0.58, 0.58, 0.58);
vec3 lightEmissionColor = vec3(1.0, 1.0, 1.0);
vec3 lightColor = vec3(1.0, 0.9, 0.7);
float lightPower = 2.0;

// Soft shadows:
float shadowSoftness = 5.0;

// Fog:
vec3 fogColor = vec3(0.25, 0.25, 0.6);
float fogginess = 0.0001;

// Camera settings:
vec3 cameraPosition = vec3(0.0, 300.0, 1100.0);
vec3 cameraRight = vec3(1.0, 0.0, 0.0);
vec3 cameraUp = vec3(0.0, 1.0, 0.0);
vec3 cameraForward = vec3(0.0, 0.0, -1.0);
float cameraFocalLength = 400.0;

// User interaction settings:
float tiltSpeed = 3.0;

// Ray marching constants:
const vec3 GRADIENT_STEP = vec3(0.001, 0.0, 0.0);
const float MAX_TRACE_DISTANCE = 2000.0;
const float MIN_HIT_DISTANCE = 0.005;
const float SHADOW_BIAS = MIN_HIT_DISTANCE * 2.0;
const int MAX_STEPS = 500;

// Raymarching structures:
struct Ray {
    vec3 origin;
    vec3 direction;
};

struct Surface {
    vec3 ambientColor;
    vec3 diffuseColor;
    vec3 specularColor;
    float shininess;
    float signedDistance;
};

// Signed Distance Functions (SDFs):
float sdPlane(in vec3 p, in vec3 n) {
    return dot(n, p);
}

// sdCuboid(), sdCone(), and sdCylinder() are taken from Inigo Quilez's 3D distance functions article (https://www.iquilezles.org/www/articles/distfunctions/distfunctions.htm):
float sdCuboid(in vec3 p, in float h, in float w, in float d) {
    vec3 q = abs(p) - 0.5 * vec3(w, h, d);
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);
}

float sdCone(in vec3 p, in float b, in float h) {
    p.y -= h;
    vec2 q = h * vec2(b / h * 2.0, -2.0);
    
    vec2 w = vec2( length(p.xz), p.y );
    vec2 a = w - q * clamp(dot(w, q) / dot(q, q), 0.0, 1.0);
    vec2 c = w - q * vec2(clamp(w.x / q.x, 0.0, 1.0), 1.0);
    float k = sign(q.y);
    float d = min(dot(a, a), dot(c, c));
    float s = max(k * (w.x * q.y - w.y * q.x), k * (w.y - q.y));
    return sqrt(d) * sign(s);
}

float sdCylinder(in vec3 p, in float h, in float r) {
    vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, 0.5 * h);
    return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));
}

float sdTorus(in vec3 p, in float r1, in float r2) {
    return length(vec2(length(p.xz) - r1, p.y)) - r2;
}

float sdSphere(in vec3 p, in float r) {
    return length(p) - r;
}

// Constructive Solid Geometry (CSG) Operators:
Surface Union(in Surface surface1, in Surface surface2) {
    Surface surfaceUnion = surface1;
    if (surface2.signedDistance < surfaceUnion.signedDistance) {
        surfaceUnion = surface2;
    }

    return surfaceUnion;
}

Surface Intersection(in Surface surface1, in Surface surface2) {
    Surface surfaceIntersection = surface1;
    if (surface2.signedDistance > surfaceIntersection.signedDistance) {
        surfaceIntersection = surface2;
    }

    return surfaceIntersection;
}

Surface Difference(in Surface surface1, in Surface surface2) {
    return Intersection(surface1, Surface(surface2.ambientColor,
                                          surface2.diffuseColor,
                                          surface2.specularColor,
                                          surface2.shininess,
                                          -surface2.signedDistance));
}

// Transformations:
vec3 Translate(in vec3 p, in vec3 t) {
    return p - t;
}

vec3 Rotate(in vec3 p, in vec3 r) {
    vec3 rad = radians(-r);
    vec3 cosRad = cos(rad);
    vec3 sinRad = sin(rad);

    mat3 xRotation = mat3(1.0,      0.0,       0.0,
                          0.0, cosRad.x, -sinRad.x,
                          0.0, sinRad.x,  cosRad.x);

    mat3 yRotation = mat3( cosRad.y, 0.0, sinRad.y,
                                0.0, 1.0,      0.0,
                          -sinRad.y, 0.0, cosRad.y);

    mat3 zRotation = mat3(cosRad.z, -sinRad.z, 0.0,
                          sinRad.z,  cosRad.z, 0.0,
                               0.0,       0.0, 1.0);

    return zRotation * yRotation * xRotation * p;
}

vec3 Scale(in vec3 p, in vec3 s) {
    return p / s;
}

// Scene mapping function:
Surface mapScene(in vec3 p) {
    Surface plane = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(mod(dot(floor(p.xz / 100.0), vec2(1.0)), 2.0) * 1.25),
                            vec3(0.0, 0.0, 0.0), 0.0,
                            sdPlane(Translate(p, vec3(0.0, -75.0, 0.0)), vec3(0.0, 1.0, 0.0)));

    Surface cuboid = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(1.0, 0.6, 0.0),
                            vec3(1.0, 0.4, 0.0), 1.0,
                            sdCuboid(Translate(p, vec3(-300.0, -12.5, -300.0)), 125.0, 250.0, 200.0));

    Surface cone = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(1.0, 1.0, 0.0),
                            vec3(1.0, 1.0, 1.0), 8.0,
                            sdCone(Translate(p, vec3(0.0, 25.0, -300.0)), 50.0, 100.0));

    Surface cylinder = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(0.0, 1.0, 0.0),
                            vec3(0.0, 0.0, 0.0), 0.0,
                            sdCylinder(Translate(p, vec3(300.0, 0.0, -300.0)), 150.0, 50.0));

    Surface torus = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(0.0, 0.0, 1.0),
                            vec3(1.0, 1.0, 1.0), 8.0,
                            sdTorus(Translate(p, vec3(-150.0, -35.0, 100.0)), 100.0, 40.0));

    Surface sphere = Surface(vec3(0.0, 0.0, 0.0),
                            vec3(1.0, 0.0, 0.0),
                            vec3(1.0, 1.0, 1.0), 8.0,
                            sdSphere(Translate(p, vec3(150.0, 0.0, 100.0)), 75.0));

    return Union(plane, Union(cuboid, Union(cone, Union(cylinder, Union(torus, sphere)))));
}

// Normal calculation function (using gradient):
vec3 calculateNormal(in vec3 p) {
    float gradientX = mapScene(p + GRADIENT_STEP.xyy).signedDistance - mapScene(p - GRADIENT_STEP.xyy).signedDistance;
    float gradientY = mapScene(p + GRADIENT_STEP.yxy).signedDistance - mapScene(p - GRADIENT_STEP.yxy).signedDistance;
    float gradientZ = mapScene(p + GRADIENT_STEP.yyx).signedDistance - mapScene(p - GRADIENT_STEP.yyx).signedDistance;
    return normalize(vec3(gradientX, gradientY, gradientZ));
}

// Sky mapping function:
vec3 mapSky(in Ray ray) {
    return mix(vec3(1.0), vec3(0.25, 0.25, 1.0), 0.5 + 0.5 * ray.direction.y);
}

// Fog function:
vec3 applyFog(in vec3 shade, in Ray ray, in float rayDistance) {
    float lightAmount = max(0.0, dot(ray.direction, lightDirection));
    vec3 fog = mix(fogColor, lightColor, pow(lightAmount, lightPower));
    float fogAmount = 1.0 - exp(-rayDistance * fogginess);
    return mix(shade, fog, fogAmount);
}

// Shadow mapping function:
float mapShadow(in vec3 p, in vec3 normal, in float softness) {
    float softShadow = 1.0;
    p += normal * SHADOW_BIAS;
    float shadowDistance = 0.0;
    for (int iterations=0; iterations < MAX_STEPS; iterations++) {
        vec3 shadowTestPosition = p + lightDirection * shadowDistance;
        Surface occluderSurface = mapScene(shadowTestPosition);
        if (abs(occluderSurface.signedDistance) < MIN_HIT_DISTANCE) {
            return 0.0;
        }

        if (shadowDistance > MAX_TRACE_DISTANCE) {
            break;
        }

        shadowDistance += occluderSurface.signedDistance;
        softShadow = min(softShadow, softness * occluderSurface.signedDistance / shadowDistance);
    }

    return softShadow;
}

// Surface shader (uses the Phong illumination model):
vec3 shadeSurface(in Surface surface, in vec3 hitPosition, in Ray ray, in vec3 normal) {
    vec3 illuminationAmbient = surface.ambientColor * lightEmissionColor;

    float lambertian = max(0.0, dot(normal, lightDirection));
    vec3 illuminationDiffuse = lambertian * surface.diffuseColor * lightEmissionColor;

    vec3 reflection = reflect(lightDirection, normal);
    float specularAngle = max(0.0, dot(reflection, ray.direction));
    vec3 illuminationSpecular = clamp(pow(specularAngle, surface.shininess), 0.0, 1.0) * surface.specularColor * lightEmissionColor;

    return illuminationAmbient + (illuminationDiffuse + illuminationSpecular) * mapShadow(hitPosition, normal, shadowSoftness);
}

// Raymarching loop:
vec4 rayMarch(in Ray ray) {
    float distanceTraveled = 0.0;
    for (int iterations=0; iterations < MAX_STEPS; iterations++) {
        vec3 currentPosition = ray.origin + ray.direction * distanceTraveled;
        Surface sceneSurface = mapScene(currentPosition);
        if (abs(sceneSurface.signedDistance) < MIN_HIT_DISTANCE) {
            vec3 normal = calculateNormal(currentPosition);
            vec3 color = shadeSurface(sceneSurface, currentPosition, ray, normal);
            color = applyFog(color, ray, distanceTraveled);
            return vec4(color, 1.0);
        }

        if (distanceTraveled > MAX_TRACE_DISTANCE) {
            break;
        }

        distanceTraveled += sceneSurface.signedDistance;
    }

    return vec4(mapSky(ray), 1.0);
}

// Pixel shader output function:
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 screenCenter = 0.5 * iResolution.xy;
    fragColor = vec4(0.0, 0.0, 0.0, 1.0);

    cameraPosition = Rotate(cameraPosition, vec3(0.0, -iTime * 20.0, 0.0));
    float tilt = iMouse.y - screenCenter.y;
    if (iMouse.z > 0.0) cameraPosition.y += tilt * tiltSpeed;

    cameraForward = -normalize(cameraPosition);
    cameraRight = normalize(vec3(-cameraForward.z, 0.0, cameraForward.x));
    cameraUp = cross(cameraRight, cameraForward);

    for (float yOffset=0.0; yOffset < antialiasing.y; yOffset++) {
        for (float xOffset=0.0; xOffset < antialiasing.x; xOffset++) {
            vec2 pixel = fragCoord + vec2(xOffset, yOffset) / antialiasing - screenCenter;
            vec3 rayOrigin = cameraPosition + cameraForward * cameraFocalLength;
            vec3 rayDirection = normalize(rayOrigin - (cameraPosition - cameraRight * pixel.x - cameraUp * pixel.y));
            fragColor += rayMarch(Ray(rayOrigin, rayDirection));
        }
    }

    fragColor /= antialiasing.y * antialiasing.x;
}'''


class StRaymarcherTemplate(GlslSource):
    name = 'st_raymarcher_template'
    label = 'Raymarcher Template'
    description = 'This is a template I created for my future raymarching projects. You can use it if you want but I would suggest getting familiarized with the raymarching...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
