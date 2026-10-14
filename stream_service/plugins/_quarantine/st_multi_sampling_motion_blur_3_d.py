"""Multi sampling Motion blur 3 D: A moving ball in 3D space. Motion blurred by averaging the multi-sampling of past 24 frames.

Source: https://www.shadertoy.com/view/ttcXD2
Author: winlandiano
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2020 Ridge/winlandiano
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

// A moving ball in 3D space. Motion blurred by averaging the multi-sampling of past 24 frames.

#define BACKGROUND vec3(0, 0.17, 0.19)
#define SAMPLE_COUNT 24.
#define DIRECTIONAL_LIGHT_TYPE 51

#define INF 10000000000.0f
#define PI 3.141592653589793

struct Ray {
    vec3 origin;
    vec3 direction;
};

struct Camera {
    vec3 position;
};

struct LightSource {
    vec3 position;
    float intensity;
    vec3 color;
    int type;
};
    
struct Intersection {
    vec4 P;
    vec3 N;
    vec3 V;
};

float SphereIntersect(Ray r, vec3 center, float radius) {
    // geometric method
    vec3 L = center - r.origin;
    float d = length(L);
    float d2 = d * d;
    float cos_theta = dot(normalize(L), r.direction);
    float cos_theta2 = cos_theta * cos_theta;
    if (cos_theta <= 0.) {
        return INF;
    }
    float sin_theta2 = 1. - cos_theta2;
    float r2 = radius * radius;
    float intersect_half2 = r2 - d2 * sin_theta2;
    if (intersect_half2 < 0.) {
        return INF;
    }
    float center_project_dist = d * cos_theta;
    float intersect_half = sqrt(intersect_half2);
    float t0 = center_project_dist - intersect_half;
    if (t0 < 0.) {
        return center_project_dist + intersect_half;
    } else {
        return t0;
    }
}

// ----------------  shading technics  ----------------
vec3 Lambertian(vec3 N, LightSource light) {
    return clamp(light.color * light.intensity * dot(N, -light.position), 0., 1.) + vec3(0.1);
}

// ----------------  shading technics ends  ----------------
vec3 Render(Ray r, float time, LightSource light) {
    vec3 col = vec3(0);

    float angle = PI * cos(time) * 4.;
    vec3 center = vec3(cos(angle), 0, 3. + sin(angle));
    const float radius = 0.5;

    float t_min = SphereIntersect(r, center, radius);

    // only directional light
    if (t_min < INF) {
        vec4 P = vec4(r.origin + r.direction * t_min, 1.);
        vec3 N = normalize(P.xyz - center);
        col += Lambertian(N, light);
    } else {
        col = BACKGROUND;
    }
    return col;
}

Camera camera;

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    // Normalized pixel coordinates (from 0 to 1)
    vec2 uv = (fragCoord - .5 * iResolution.xy) / iResolution.y;

    // Setup scene
    LightSource light = LightSource(normalize(vec3(-1, -1, 1)), 1., vec3(1, 1, 1), DIRECTIONAL_LIGHT_TYPE);
    camera = Camera(vec3(0, 0, 0));

    Ray r = Ray(camera.position, normalize(vec3(uv, 1)));
	vec3 col = vec3(0);
    float dt = iTimeDelta / 10.;
    for (float time = iTime - dt * SAMPLE_COUNT; time <= iTime; time += dt) {
        col += Render(r, time, light);
    }
    col /= SAMPLE_COUNT;

    fragColor = vec4(col, 1.);
}'''


class StMultiSamplingMotionBlur3D(GlslSource):
    name = 'st_multi_sampling_motion_blur_3_d'
    label = 'Multi sampling Motion blur 3 D'
    description = 'A moving ball in 3D space. Motion blurred by averaging the multi-sampling of past 24 frames.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
