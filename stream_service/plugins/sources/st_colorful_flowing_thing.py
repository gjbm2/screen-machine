"""Colorful Flowing Thing: It's a colorful flowing thing.

Source: https://www.shadertoy.com/view/lsySD1
Author: sillsm
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Copyright Max Sills 2016, licensed under the MIT license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Copyright Max Sills 2016, licensed under the MIT license.
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

// Rounded box minus a sphere with oscilating radius.
float udBox( vec3 p, vec3 b )
{
  p = qRotate(vec3(0,1.,1.),10.,vec3(0.), p);
  float dist = max(length(max(abs(p)-b,0.0)) - 1., -1.*(length(p)));
  dist += 5.*sin(iTime+ p.x)*cos(p.y);
    return dist;
}


vec4 trace(vec3 ray, vec3 viewportxy)				
{
    vec3 p = vec3(0);
    float t = 0.0;
    for (int i = 0; i < 64; ++i)				
    {	
        //   the amount t must be to get to it
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
	vec2 uv = fragCoord.xy / iResolution.xy;	// Scale pixel coords to 0..1 range instead
    uv = uv * 2.0 - 1.0;						// Now to the -1 to +1 range
    uv.x *= iResolution.x / iResolution.y;
    
    vec3 eye = vec3(0, 0, -7);
    vec3 screen = vec3(0,0,-5);
    vec4 pixel = vec4(uv, 0, 1);
    
    eye = qRotate(vec3(0,1.,0.),iTime,vec3(0.), eye);
    vec3 newScreen = qRotate(vec3(0,1.,0.),iTime,vec3(0.), screen);
    
    mat4 mat = LookAtRH(newScreen, vec3(0), vec3(0,1,0));
    vec3 pt = (mat*pixel).xyz;
    vec3 ray = normalize(pt - eye);
    
    vec4 tr     = trace(ray, pt);
    vec3 ref    = vec3(0.,0.,1.);
    float t     = tr.w;

    vec3 x = dFdx(tr.xyz);
    vec3 y = dFdy(tr.xyz);
    vec3 normal = normalize(cross(x,y));
    
    normal = abs(normal);

    vec3 col = 5.*(1./t)*vec3(normal);
    col *= .8; // Gamma correction.

    fragColor = vec4(col, 0);
}'''


class StColorfulFlowingThing(GlslSource):
    name = 'st_colorful_flowing_thing'
    label = 'Colorful Flowing Thing'
    description = "It's a colorful flowing thing."
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
