"""Exodus: Source for Aberration Creations production - 4KB intro "Exodus", published at MAGFest Demoparty 2019. 3rd place in combined demo competition. Code: Angelo...

Source: https://www.shadertoy.com/view/sscGz4
Author: unjello
License: CC0-1.0
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: /// License: CC0
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''/// Exodus / Aberration Creations, a 4k intro
/// 3rd place @ MAGFest 2019
/// License: CC0
///
/// Effects inspired by:
/// Octahedral Voxel Tracing / fizzer: https://www.shadertoy.com/view/4lcfDB
/// Swirly Strands / Plento: https://www.shadertoy.com/view/MtKfWy
/// InFX.1 / patu: https://www.shadertoy.com/view/llSSRm
///
/// Soundtrack: https://soundcloud.com/argasek/exodus-video
/// Video: www.youtube.com/watch?v=HJA1xIevGl0
///


float MIN_DIST = 0.0;
float MAX_DIST = 120.0;
float EPSILON = 0.0001;
vec3 K_a = vec3(1.);
vec3 K_d = vec3(.6);
vec3 K_s = vec3(0.5, 1.0, 0.5);
vec3 lp = vec3(0.0, 1.0, -0.5);
vec3 zero3 = vec3(0.);

int MAX_STEPS = 80;
int MODE_CROSS_CENTER = 1;
int MODE_CROSS_JUMPING = 2;
int MODE_METABALLS_CENTER = 3;
int MODE_SWIRLS_CENTER = 5;
int MODE_SWIRLS_SIDE = 6;

// random took from
// https://thebookofshaders.com/11/
float random (in vec2 st) {
    return fract(sin(dot(st.xy, vec2(12.9898,78.233))) * 43758.5453123);
}

float noise (vec2 st) {
    vec2 i = floor(st);
    vec2 f = fract(st);

    // Four corners in 2D of a tile
    float a = random(i);
    float b = random(i + vec2(1.0, 0.0));
    float c = random(i + vec2(0.0, 1.0));
    float d = random(i + vec2(1.0, 1.0));

    // Smooth Interpolation

    // Cubic Hermine Curve.  Same as SmoothStep()
    vec2 u = f*f*(3.0-2.0*f);
    // u = smoothstep(0.,1.,f);

    // Mix 4 coorners percentages
    return 0.4*(mix(a, b, u.x) +
            (c - a)* u.y * (1.0 - u.x) +
            (d - b) * u.x * u.y);
}

float sdfSphere(vec3 p, float r) {
    return length(p) - r;
}

// http://mercury.sexy/hg_sdf/
float sdfCubeCheap(vec3 p, vec3 size) {
    vec3 d = abs(p) - size;
    return max(d.x, max(d.y, d.z));
}

float sdfOpUnion(float a, float b) {
	return min(a,b);
}

vec3 sdfOpMod(vec3 p, vec3 size) {
    vec3 halfsize = size * 0.5;
    p = mod(p + halfsize, size) - halfsize;
    return p;
}

vec3 opTwist( vec3 p, float r ) {
    float  c = cos(r * p.y + r);
    float  s = sin(r * p.y + r);
    mat2   m = mat2(c,-s,s,c);
    return vec3(m*p.xz,p.y);
}

float opBlob(float d1, float d2, float d3, float d4, float d5, float d6) {
    float k = 2.0;
	return -log(exp(-k*d1)+exp(-k*d2)+exp(-k*d3)+exp(-k*d4)+exp(-k*d5)+exp(-k*d6))/k;
}


// https://www1.udel.edu/biology/rosewc/kaap427627/notes/matrices_rotations.pdf
mat3 fullRotate(vec3 theta) {
    float sx=sin(theta.x);
    float cx=cos(theta.x);
    float sy=sin(theta.y);
    float cy=cos(theta.y);
    float sz=sin(theta.z);
    float cz=cos(theta.z);
    return mat3(
        vec3(cy*cz, -cy*sz, sy),
        vec3(sx*sy*cz+cx*sz, -sx*sy*sz+cx*cz, -sx*cy),
        vec3(-cx*sy*cz+sx*sz, cx*sy*sz+sx*cz, cx*cy)
        );
}

float sdf_metaballs(vec3 p) {
    float t = iTime / 4.;
	float p1 = sdfSphere(0.5*(p + vec3(cos(t*0.5),sin(t*0.3),cos(t))), 1.+0.5*cos(t*6.0));
    float p2 = sdfSphere(2.0*(p + 3.0 * vec3(cos(t*1.1),cos(t*1.3),cos(t*1.7))), 3.+2.*sin(t))/2.0;
    float p3 = sdfSphere(2.0*(p + 5.0 * vec3(cos(t*0.7),cos(t*1.9),cos(t*2.3))), 3.)/2.0;
    float p4 = sdfSphere(2.0*(p + 3.0 * vec3(cos(t*0.3),cos(t*2.9),sin(t*1.1))), 3.+2.*sin(t))/2.0;
    float p5 = sdfSphere(2.0*(p + 6.0 * vec3(sin(t*1.3),sin(t*1.7),sin(t*0.7))), 3.0+1.5*cos(t))/2.0;
    float p6 = sdfSphere(2.0*(p + 3.0 * vec3(sin(t*2.3),sin(t*1.9),sin(t*2.9))), 3.0)/2.0;
    return opBlob(p1, p2, p3, p4, p5, p6);
}

float sdf_swirls(vec3 p, int mode) {
   p -= vec3(1.0, -0.25, 4.0);   
   p *= fullRotate(vec3(
       0.0,
       0.0, 
       mode == MODE_SWIRLS_CENTER ? p.z*0.06+0.2*sin(iTime) : p.z*.06+iTime*0.25
   ));
   p.y += sin(p.z + iTime + p.x*1.0)*0.2;
   p.x += cos(p.y - p.z * 2.0 + iTime)*0.3;
   p = sdfOpMod(p, vec3(1.5, 1.5, 0.5+0.3*sin(iTime)));
   
   return sdfCubeCheap(p, vec3(0.033, 0.033, 2.0));
}

float sdfCross(vec3 p, float w ) {
  float da = sdfCubeCheap(p.xyz,vec3(20., w, w));
  float db = sdfCubeCheap(p.yzx,vec3(w, 20., w));
  float dc = sdfCubeCheap(p.zxy,vec3(w, w , 20.));
  return sdfOpUnion(sdfOpUnion(sdfOpUnion(db,dc), da), da);
} 

float sdf_cross(vec3 p) {
    float t = iTime / 4.;
    float w = 1.7 - length(p) / 10.;
   p = opTwist(p, 0.1*sin(iTime*0.02))*fullRotate(vec3(iTime*0.01, 0.0, iTime*0.02));
   p *= fullRotate(vec3(sin(iTime*0.1), 0.0, cos(iTime*0.02)));
   float res = sdfOpUnion(
       				sdfCross(p, w),
       				sdfCross(p * fullRotate(vec3(3.14/4.0, 0.0, 3.14/4.0)), w));
   res = sdfOpUnion(res, sdfCross(p * fullRotate(vec3(3.14, 3.14/4.0, 3.14)), w));
   return res;
}


vec2 render_raymarch(vec3 eye, vec3 dir, int mode) {
    float dist = MIN_DIST;
    float glow = 0.0;
    float minDist = MAX_DIST;
    
    for (int i = 0; i < MAX_STEPS; ++i) {
        vec3 v = eye + dist * dir;
        float step = 0.0;
        if (mode == MODE_METABALLS_CENTER) {
            step = sdf_metaballs(v);
        }
        if (mode == MODE_CROSS_CENTER || mode == MODE_CROSS_JUMPING) {
        	step = sdf_cross(v);
        }
        if (mode == MODE_SWIRLS_CENTER || mode == MODE_SWIRLS_SIDE) {
			step = sdf_swirls(v, mode);
        }
        
        if (abs(step) < EPSILON) {
        	return vec2(dist, glow);
        }

        dist += step;
                
        minDist = min(minDist, step * 4.);
        glow = pow( 1. / minDist, 0.4);  

        if (dist >= MAX_DIST) {
            return vec2(dist, glow);
        }
    }
	return vec2(dist, glow);
}


vec3 rayDirection(float fieldOfView, vec2 size, vec2 fragCoord) {
    vec2 xy = fragCoord - size / 2.0;
    float z = size.y / tan(radians(fieldOfView) / 2.0);
    return normalize(vec3(xy, -z));
}

mat4 viewMatrix(vec3 eye, vec3 center, vec3 up) {
    // Based on gluLookAt man page
    vec3 f = normalize(center - eye);
    vec3 s = normalize(cross(f, up));
    vec3 u = cross(s, f);
    return mat4(
        vec4(s, 0.0),
        vec4(u, 0.0),
        vec4(-f, 0.0),
        vec4(0.0, 0.0, 0.0, 1)
    );
}

// http://learnwebgl.brown37.net/09_lights/lights_attenuation.html
vec3 getSunLightColor(vec3 eye, vec3 dir, vec3 p, vec3 lp) {
    vec3 sun_pos = eye;
	vec3 L = sun_pos - p;
    float d = max(length(L), EPSILON);
    float atten = 1.0 / (1.0 + d*0.2 + d*d*0.1); 
    vec3 c = (K_a + K_d + K_s)*atten;
	return c;
}

vec3 getFoggyColor(vec3 eye, float d, vec3 dir, vec3 lightPosition) {
	    vec3 p = eye + d * dir;
        vec3 c = getSunLightColor(eye, dir, p, lightPosition);
        
        float fog = smoothstep(0.0, 0.68, d*0.005);
        return mix(c, zero3, fog);
}   

vec4 effect_swirls(vec2 fragCoord, int mode) {
        vec2 uv = vec2(fragCoord.xy - 0.5*iResolution.xy)/iResolution.y; 
        vec3 eye = vec3(mode == MODE_SWIRLS_CENTER ? 0.0 : 0.0, 0.0, (mode == MODE_SWIRLS_CENTER ? -17.0 : 2.0)*iTime); 
    	vec3 viewDir = rayDirection(mode == MODE_SWIRLS_CENTER ? 45.0 : 25.0, iResolution.xy, mode == MODE_SWIRLS_CENTER ? fragCoord : fragCoord.yx);//normalize(vec3(uv,2.0));

        float d = render_raymarch(eye, viewDir, mode).x;

        if (d >= MAX_DIST) {
            return vec4(0.0);
        } else {
            return vec4(getFoggyColor(eye, d, viewDir, lp), 1.0);
        }
}

vec4 effect_raymarch(vec2 fragCoord, int mode) {
    float k = (iTime+150.)/ 2.0;
    vec3 eye = vec3(
        mode == MODE_METABALLS_CENTER ? 30. : sin(k) * 40.,
        1. , 
        mode == MODE_METABALLS_CENTER ? -5.+sin(k) :cos(k) * -20.);
    vec3 viewDir = rayDirection(45.0, iResolution.xy, fragCoord);
	vec3 tt = vec3(10.,
                   mode == MODE_CROSS_CENTER ? 0. : 20.
                   , 0.);

    if (mode == MODE_METABALLS_CENTER) {
        tt.x/=2.;
        tt.y = 2.5+sin(k);
    }
  	
    vec2 uv = fragCoord.xy / iResolution.xy - 1.0;
    vec3 cc = vec3(1.0);
    if (mode == MODE_CROSS_CENTER) {
        uv.y += noise(uv)*sin(k*noise(uv*cos(k)));
	    uv.x -= sin(k*noise(uv*sin(k)));
        float n = (ceil(uv.x * uv.y));

        if (abs(n) < EPSILON) {
        tt.y += 2.0 * sin(iTime);
        cc = vec3(0.65);
        }
    } else {
        float n, n2, n3;
        float div = mode == MODE_CROSS_JUMPING ? 1. : -1.;
        n = (ceil(uv.x*2.5 + div*uv.y*2.5 + div*2.0 - div*sin(k+noise(uv))));
        n2 = (ceil(uv.x*2.5 + div*uv.y*2.5 +  2.0*sin(k)));
        n3 = (ceil(uv.x*2.5 + div*uv.y*2.5 + div*2.0 - div*sin(k)*cos(k)));
		
        vec3 cc = vec3(1.0);
  
        if (abs(n) < EPSILON) {
            tt.y += 2.0 * sin(iTime);
        }
        if (abs(n2) < EPSILON) {
            tt.y += 3.0 * cos(iTime);
        }
        if (abs(n3) < EPSILON) {
            tt.y += 4.0 * cos(iTime);
        }
        cc = vec3(0.65);
    }
    
    
    vec3 up = vec3(0.2, 0.2, -1.);
    if (mode == MODE_CROSS_JUMPING) {
        up.z = -50.*cos(k);
    } else if (mode == MODE_CROSS_CENTER) {
        up.y = sin(k*5.);
        up.z = cos(k*5.);
    }
    
    mat4 viewToWorld = viewMatrix(eye, tt, up);
    vec3 worldDir = (viewToWorld * vec4(viewDir, 0.0)).xyz;
    
    vec2 dd = render_raymarch(eye, worldDir, mode);
    float d = dd.x;
    float glow = dd.y;

    vec3 c = zero3;
    if (d >= MAX_DIST) {
        float g = glow*glow;
        c += K_s*glow*0.2 + K_d*g;
    } else {
        c = getFoggyColor(eye, d, worldDir, mode == 4 ? vec3(0.0, -10., -15.) : lp);
    }
    return vec4(c*cc, 1.0);
}


vec4 intro(vec2 fragCoord) {
    if (iTime <= 4.7) {
        return effect_raymarch(fragCoord, MODE_METABALLS_CENTER);
    } else if (iTime <= 9.6) {
        return effect_swirls(fragCoord, MODE_SWIRLS_SIDE);
    } else if (iTime <= 16.1) {
		return effect_raymarch(fragCoord, MODE_CROSS_JUMPING);
    } else if (iTime <= 19.1) {
        return effect_swirls(fragCoord, MODE_SWIRLS_SIDE);
    } else if (iTime <= 25.7) {
        return effect_raymarch(fragCoord, MODE_METABALLS_CENTER);
    } else if (iTime <= 28.7) {
        return effect_raymarch(fragCoord, MODE_CROSS_JUMPING);
    } else if (iTime <= 33.7) {
		return effect_swirls(fragCoord, MODE_SWIRLS_SIDE);
    } else if (iTime <= 38.3) {
        return effect_raymarch(fragCoord, MODE_CROSS_JUMPING);
    } else if (iTime <= 43.1) {
        return effect_raymarch(fragCoord, MODE_METABALLS_CENTER);
    } else if (iTime <= 47.9) {
        return effect_swirls(fragCoord, MODE_SWIRLS_SIDE);
    } else if (iTime <= 57.7) {
        return effect_raymarch(fragCoord, MODE_METABALLS_CENTER);
    } else if (iTime <= 76.8) {
        return  effect_raymarch(fragCoord, MODE_CROSS_CENTER);
    } else {
        return  effect_swirls(fragCoord, MODE_SWIRLS_CENTER);
    }
}


void mainImage( out vec4 fragColor, in vec2 fragCoord ) {

    vec2 uv = (fragCoord.xy - iResolution.xy)/ iResolution.xy;
    fragColor = intro(fragCoord);
    // Vignette
    fragColor.rgb *= 1. - (pow(abs(uv.x), 5.) + pow(abs(uv.y), 5.)) * .4;    
    // Tonemapping
    fragColor.rgb /= (fragColor.rgb + vec3(.5)) * .7;
    // Gamma
    fragColor.rgb = pow(fragColor.rgb, vec3(1. / 2.2));

    if (iTime > 94.0) {
        fragColor /= (-93.9+iTime)*6.;
    }

}
'''


class StExodus(GlslSource):
    name = 'st_exodus'
    label = 'Exodus'
    description = 'Source for Aberration Creations production - 4KB intro "Exodus", published at MAGFest Demoparty 2019. 3rd place in combined demo competition. Code: Angelo...'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.5)
