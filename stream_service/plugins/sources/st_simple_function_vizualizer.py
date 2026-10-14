"""Simple function vizualizer: License CC0: Simple function vizualizer I had the need to visualize some functions so created a simple visualizer Thought perhaps someone else could find use...

Source: https://www.shadertoy.com/view/ftlSzr
Author: mrange
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // License CC0: Simple function vizualizer
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// -----------------------------------------------------------------------------
// License CC0: Simple function vizualizer
//  I had the need to visualize some functions so created a simple visualizer
//  Thought perhaps someone else could find use for it. I am sure examples
//  already exists on shadertoy but I didn't find them
// -----------------------------------------------------------------------------

#define ZOOM 0.5
#define TIME 1.0*iTime

// f - the function that is visualized
float f(float x) {
  return sin(x)*sin(x*sqrt(0.5));
}

// -----------------------------------------------------------------------------

// From: http://mercury.sexy/hg_sdf/
float mod1(inout float p, float size) {
  float halfsize = size*0.5;
  float c = floor((p + halfsize)/size);
  p = mod(p + halfsize, size) - halfsize;
  return c;
}

// From: https://iquilezles.org/www/articles/distfunctions2d/distfunctions2d.htm
float segment(vec2 p, vec2 a, vec2 b) {
  vec2 pa = p-a, ba = b-a;
  float h = clamp( dot(pa,ba)/dot(ba,ba), 0.0, 1.0 );
  return length( pa - ba*h );
}

vec2 df(vec2 p, float z, float aa) {
  p /= z;
  vec2 gp = p;
  float m = aa*2.0;
  float n = mod1(p.x, m);
  float d = 1E3;
  
  const int reps = 3;

  for (int i = -reps; i <= reps; ++i) {
    float x0 = (n + float(i))*m + TIME*0.5;
    float x1 = (n + float(i + 1))*m + TIME*0.5;
  
    float y0 = 0.5*f(2.0*x0);
    float y1 = 0.5*f(2.0*x1);
    
    float dd = segment(p, vec2(-m*0.5 + m * float(i), y0), vec2(m*0.5 + m * float(i), y1))-2.0*aa/z;
    
    d = min(d, dd);
  }
  
  gp.x += TIME*0.5;
  mod1(gp.x, 0.5);
  mod1(gp.y, 0.5);
  float dgrid = min(abs(gp.x), abs(gp.y))-aa/z;
  return vec2(d, dgrid)*z;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
  vec2 q = fragCoord/iResolution.xy;
  vec2 p = -1. + 2. * q;
  p.x *= iResolution.x/iResolution.y;
  float aa = 2.0/iResolution.y;
  
  vec2 d = df(p, ZOOM, aa);
 
  vec3 col = mix(vec3(0.05), vec3(0.1), q.y);
  col = mix(col, vec3(0.5, 0.75, 1.0), smoothstep(-aa, aa, -d.y));
  col = mix(col, vec3(1.0), smoothstep(-aa, aa, -d.x));
  
  col = pow(col, vec3(1.0/2.0));
  
  fragColor = vec4(col, 1.0);
}

'''


class StSimpleFunctionVizualizer(GlslSource):
    name = 'st_simple_function_vizualizer'
    label = 'Simple function vizualizer'
    description = 'License CC0: Simple function vizualizer I had the need to visualize some functions so created a simple visualizer Thought perhaps someone else could find use...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
