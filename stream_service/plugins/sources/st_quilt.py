"""Quilt: Color a box, tilt the viewing plane by cell, raymarch.

Source: https://www.shadertoy.com/view/ldG3R3
Author: sillsm
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Max Sills 2016 licensed under the MIT license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Max Sills 2016 licensed under the MIT license.
//
// Quaternion rotation around axis, of angle, centered at center.
vec3 qRotate(vec3 axis, float angle, vec3 center, vec3 point)
{
float x = axis.x * sin(angle / 2.);
float y = axis.y * sin(angle / 2.);
float z = axis.z * sin(angle / 2.);
float w = cos(angle / 2.);

mat3 rotation = mat3(1. - 2.*y*y - 2.*z*z, 2.*(x*y+w*z), 2.*(x*z-w*y),
                     2.*(x*y-w*z), 1. - 2.*x*x - 2.*z*z, 2.*(y*z +w*x),
                     2.*(x*z + w*y), 2.*(y*z-w*x),1. - 2.*x*x - 2.*y*y);
     
return center + (rotation * (point-center));
}

// Return <theta, phi, distance>
vec3 udBox( vec3 p, vec3 b )
{
  vec3 newP = qRotate(vec3(0.,1.,0.),iTime,vec3(0.), p);
  float theta = acos(normalize(p).z) - acos(normalize(newP).z);
  float phi   = atan(p.y/p.x) - atan(newP.y/newP.x);
  return vec3(phi,theta, length(max(abs(newP)-b,0.0)));
}

vec3 march(vec3 eye, vec3 ray)				
{
    vec3 box = vec3(0);
    float t = 0.0;
    for (int i = 0; i < 32; ++i)				
    {	
        vec3 p = eye + t* normalize(ray-eye);	
        box = udBox(p, vec3(1,1,1));
        float distance = box.z;
        t += distance * 0.5;					
    }
    return vec3(box.x,box.y,t);							
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = fragCoord.xy / iResolution.xy;	// Scale pixel coords to 0..1 range instead
    uv = uv * 2.0 - 1.0;						// Now to the -1 to +1 range
    uv.x *= iResolution.x / iResolution.y;		// Correct for aspect ratio.
    
    // Image plane is at (0, 0, -1)
    vec3 ray = vec3(uv, -1.0);	
    vec3 eye = vec3(0.0, 0.0, -3);
    
    // Tilt the viewing pyramid a bit based on cell.
    float angle = floor(fract(uv.x)*4.)+floor(fract(uv.y)*4.);
    float angle2= floor(fract(uv.x)*20.)+floor(fract(uv.y)*20.);
    angle = mix(angle,angle2, .1*abs(cos(iTime/10.)));
    ray = qRotate(vec3(1.,0.,0.),angle,vec3(0.), ray);
    eye = qRotate(vec3(1.,0.,0.),angle,vec3(0.), eye);

    vec3 tr     = march(eye, ray);		
    vec2 pos    = tr.xy;
    float t     = tr.z;

    fragColor = (1.0/t)*vec4(cos(pos.x), cos(pos.y), sin(pos.x), 0);
}'''


class StQuilt(GlslSource):
    name = 'st_quilt'
    label = 'Quilt'
    description = 'Color a box, tilt the viewing plane by cell, raymarch.'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.4)
