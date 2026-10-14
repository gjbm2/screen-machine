"""Colorful metaballs: This is my first post at shadertoy! Thank you for watching!

Source: https://www.shadertoy.com/view/3dlSDn
Author: butadiene
License: MIT
license_class: permissive
Imported verbatim from GLSL (Shadertoy); runs through stream_service.plugins._lib.glsl (GLSL -> CUDA).
Licence line in the shader: // The MIT License
"""
from stream_service.plugins._lib.glsl import GlslSource, scene_params


GLSL = r'''// The MIT License
// Copyright © 2019 Butadiene
// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.


			const float  _ypos =-0.25;


			// The MIT License
			// Copyright © 2013 Inigo Quilez
			// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
			//Making noise
			float hash(vec2 p)  
			{
				p  = 50.0*fract( p*0.3183099 + vec2(0.71,0.113));
				return -1.0+2.0*fract( p.x*p.y*(p.x+p.y) );
			}

			float noise( in vec2 p )
			{
				vec2 i = floor( p );
				vec2 f = fract( p );
	
				vec2 u = f*f*(3.0-2.0*f);

				return mix( mix( hash( i + vec2(0.0,0.0) ), 
								 hash( i + vec2(1.0,0.0) ), u.x),
							mix( hash( i + vec2(0.0,1.0) ), 
								 hash( i + vec2(1.0,1.0) ), u.x), u.y);
			}			
			///////////////////////////////////////////////////////////////////////
											
			float smoothMin(float d1,float d2,float k)
			{
				return -log(exp(-k*d1)+exp(-k*d2))/k;
			}
						
			// Base distance function
			float ball(vec3 p,float s)
			{
				return length(p)-s;
			}

			
			// Making ball status
			vec4 metaballvalue(int i)
			{
				float ifloat = float(i);
                float kt = 3.*iTime*(0.1+0.01*ifloat);
				vec3 ballpos = 0.3*vec3(noise(vec2(ifloat,ifloat)+kt),noise(vec2(ifloat+10.,ifloat*20.)+kt),noise(vec2(ifloat*20.,ifloat+20.)+kt));
				float scale = 0.05+0.02*hash(vec2(ifloat,ifloat));
				return  vec4(ballpos,scale);
			}
			// Making ball distance function
			float metaballone(vec3 p, int i)
			{	
				vec4 value = metaballvalue(i);
				vec3 ballpos = p-value.xyz;
				float scale =value.w;
				return  ball(ballpos,scale);
			}

			//Making metaballs distance function
			float metaball(vec3 p)
			{
				float d1;
				float d2 =  metaballone(p,0);
				for (int i = 1; i < 6; ++i) {
				
					d1 = metaballone(p,i);
					d1 = smoothMin(d1,d2,20.);
					d2 =d1;
					}
				return d1;
			}
		
			// Making distance function
			float dist(vec3 p)
			{	
				float y = p.y;
				float d1 =metaball(p);
				float d2 = y-(_ypos); //For floor
			    d1 = smoothMin(d1,d2,20.);
				return d1;
			}


			//enhanced sphere tracing  http://erleuchtet.org/~cupe/permanent/enhanced_sphere_tracing.pdf

			float raymarch (vec3 ro,vec3 rd)
			{
				float previousradius = 0.0;
				float maxdistance = 3.;
				float outside = dist(ro) < 0. ? -1. : +1.;
				float pixelradius = 0.01;
				float omega = 1.2;
				float t =0.0001;
				float step = 0.;
				float minpixelt =999999999.;
				float mint = 0.;
				float hit = 0.01;
					for (float i = 0.; i < 80.; ++i) {

						float radius = outside*dist(ro+rd*t);
						bool fail = omega>1. &&step>(abs(radius)+abs(previousradius));
						if(fail){
							step -= step *omega;
							omega =1.0;
						}
						else{
							step = omega * radius;
						}
						previousradius = radius;
						float pixelt = radius/t;
						if(!fail&&pixelt<minpixelt){
							minpixelt = pixelt;
							mint = t;
						}
						if(!fail&&pixelt<pixelradius||t>maxdistance)
						break;
						t += step;
					}
				
					if ((t > maxdistance || minpixelt > pixelradius)&&(mint>hit)){
					return -1.;
					}
					else{
					return mint;
					}
				
			}

			// The MIT License
			// Copyright © 2013 Inigo Quilez
			// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
			// https://www.shadertoy.com/view/Xds3zN

			//Tetrahedron technique  http://iquilezles.org/www/articles/normalsSDF/normalsSDF.htm
			vec3 getnormal( in vec3 p)
			{
				vec2 e = vec2(0.5773,-0.5773)*0.0001;
				vec3 nor = normalize( e.xyy*dist(p+e.xyy) + e.yyx*dist(p+e.yyx) + e.yxy*dist(p+e.yxy ) + e.xxx*dist(p+e.xxx));
				nor = normalize(vec3(nor));
				return nor ;
			}
			////////////////////////////////////////////////////////////////////////////

			// Making shadow
			float softray( vec3 ro, vec3 rd , float hn)
			{
				float t = 0.000001;
				float jt = 0.0;
				float res = 1.;
				for (int i = 0; i < 20; ++i) {
					jt = dist(ro+rd*t);
					res = min(res,jt*hn/t);
					t = t+ clamp(0.02,2.,jt);
				}
				return clamp(res,0.,1.);
			}
			
			// The MIT License
			// Copyright © 2013 Inigo Quilez
			// Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions: The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
			// https://www.shadertoy.com/view/ld2GRz

			vec4 material(vec3 pos)
			{
                vec4 ballcol[6]=vec4[6](vec4(0.5,0.,0.,1.),
								vec4(0.,0.5,0.,1.),
								vec4(0.,0.,0.5,1.),
								vec4(0.25,0.25,0,1.),
								vec4(0.25,0,0.25,1.),
                    			vec4(0.,0.25,0.25,1.));
				vec3 mate = vec3(0,0,0);
				float w = 0.01;
					// Making ball color
					for (int i = 0; i < 6; ++i) {
						float x = clamp( (length( metaballvalue(i).xyz - pos )-metaballvalue(i).w)*10.,0.,1. ); 
						float p = 1.0 - x*x*(3.0-2.0*x);
						mate += p*vec3(ballcol[i].xyz);
						w += p;
					}
				// Making floor color
				float x = clamp(  (pos.y-_ypos)*10.,0.,1. );
				float p = 1.0 - x*x*(3.0-2.0*x);
				mate += p*vec3(0.4,0.4,0.4);
				w += p;
				mate /= w;
				return vec4(mate,1);
			}
			////////////////////////////////////////////////////
			
			//Phong reflection model ,Directional light
			vec4 lighting(vec3 pos,vec3 ro)
			{	
				vec3 mpos =pos;
				vec3 normal =getnormal(mpos);
					
				vec3 viewdir = normalize(pos-ro);
				vec3 lightdir = normalize(vec3(0.5,0.5,-0.5));
				
				float sha = softray(mpos,lightdir,3.3);
				vec4 Color = material(mpos);
				
				float NdotL = max(0.,dot(normal,lightdir));
				vec3 R = -normalize(reflect(lightdir,normal));
				float spec =pow(max(dot(R,-viewdir),0.),10.);

				vec4 col =  sha*(Color* NdotL+vec4(spec,spec,spec,0.));
				return col;
			}



void mainImage( out vec4 fragColor, in vec2 fragCoord )
{
   
    vec2 uv = (fragCoord* 2.0 - iResolution.xy) / min(iResolution.x,iResolution.y);
	
    vec2 sc = 2.*(uv-0.5);
    
    vec3 ro = vec3(0.18,0.2,-0.8);
        
    vec3 rd = normalize(vec3(sc,4)-ro);
    
    vec4 baccol = vec4((0.2+uv.y*0.5)*vec3(0.,1.,1.),1.);
    
    float t = raymarch(ro,rd);
    
	vec4 col;

	if (t==-1.) {
		col = baccol;
	}
	else{
		vec3 pos = ro+rd*t;
		col = lighting(pos,ro);
	}
 
    fragColor = col;
}'''


class StColorfulMetaballs(GlslSource):
    name = 'st_colorful_metaballs'
    label = 'Colorful metaballs'
    description = 'This is my first post at shadertoy! Thank you for watching!'
    dialect = 'st'
    GLSL = GLSL
    params = scene_params({}, render_scale=0.25)
