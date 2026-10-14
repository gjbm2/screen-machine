"""Plasma tree: Plasma tree

Source: https://www.shadertoy.com/view/MtdGzf
Author: sillsm
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // Copyright Max Sills 2016, licensed under the MIT license.
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// Copyright Max Sills 2016, licensed under the MIT license.
// Plasma tree
#define PI 3.14159265
#define MIRROR
//#define DEBUG
struct Datum
{
  vec3 location;
  float distance;
  float material;
};

mat4 Ry (float angle)
{
    float c = cos(angle);
    float s = sin(angle);
    
return  mat4(
        vec4(c, 0, -s, 0),
        vec4(0, 1, 0, 0),
        vec4(s, 0, c, 0),
        vec4(0, 0, 0, 1)
); 
}

mat4 Rz (float angle)
{
    float c = cos(angle);
    float s = sin(angle);
    
return  mat4(
        vec4(c, s, 0, 0),
        vec4(-s, c, 0, 0),
        vec4(0, 0, 1, 0),
        vec4(0, 0, 0, 1)
); 
}

mat4 Disp (vec3 displacement)
{
return  mat4(
        vec4(1, 0, 0, 0),
        vec4(0, 1, 0, 0),
        vec4(0, 0, 1, 0),
        vec4(displacement, 1)
); 
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

vec2 opU( float d1, float d2, float m1, float m2 )
{
    return (d1<d2) ? vec2(d1, m1) : vec2(d2, m2);
}

float sdCappedCylinder( vec3 p, vec2 h )
{
  vec2 d = abs(vec2(length(p.xz),p.y)) - h;
  return min(max(d.x,d.y),0.0) + length(max(d,0.0));
}

float sdPlane( vec3 p )
{ 
	return p.y;
}

// Rounded box with a plasma displacement.
float pudBox( vec3 p, vec3 b )
{
  //p = qRotate(vec3(0,1.,1.),10.,vec3(0.), p);
  float dist = max(length(max(abs(p)-b,0.0)) - 1., -1.*(length(p)));
  return dist - plasma2(5.*p.xy)/6.;
}


mat4 branch(float a_y, float a_z, float height, float b_len) 
{
    mat4 dd = Disp(vec3(b_len*sin(a_z),-height + b_len*cos(a_z),0));
    return Rz(a_z) * dd * Ry(a_y);
}

float tree(vec3 p)
{
  vec4 hom = vec4(p, 1); //homogenous coordinates.

  float b1_a = 2.0;
  float phi = iTime; // How much you want to rotate branch
    
  // First branch
  float replication = clamp((iTime + 2.)/2., 3., 7.);
  mat4 b1_f = branch(PI/replication - PI, b1_a, 3., 9.);
    
  // Second branch
  float r2 = clamp((iTime+12.)/2., 3., 5.);;
  float b2_a = 1.1;
  float b2_size = clamp(iTime+1.,1.,5.);
  float c = clamp(iTime+2.,1.,5.);
  mat4 b2_f = branch(PI/r2 - PI, b2_a, -6., b2_size);   
    
  // Reflection symmetry around trunk for first branch.
  hom = Ry(-phi) * hom;
  float theta = atan(hom.z, hom.x);//(goes from -pi to pi)
  float t = theta;
  t = mod(t, 2.*PI);
  #ifdef MIRROR
  t = mod(t, 2.*PI/replication);
  #endif
  float r = length(hom.xz);
  vec3 new_p = vec3(r*cos(t), hom.y, r*sin(t));
    
  // Debug Code
  #ifdef DEBUG
  if ((t > 2.*PI/replication - .1 && t < 2.*PI/replication + .1) ||
      t < .1 && t > -.1) return 15.;
  #endif   
    
  // Reflection symmetry around first branch for second branch.
  hom = Ry(-phi)* b1_f * vec4(new_p, 1);
  theta = atan(hom.z, hom.x);//(goes from -pi to pi)
  t = theta;
  t = mod(t, 2.*PI);
  #ifdef MIRROR
  t = mod(t, 2.*PI/r2);
  #endif
  r = length(hom.xz);
  vec3 new_p2 = vec3(r*cos(t), hom.y, r*sin(t));
     
  // Apply branch coordinate system to symmetry coordinates.  
  vec4 b1_p = b1_f * vec4(new_p,1);
  vec4 b2_p = b2_f * vec4(new_p2,1);
  vec4 b3_p = Disp(vec3(0.,b2_size-2.,0)) * b2_p;
  
  // Place a trunk, two branches, and abox.
  float trunk = sdCappedCylinder(p, vec2(3., 6));
  float b1 = sdCappedCylinder(b1_p.xyz, vec2(.5, 6));
  float b2 = sdCappedCylinder(b2_p.xyz, vec2(.25, b2_size));
  float b3 = pudBox(b3_p.xyz,vec3(.5,.5,.5));
    
  return min(b3,min(b2, min(b1, trunk)));    
}

// Returns distance_to_shape, material index
vec2 scene(vec3 ray)  
{
    float planeMat = .4;
    float boxMat   = .1;
    return opU(tree(ray), sdPlane(ray), boxMat, planeMat);
}

Datum trace(vec3 ray, vec3 viewportxy)				
{
    const float hitThreshold = 0.001;
    vec3 p = vec3(0);
    float t = 0.0;
    float m = 0.0;
    for (int i = 0; i < 100; ++i)				
    {	
        p = viewportxy + (t* ray);
        vec2 data = scene(p);
        float dist = data.x;
        m  = data.y;
        t += dist;// * .9;
        
        if ( dist < hitThreshold ){
            break;
        }
    }
    return Datum(p, t, m);						
}

// iq normal
vec3 calcNormal( in vec3 pos )
{
	vec3 eps = vec3( 0.001, 0.0, 0.0 );
	vec3 nor = vec3(
	    scene(pos+eps.xyy).x - scene(pos-eps.xyy).x,
	    scene(pos+eps.yxy).x - scene(pos-eps.yxy).x,
	    scene(pos+eps.yyx).x - scene(pos-eps.yyx).x );
	return normalize(nor);
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

// Stolen from nmz
float f(in vec2 p)
{
    return sin(p.x+sin(p.y+iTime*0.1)) * sin(p.y*p.x*0.1+iTime*0.2);
}

// Stolen from nmz
vec2 field(in vec2 p)
{
	vec2 ep = vec2(.05,0.);
    vec2 rz= vec2(0);
	for( int i=0; i<7; i++ )
	{
		float t0 = f(p);
		float t1 = f(p + ep.xy);
		float t2 = f(p + ep.yx);
        vec2 g = vec2((t1-t0), (t2-t0))/ep.xx;
		vec2 t = vec2(-g.y,g.x);
        
        p += .9*t + g*0.3;
        rz= t;
	}
    
    return rz;
}


// Stolen from ming's tutorial.
vec3 shade( vec3 pos, vec3 nrm, vec4 light )
{
	vec3 toLight = light.xyz - pos;
	
	float toLightLen = length( toLight );
	toLight = normalize( toLight );
	
	float comb = 0.1;
	//float vis = shadow( pos, toLight, 0.01, toLightLen );
	float vis = 1.;//shadowSoft( pos, toLight, 0.0625, toLightLen, 8.0 );
	
	if ( vis > 0.0 )
	{
		float diff = 2.0 * max( 0.0, dot( nrm, toLight ) );
		float attn = 1.0 - pow( min( 1.0, toLightLen / light.w ), 2.0 );
		comb += diff * attn * vis;
	}
	
	return vec3( comb, comb, comb );
}

// Given distance to shape, material index, world coord, and pixel, color.
vec4 color (float t, float m, vec3 p, vec2 uv)
{
    float v = plasma2(5.*p.xy);
    t = clamp(t, -5., 5.);
    vec3 col = 5.*(1./t)*vec3(1, sin(3.14*v), cos(3.14*v));
    col *= .8; // Gamma correction.

    if (length(p) > 100.) {
        float v2 = plasma2(uv*20.);
        col = .5*vec3(sin(v2), sin(v2 + iTime), sin(v2+7.)); 
        return vec4(col,0);
    }
    if (abs(m -.4) < .001) {
        vec2 fld = field(p.xz*.10);
        col = sin(vec3(-.3,0.1,0.5)+fld.x-fld.y)*0.65+0.35;
    }
    vec3 nrm = calcNormal(p);
    vec4 light1 = vec4(20., 20., -10.0, 40.0 );
    vec3 cc  = shade(p,nrm,light1);
    //return vec4(cc,0);

    return cc.x*vec4(col, 0);
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
    vec3 position = vec3(0,0,-40.);
    position += vec3(20.*cos(iTime/5.), 30., -60.*abs(sin(iTime/5.)));
    position += vec3(iMouse.x/10.,0,iMouse.y/10.);
    
    #ifdef DEBUG
    position = vec3(0,40,1);
    #endif
    
    mat4 mat = LookAtRH(position, vec3(0,0,0), vec3(0,1,0));
    vec3 pt = (mat*pixel).xyz;
    vec3 ray = normalize(mat*(pixel - vec4(0,0,-f,1))).xyz;
    
    Datum d     = trace(ray, pt);
    fragColor   = color(d.distance, d.material, d.location, uv); 
}'''


class StPlasmaTree(GlslSource):
    name = 'st_plasma_tree'
    label = 'Plasma tree'
    description = 'Plasma tree'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.3)
