"""Plasma Box: Plasma Cube

Source: https://www.shadertoy.com/view/lsKXDW
Author: sillsm
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Copyright Max Sills 2016, licensed under the MIT license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Copyright Max Sills 2016, licensed under the MIT license.
// Plasma cube.
//
// Quaternion rotation around axis, of angle, centered at center.
vec3 qRotate(vec3 axis, float angle, vec3 center, vec3 point)
{
axis = normalize(axis);
float x = axis.x * sin(angle / 2.);
float y = axis.y * sin(angle / 2.);
float z = axis.z * sin(angle / 2.);
float w = cos(angle / 2.);

mat3 rotation = mat3(1. - 2.*y*y - 2.*z*z, 2.*(x*y+w*z), 2.*(x*z-w*y),
                     2.*(x*y-w*z), 1. - 2.*x*x - 2.*z*z, 2.*(y*z +w*x),
                     2.*(x*z + w*y), 2.*(y*z-w*x),1. - 2.*x*x - 2.*y*y);
     
return center + (rotation * (point-center));
}

// Derived from http://www.bidouille.org/prog/plasma
float plasma2 (vec2 uv)
{
    uv *= 2.;
    float v = sin((uv.x+iTime));
    v += sin((uv.y+iTime)/2.0);
    v += sin((uv.x+uv.y+iTime)/2.0);
    vec2 c = uv/2.0 * vec2(sin(iTime/3.0), cos(iTime/2.0));
    v += sin(sqrt(c.x*c.x+c.y*c.y+1.0)+iTime);
    v = v/2.0;
    return v;
}

// Rounded box with a plasma displacement.
float udBox( vec3 p, vec3 b )
{
  p = qRotate(vec3(0,1.,1.),10.,vec3(0.), p);
  float dist = max(length(max(abs(p)-b,0.0)) - 1., -1.*(length(p)));
  return dist - plasma2(5.*p.xy)/6.;
}

vec4 trace(vec3 ray, vec3 viewportxy)				
{
    vec3 p = vec3(0);
    float t = 0.0;
    for (int i = 0; i < 32; ++i)				
    {	
        p = viewportxy + (t* ray);				
        float dist = udBox(p, vec3(1,1,1));
        t += dist * .9;
    }
    return vec4(p,t);							
}

// Compute the camera to world transform.
// Rotates then translates.
mat4 LookAtRH( vec3 eye, vec3 target, vec3 up )
{
    vec3 zaxis = normalize(target-eye);      // The "forward" vector.
    vec3 xaxis = normalize(cross(up, zaxis));// The "right" vector.
    vec3 yaxis = cross(zaxis, xaxis);        // The "up" vector.

    return  mat4(
        vec4(      xaxis,       0 ),
        vec4(      yaxis,       0 ),
        vec4(      zaxis,       0 ),
        vec4(      eye,         1 )
    );
}

void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
	vec2 uv = fragCoord.xy / iResolution.xy;
    uv = uv * 2.0 - 1.0;		
    uv.x *= iResolution.x / iResolution.y;
    vec2 mo = iMouse.xy/iResolution.xy;
    
    float f = 3.; // f-stop.
    vec4 pixel = vec4(uv, 0, 1);
    
    // Rotate the viewer position (near clipping plane center).
    vec3 position = qRotate(vec3(0,1.,0.),iTime/2. + 4.*mo.x,vec3(0.), vec3(0, 5.*mo.y, -5));
    
    mat4 mat = LookAtRH(position, vec3(0,0,0), vec3(0,1,0));
    vec3 pt = (mat*pixel).xyz;
    vec3 ray = normalize(mat*(pixel - vec4(0,0,-f,1))).xyz;
    
    vec4 tr     = trace(ray, pt);
    float t     = tr.w;
    float v = plasma2(5.*tr.xy);
        
    vec3 col = 5.*(1./t)*vec3(1, sin(3.14*v), cos(3.14*v));
    col *= .8; // Gamma correction.
    fragColor = vec4(col, 0);
    if (length(col) < .05) {
        float v2 = plasma2(uv*20.);
        col = 5.*vec3(sin(v2), sin(v2 + iTime), sin(v2+7.));
    }
     fragColor = vec4(col, 0);
}'''


class StPlasmaBox(GlslSource):
    name = 'st_plasma_box'
    label = 'Plasma Box'
    description = 'Plasma Cube'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
