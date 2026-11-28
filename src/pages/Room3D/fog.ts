// The picture: the room's light in real units (cd/m2), through the haze, then as the eye takes it in (light.ts).
// Drawn cheaply (owner, 26 Nov: "in a thick haze room, things become fuzzy and semi-opaque. it's a fog"; "our
// simulation should (compute inexpensively) display our view of the experience"; 27 Nov: lots of lights raise the
// ambient, so beams have less impact than if each were drawn in a dark room).
//
//   1. The room's surfaces are drawn into an image with depth, in cd/m2 (no exposure yet).
//   2. The light in the air (beams.ts: every beam's own scattered light) is drawn into a second image, also cd/m2.
//   3. Ever smaller, softer copies are made of each (1/2 .. 1/32).
//   4. One pass per pixel, with d the length of its line of sight inside the room (the haze is in the room):
//        T      = exp(-sigma d)                     how much of the surface's light gets through
//        sharp  = the room, blurred more the further off it is (haze scatters mostly forwards: far things go fuzzy)
//        air    = the haze along the line, lit by the whole room (its mean brightness, and what is bright nearby):
//                 the veil that lies over everything in a lit, hazy room
//        light  = sharp T + air (1 - T) + the beams          ALL ADDED, in cd/m2
//        glare  = a little of the brightness nearby, spread across the eye
//      and only then the eye: exposure = key(La) / La, where La is how bright the view has been lately (step 5) and
//      the key keeps a dark room dark (Krawczyk et al. 2005); then a soft shoulder on the brightest channel, so a
//      strong red stays red rather than burning out to white as a camera's would.
//   5. La: the view's average brightness, averaged on a log scale as that method does (Reinhard et al. 2002): what
//      most of the view is like, so one bright thing off to the side doesn't blind the eye to the rest (its glare is
//      step 4's). Towards the middle counts more; outside the room doesn't count. Followed quickly when it rises and
//      slowly when it falls, as the eye does.
//
// A screen's own picture is shown as the eye sees it when looking at it (it adapts to the screen: its white is never
// burnt out), though the light it throws on the room and into the haze is its real light.

import * as THREE from "three";
import { PHOTO } from "./light";

export const BEAM_LAYER = 1;
const LEVELS = 5;                                   // the room: 1/2 .. 1/32
const BEAM_LEVELS = 4;                              // the beams: 1/2 .. 1/16
/** How fast the eye follows the view's brightness (seconds to cover most of a change): up, and down. ESTIMATES. */
const ADAPT_S = { up: 0.3, down: 2.5 };
/** How much of the light near a pixel is spread over it in the eye. ESTIMATE (the eye scatters about a tenth). */
const GLARE = 0.06;
/** A material that writes this as its alpha is a screen's picture (step "a screen's own picture" above). Far above 1:
 *  dots of light drawn over a surface add to its alpha, and must not be taken for a screen. */
export const SCREEN_ALPHA = 1000;

const quadVS = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }";

// A soft downsample: the centre and four diagonal taps a texel out. Alpha is carried: how much of it is the room.
const downFS = `uniform sampler2D src; uniform vec2 texel; varying vec2 vUv;
  void main(){
    vec4 c = texture2D(src, vUv) * 0.5;
    c += texture2D(src, vUv + texel * vec2(-1.0, -1.0)) * 0.125;
    c += texture2D(src, vUv + texel * vec2( 1.0, -1.0)) * 0.125;
    c += texture2D(src, vUv + texel * vec2(-1.0,  1.0)) * 0.125;
    c += texture2D(src, vUv + texel * vec2( 1.0,  1.0)) * 0.125;
    gl_FragColor = c;
  }`;

// The room's box along a line: boxPath, the length of the segment a -> b inside it; boxSpan, where a ray from o along
// d (a unit vector) enters and leaves it (the slab method).
export const BOX_GLSL = `
  vec2 boxSpan(vec3 o, vec3 d, vec3 lo, vec3 hi) {
    vec3 inv = 1.0 / (d + sign(d) * 1e-6 + vec3(equal(d, vec3(0.0))) * 1e-6);
    vec3 t0 = (lo - o) * inv, t1 = (hi - o) * inv;
    vec3 tn = min(t0, t1), tf = max(t0, t1);
    return vec2(max(max(tn.x, tn.y), tn.z), min(min(tf.x, tf.y), tf.z));
  }
  float boxPath(vec3 a, vec3 b, vec3 lo, vec3 hi) {
    vec3 d = b - a; float L = length(d);
    if (L < 1e-5) return 0.0;
    vec2 s = boxSpan(a, d / L, lo, hi);
    return max(0.0, min(s.y, L) - max(s.x, 0.0));
  }`;

// Which way haze throws light (per steradian), by the cosine of the angle between the light's direction and the
// direction to the eye: mostly forwards (Henyey-Greenstein, g 0.65: hazer droplets are about a micron), with a fifth
// thrown evenly. ESTIMATE. Towards you: about 20 times what goes sideways; back the way it came: two thirds of it.
export const PHASE_GLSL = `
  float hazePhase(float c) {
    float g = 0.65, k = 1.0 + g * g - 2.0 * g * c;
    return 0.8 * (1.0 - g * g) / (12.566 * k * sqrt(k)) + 0.2 * 0.0796;
  }`;

// How bright the view is (step 5), and the eye's memory of it (tPrev).
const meterFS = `uniform sampler2D tScene; uniform sampler2D tBeams; uniform sampler2D tPrev; uniform float up; uniform float down;
  void main(){
    float sum = 0.0, wsum = 0.0;
    for (int j = 0; j < 10; j++) for (int i = 0; i < 16; i++) {
      vec2 uv = (vec2(float(i), float(j)) + 0.5) / vec2(16.0, 10.0);
      vec4 s = texture2D(tScene, uv);
      float cov = clamp(s.a, 0.0, 1.0);                      // how much of this patch is the room
      // brightness, not lumens: a saturated light looks brighter than its lumens say (Helmholtz-Kohlrausch)
      float L = dot(s.rgb / max(cov, 0.05) + texture2D(tBeams, uv).rgb, vec3(0.30, 0.50, 0.20));
      if (!(L >= 0.0)) L = 0.0;
      float w = (1.0 - 0.6 * length(uv - 0.5)) * cov;
      sum += w * log(min(L, 4000.0) + 0.01); wsum += w;
    }
    float cur = wsum > 0.5 ? exp(sum / wsum) - 0.01 : 0.02;
    float prev = texture2D(tPrev, vec2(0.5)).r;
    float v = prev > 0.0 ? prev + (cur - prev) * (cur > prev ? up : down) : cur;
    if (!(v > 0.0)) v = 0.02;
    gl_FragColor = vec4(vec3(clamp(v, 0.001, 4000.0)), 1.0);
  }`;

const compFS = `uniform sampler2D tScene; uniform sampler2D tDepth; uniform sampler2D tBeams; uniform sampler2D tBeamsWide;
  uniform sampler2D t1; uniform sampler2D t2; uniform sampler2D t3; uniform sampler2D t4; uniform sampler2D t5;
  uniform sampler2D tMeter; uniform vec3 airMean; uniform float glare; uniform float screenEx;
  uniform vec2 px4; uniform vec2 px5; uniform vec2 pxB;
  uniform float sigma; uniform mat4 projInv; uniform mat4 viewInv; uniform vec3 camPos; uniform vec3 boxMin; uniform vec3 boxMax;
  varying vec2 vUv;
  ${BOX_GLSL}
  // A small copy read smoothly (a 3 x 3 tent, a texel apart): its glow is round, not its texels' squares.
  vec3 tent(sampler2D t, vec2 uv, vec2 px) {
    vec3 c = texture2D(t, uv).rgb * 4.0;
    c += (texture2D(t, uv + vec2(px.x, 0.0)).rgb + texture2D(t, uv - vec2(px.x, 0.0)).rgb
        + texture2D(t, uv + vec2(0.0, px.y)).rgb + texture2D(t, uv - vec2(0.0, px.y)).rgb) * 2.0;
    c += texture2D(t, uv + px).rgb + texture2D(t, uv - px).rgb
       + texture2D(t, uv + vec2(px.x, -px.y)).rgb + texture2D(t, uv + vec2(-px.x, px.y)).rgb;
    return c / 16.0;
  }
  void main(){
    float z = texture2D(tDepth, vUv).x;
    vec4 vp = projInv * vec4(vUv * 2.0 - 1.0, z * 2.0 - 1.0, 1.0); vp /= vp.w;
    vec3 wp = (viewInv * vp).xyz;
    float tau = sigma * boxPath(camPos, wp, boxMin, boxMax);
    float T = exp(-tau);
    vec4 s0 = texture2D(tScene, vUv);
    float b = clamp(tau * 1.4, 0.0, 3.0);                  // how fuzzy: by the optical depth to it
    vec3 s1 = texture2D(t1, vUv).rgb, s2 = texture2D(t2, vUv).rgb, s3 = texture2D(t3, vUv).rgb;
    vec3 sharp = b < 1.0 ? mix(s0.rgb, s1, b) : b < 2.0 ? mix(s1, s2, b - 1.0) : mix(s2, s3, b - 2.0);
    vec3 wide = tent(t4, vUv, px4) * 0.45 + tent(t5, vUv, px5) * 0.55 + tent(tBeamsWide, vUv, pxB);
    vec3 air = (0.5 * airMean + 0.5 * wide) * (1.0 - T);   // the haze, lit by the room
    vec3 hdr = sharp * T + air + texture2D(tBeams, vUv).rgb;
    hdr = mix(hdr, wide, glare);                           // glare in the eye
    // the eye: adapted to La, a dark view kept dark
    float La = clamp(texture2D(tMeter, vec2(0.5)).r, 0.02, 2000.0);
    float key = 1.03 - 2.0 / (2.0 + log(La + 1.0) * 0.4343);
    float ex = key / La;
    if (s0.a > 500.0) ex = min(ex, screenEx);                // a screen's picture: the eye on it adapts to it
    vec3 c = max(hdr, 0.0) * ex;
    float pk = max(max(c.r, c.g), max(c.b, 1e-6));
    float m = 1.0 - exp(-pk);                              // the shoulder, on the brightest channel: the hue is kept
    c *= m / pk;
    c = mix(c, vec3(m), 0.5 * (1.0 - 1.0 / (1.0 + 0.01 * max(pk - 1.0, 0.0))));   // far past white, it pales a little
    gl_FragColor = vec4(c, 1.0);
    #include <colorspace_fragment>
    // a dark picture bands in 8 bits: a grain of noise hides the steps
    gl_FragColor.rgb += (fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;
  }`;

/** Uniforms every material that draws light in the air shares (beams.ts): the haze, the room's box and depth. */
export const beamShared = {
  on: { value: false },                             // is there anything in the air to show a beam (not a shader's)
  sigma: { value: 0 },                              // what the haze takes out of light passing through it, per metre
  scatter: { value: 0 },                            // what it throws out of a beam, per metre (the same, but for the paths aid)
  tDepth: { value: null as THREE.Texture | null },
  invRes: { value: new THREE.Vector2(1, 1) },
  pxAngle: { value: 0.002 },                        // the angle one pixel covers (radians)
  projInv: { value: new THREE.Matrix4() },
  viewInv: { value: new THREE.Matrix4() },
  boxMin: { value: new THREE.Vector3(-1e4, -1e4, -1e4) },
  boxMax: { value: new THREE.Vector3(1e4, 1e4, 1e4) },
};

export class HazePost {
  private main: THREE.WebGLRenderTarget;
  private air: THREE.WebGLRenderTarget;
  private chain: THREE.WebGLRenderTarget[] = [];
  private airChain: THREE.WebGLRenderTarget[] = [];
  private meter: THREE.WebGLRenderTarget[] = [];
  private quad: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private qScene = new THREE.Scene();
  private qCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private down: THREE.ShaderMaterial;
  private comp: THREE.ShaderMaterial;
  private met: THREE.ShaderMaterial;
  private size = new THREE.Vector2();
  readonly boxMin = beamShared.boxMin.value;
  readonly boxMax = beamShared.boxMax.value;

  constructor(private r: THREE.WebGLRenderer) {
    const soft = { type: THREE.HalfFloatType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
    this.main = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
    this.main.depthTexture = new THREE.DepthTexture(1, 1, THREE.UnsignedIntType);
    this.air = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4, depthBuffer: false });
    for (let i = 0; i < LEVELS; i++) this.chain.push(new THREE.WebGLRenderTarget(1, 1, soft));
    for (let i = 0; i < BEAM_LEVELS; i++) this.airChain.push(new THREE.WebGLRenderTarget(1, 1, soft));
    for (let i = 0; i < 2; i++) this.meter.push(new THREE.WebGLRenderTarget(1, 1,
      { type: THREE.HalfFloatType, minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter, depthBuffer: false }));
    const mat = (fragmentShader: string, uniforms: Record<string, THREE.IUniform>) => new THREE.ShaderMaterial({
      uniforms, vertexShader: quadVS, fragmentShader, depthTest: false, depthWrite: false, toneMapped: false });
    this.down = mat(downFS, { src: { value: null }, texel: { value: new THREE.Vector2() } });
    this.met = mat(meterFS, { tScene: { value: this.chain[LEVELS - 1].texture }, tBeams: { value: this.airChain[BEAM_LEVELS - 1].texture },
      tPrev: { value: null }, up: { value: 1 }, down: { value: 1 } });
    this.comp = mat(compFS, {
      tScene: { value: this.main.texture }, tDepth: { value: this.main.depthTexture }, tBeams: { value: this.air.texture },
      tBeamsWide: { value: this.airChain[BEAM_LEVELS - 1].texture },
      t1: { value: this.chain[0].texture }, t2: { value: this.chain[1].texture }, t3: { value: this.chain[2].texture },
      t4: { value: this.chain[3].texture }, t5: { value: this.chain[4].texture },
      tMeter: { value: null }, airMean: { value: new THREE.Vector3() }, glare: { value: GLARE },
      screenEx: { value: 2.2 / PHOTO.screen_nits },
      px4: { value: new THREE.Vector2() }, px5: { value: new THREE.Vector2() }, pxB: { value: new THREE.Vector2() },
      sigma: { value: 0 }, projInv: beamShared.projInv, viewInv: beamShared.viewInv,
      camPos: { value: new THREE.Vector3() }, boxMin: beamShared.boxMin, boxMax: beamShared.boxMax,
    });
    this.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), this.comp);
    this.quad.frustumCulled = false;
    this.qScene.add(this.quad);
    beamShared.tDepth.value = this.main.depthTexture;
  }

  private fit() {
    const s = this.r.getDrawingBufferSize(new THREE.Vector2());
    if (s.equals(this.size)) return;
    this.size.copy(s);
    this.main.setSize(s.x, s.y);
    this.air.setSize(s.x, s.y);
    for (const chain of [this.chain, this.airChain]) {
      let w = s.x, h = s.y;
      for (const rt of chain) { w = Math.max(1, w >> 1); h = Math.max(1, h >> 1); rt.setSize(w, h); }
    }
    beamShared.invRes.value.set(1 / s.x, 1 / s.y);
    const u = this.comp.uniforms, px = (rt: THREE.WebGLRenderTarget, v: THREE.Vector2) => v.set(1 / rt.width, 1 / rt.height);
    px(this.chain[3], u.px4.value); px(this.chain[4], u.px5.value); px(this.airChain[BEAM_LEVELS - 1], u.pxB.value);
  }

  private pass(m: THREE.ShaderMaterial, to: THREE.WebGLRenderTarget | null) {
    this.quad.material = m;
    this.r.setRenderTarget(to);
    this.r.render(this.qScene, this.qCam);
  }

  private soften(from: THREE.WebGLRenderTarget, chain: THREE.WebGLRenderTarget[]) {
    let src = from;
    for (const rt of chain) {
      this.down.uniforms.src.value = src.texture;
      this.down.uniforms.texel.value.set(1 / src.width, 1 / src.height);
      this.pass(this.down, rt);
      src = rt;
    }
  }

  /** The room in haze of extinction ``sigma`` (per metre), lit by a room of mean brightness ``airMean`` (cd/m2,
   *  light.ts RoomLight.mean), ``dt`` seconds after the last picture. */
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera, sigma: number, airMean: THREE.Vector3, dt: number) {
    this.fit();
    beamShared.projInv.value.copy(camera.projectionMatrixInverse);
    beamShared.viewInv.value.copy(camera.matrixWorld);
    beamShared.pxAngle.value = (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)) / Math.max(1, this.size.y);
    // the room's surfaces
    camera.layers.set(0);
    this.r.setRenderTarget(this.main);
    this.r.render(scene, camera);
    // the light in the air, hidden behind what's in front by the room's depth
    const shadows = this.r.shadowMap.autoUpdate;
    this.r.shadowMap.autoUpdate = false;
    camera.layers.set(BEAM_LAYER);
    this.r.setRenderTarget(this.air);
    this.r.render(scene, camera);
    camera.layers.set(0);
    this.r.shadowMap.autoUpdate = shadows;
    this.soften(this.main, this.chain);
    this.soften(this.air, this.airChain);
    // the eye's adaptation
    const [prev, next] = this.meter;
    this.met.uniforms.tPrev.value = prev.texture;
    this.met.uniforms.up.value = 1 - Math.exp(-dt / ADAPT_S.up);
    this.met.uniforms.down.value = 1 - Math.exp(-dt / ADAPT_S.down);
    this.pass(this.met, next);
    this.meter.reverse();
    // the picture
    const u = this.comp.uniforms;
    u.tMeter.value = next.texture;
    u.sigma.value = sigma;
    u.airMean.value.copy(airMean);
    u.camPos.value.setFromMatrixPosition(camera.matrixWorld);
    this.pass(this.comp, null);
  }

  dispose() {
    for (const rt of [this.main, this.air, ...this.chain, ...this.airChain, ...this.meter]) rt.dispose();
    this.down.dispose(); this.comp.dispose(); this.met.dispose(); this.quad.geometry.dispose();
  }
}
