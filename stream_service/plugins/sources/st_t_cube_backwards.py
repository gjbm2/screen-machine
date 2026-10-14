"""T Cube backwards: Raymarching the T-Cube fractal backwards, aka reverse perspective.

Source: https://www.shadertoy.com/view/MdSSWR
Author: gleurop
License: public-domain
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Public domain
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Public domain

const int   MAX_ITER = 50;
const float MAX_DIST = 3.0;
const float MIN_DIST = 0.001;
	
float dist_box(vec3 p, vec3 b)
{
	return length(max(abs(p) - b, 0.0));
}

vec2 dist(vec3 p) // returns (distance, hue)
{
	p += 1.0;
	float h = 0.0;
	float d = 1.0;
	float s = 1.0;
	for (int i = 0; i < 4; i++)
    {
		p = abs(p - s);
		s *= 0.4;
		float di = dist_box(p, vec3(s));
		if (di < d)
        {
			d = di;
			h = float(i) / 4.0 + 0.4;
		}
	}
	return vec2(d, h);
}

// Backwards marching: raymarch from the view distance to the camera.
vec4 intersect(vec3 ray_origin, vec3 ray_dir)
{
	vec3 p = ray_origin + MAX_DIST*ray_dir; 
    vec2 d = vec2(1);
    float td = 0.0;
	for (int i = 0; i < MAX_ITER; i++)
	{
        if (d.x < MIN_DIST || td > MAX_DIST)
			break;
        
        d = dist(p - td*ray_dir);
        td += d.x;
	}
    
	return vec4(p - td*ray_dir, d.y);
}

vec3 hsv(in float h, in float s, in float v)
{
	return mix(vec3(1.0), clamp((abs(fract(h + vec3(3, 2, 1) / 3.0) * 6.0 - 3.0) - 1.0), 0.0 , 1.0), s) * v;
}

vec3 get_normal(in vec3 p)
{
	vec2 e = vec2(0.0, 0.001);
	
	return normalize(vec3(
		dist(p + e.yxx).x - dist(p - e.yxx).x,
		dist(p + e.xyx).x - dist(p - e.xyx).x,
		dist(p + e.xxy).x - dist(p - e.xxy).x));
}

vec3 get_lighting(in vec3 pos, in vec3 normal, in vec3 light, in vec3 color)
{
	float b = max(0.0, dot(normal, light));
	return b * color;
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	const vec3 up_dir = vec3(0, 1, 0);
	const vec3 cam_eye = vec3(0, 0, 0);
	vec3 cam_pos = vec3(sin(iTime*0.25), 0.0, cos(iTime*0.25))*1.5;
	vec3 cam_dir = normalize(cam_eye - cam_pos);
	vec3 u = normalize(cross(up_dir, cam_dir));
	vec3 v = cross(cam_dir, u);
	vec2 scr_pos = -1.0 + 2.0*fragCoord.xy / iResolution.xy;
	scr_pos.x *= iResolution.x / iResolution.y;
	vec3 ray_dir = normalize(u * scr_pos.x + v * scr_pos.y + cam_dir);
	
	vec4 h = intersect(cam_pos, ray_dir);
    float d = dist(h.xyz).x;
	vec3 material = hsv(h.w, 1.0, 1.0);
	vec3 normal = get_normal(h.xyz);
	vec3 color = get_lighting(h.xyz, normal, ray_dir, material);
	
	fragColor = mix(vec4(color, 1.0), vec4(0), d);
}'''


class StTCubeBackwards(GlslSource):
    name = 'st_t_cube_backwards'
    label = 'T Cube backwards'
    description = 'Raymarching the T-Cube fractal backwards, aka reverse perspective.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
