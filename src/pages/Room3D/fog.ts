// Haze as a fog, drawn cheaply (owner, 26 Nov: "in a thick haze room, things become fuzzy and semi-opaque. it's a
// fog"; "our simulation should (compute inexpensively) display our view of the experience").
//
// The room is drawn into an image with depth; a chain of ever smaller, softer copies is made (1/2 .. 1/32); then one
// pass per pixel, with d the length of its line of sight inside the room (the haze is in the room, not outside it):
//
//   T     = exp(-sigma d)                         how much of the surface's light gets through
//   sharp = the room, blurred more the further off it is (haze scatters mostly forwards: far things go fuzzy)
//   glow  = the widest copies: the room's light scattered through the air (bright things fill the fog round them with
//           their own colour: the screen, a red wash, a strobe)
//   pixel = sharp T + glow (1 - T)
//
// The beams are drawn after, straight to the screen, each fragment dimmed by its own distance through the haze and
// hidden behind what's in front of it by the room's depth (they're in the air, not on a surface).

import * as THREE from "three";

export const BEAM_LAYER = 1;
const LEVELS = 5;                                   // 1/2 .. 1/32

const quadVS = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }";

// A soft downsample: the centre and four diagonal taps a texel out.
const downFS = `uniform sampler2D src; uniform vec2 texel; varying vec2 vUv;
  void main(){
    vec3 c = texture2D(src, vUv).rgb * 0.5;
    c += texture2D(src, vUv + texel * vec2(-1.0, -1.0)).rgb * 0.125;
    c += texture2D(src, vUv + texel * vec2( 1.0, -1.0)).rgb * 0.125;
    c += texture2D(src, vUv + texel * vec2(-1.0,  1.0)).rgb * 0.125;
    c += texture2D(src, vUv + texel * vec2( 1.0,  1.0)).rgb * 0.125;
    gl_FragColor = vec4(c, 1.0);
  }`;

// The length of the segment a -> b inside the box (the slab method).
export const BOX_PATH_GLSL = `
  float boxPath(vec3 a, vec3 b, vec3 lo, vec3 hi) {
    vec3 d = b - a; float L = length(d);
    if (L < 1e-5) return 0.0;
    d /= L;
    vec3 inv = 1.0 / (d + sign(d) * 1e-6 + vec3(equal(d, vec3(0.0))) * 1e-6);
    vec3 t0 = (lo - a) * inv, t1 = (hi - a) * inv;
    vec3 tn = min(t0, t1), tf = max(t0, t1);
    float n = max(max(tn.x, tn.y), max(tn.z, 0.0)), f = min(min(tf.x, tf.y), min(tf.z, L));
    return max(0.0, f - n);
  }`;

const compFS = `#include <packing>
  uniform sampler2D tScene; uniform sampler2D tDepth;
  uniform sampler2D t1; uniform sampler2D t2; uniform sampler2D t3; uniform sampler2D t4; uniform sampler2D t5;
  uniform float sigma; uniform mat4 projInv; uniform mat4 viewInv; uniform vec3 camPos; uniform vec3 boxMin; uniform vec3 boxMax;
  varying vec2 vUv;
  ${BOX_PATH_GLSL}
  void main(){
    float z = texture2D(tDepth, vUv).x;
    vec4 vp = projInv * vec4(vUv * 2.0 - 1.0, z * 2.0 - 1.0, 1.0); vp /= vp.w;
    vec3 wp = (viewInv * vp).xyz;
    float tau = sigma * boxPath(camPos, wp, boxMin, boxMax);
    float T = exp(-tau);
    vec3 s0 = texture2D(tScene, vUv).rgb;
    float b = clamp(tau * 1.4, 0.0, 3.0);                  // how fuzzy: by the optical depth to it
    vec3 s1 = texture2D(t1, vUv).rgb, s2 = texture2D(t2, vUv).rgb, s3 = texture2D(t3, vUv).rgb;
    vec3 sharp = b < 1.0 ? mix(s0, s1, b) : b < 2.0 ? mix(s1, s2, b - 1.0) : mix(s2, s3, b - 2.0);
    vec3 glow = texture2D(t4, vUv).rgb * 0.45 + texture2D(t5, vUv).rgb * 0.55;
    gl_FragColor = vec4(sharp * T + glow * (1.0 - T) * 1.15, 1.0);
    #include <tonemapping_fragment>
    #include <colorspace_fragment>
  }`;

/** Uniforms every beam material shares (scene.ts beam()): the haze and the room's depth, for each fragment. */
export const beamShared = {
  sigma: { value: 0 },
  camPos: { value: new THREE.Vector3() },
  tDepth: { value: null as THREE.Texture | null },
  invRes: { value: new THREE.Vector2(1, 1) },
};

export class HazePost {
  private main: THREE.WebGLRenderTarget;
  private chain: THREE.WebGLRenderTarget[] = [];
  private quad: THREE.Mesh<THREE.PlaneGeometry, THREE.ShaderMaterial>;
  private qScene = new THREE.Scene();
  private qCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  private down: THREE.ShaderMaterial;
  private comp: THREE.ShaderMaterial;
  private size = new THREE.Vector2();
  readonly boxMin = new THREE.Vector3(-1e4, -1e4, -1e4);
  readonly boxMax = new THREE.Vector3(1e4, 1e4, 1e4);

  constructor(private r: THREE.WebGLRenderer) {
    const opts = { type: THREE.HalfFloatType, minFilter: THREE.LinearFilter, magFilter: THREE.LinearFilter, depthBuffer: false };
    this.main = new THREE.WebGLRenderTarget(1, 1, { type: THREE.HalfFloatType, samples: 4 });
    this.main.depthTexture = new THREE.DepthTexture(1, 1, THREE.UnsignedIntType);
    for (let i = 0; i < LEVELS; i++) this.chain.push(new THREE.WebGLRenderTarget(1, 1, opts));
    this.down = new THREE.ShaderMaterial({ uniforms: { src: { value: null }, texel: { value: new THREE.Vector2() } },
      vertexShader: quadVS, fragmentShader: downFS, depthTest: false, depthWrite: false, toneMapped: false });
    this.comp = new THREE.ShaderMaterial({
      uniforms: {
        tScene: { value: this.main.texture }, tDepth: { value: this.main.depthTexture },
        t1: { value: this.chain[0].texture }, t2: { value: this.chain[1].texture }, t3: { value: this.chain[2].texture },
        t4: { value: this.chain[3].texture }, t5: { value: this.chain[4].texture },
        sigma: { value: 0 }, projInv: { value: new THREE.Matrix4() }, viewInv: { value: new THREE.Matrix4() },
        camPos: { value: new THREE.Vector3() }, boxMin: { value: this.boxMin }, boxMax: { value: this.boxMax },
      },
      vertexShader: quadVS, fragmentShader: compFS, depthTest: false, depthWrite: false,
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
    let w = s.x, h = s.y;
    for (const rt of this.chain) { w = Math.max(1, w >> 1); h = Math.max(1, h >> 1); rt.setSize(w, h); }
    beamShared.invRes.value.set(1 / s.x, 1 / s.y);
  }

  private pass(m: THREE.ShaderMaterial, to: THREE.WebGLRenderTarget | null) {
    this.quad.material = m;
    this.r.setRenderTarget(to);
    this.r.render(this.qScene, this.qCam);
  }

  /** The room in haze of extinction ``sigma`` (per metre), then its beams. */
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera, sigma: number) {
    this.fit();
    camera.layers.set(0);
    this.r.setRenderTarget(this.main);
    this.r.render(scene, camera);
    let src: THREE.Texture = this.main.texture, w = this.size.x, h = this.size.y;
    for (const rt of this.chain) {
      this.down.uniforms.src.value = src;
      this.down.uniforms.texel.value.set(1 / w, 1 / h);
      this.pass(this.down, rt);
      src = rt.texture; w = rt.width; h = rt.height;
    }
    const u = this.comp.uniforms;
    u.sigma.value = sigma;
    u.projInv.value.copy(camera.projectionMatrixInverse);
    u.viewInv.value.copy(camera.matrixWorld);
    u.camPos.value.setFromMatrixPosition(camera.matrixWorld);
    this.pass(this.comp, null);
    // the beams, over it
    beamShared.camPos.value.copy(u.camPos.value);
    const bg = scene.background, auto = this.r.autoClear, shadows = this.r.shadowMap.autoUpdate;
    scene.background = null; this.r.autoClear = false; this.r.shadowMap.autoUpdate = false;
    camera.layers.set(BEAM_LAYER);
    this.r.render(scene, camera);
    camera.layers.set(0);
    scene.background = bg; this.r.autoClear = auto; this.r.shadowMap.autoUpdate = shadows;
  }

  dispose() {
    this.main.dispose();
    this.chain.forEach((rt) => rt.dispose());
    this.down.dispose(); this.comp.dispose(); this.quad.geometry.dispose();
  }
}
