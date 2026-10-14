// glsl_compat.cuh -- GLSL (ES 1.00 / ES 3.00 / desktop 1.20 subset) on CUDA C++, fp32 only.
//
// Used by translate.py: translated GLSL is placed INSIDE namespace glsl (inside a per-shader struct), so
// unqualified calls such as sin(), mix(), texture() resolve to the overloads below and never to CUDA's
// global math functions (namespace-scope name hiding).  Compile with NVRTC: -std=c++20 --use_fast_math.
//
// Conventions
//   * vec2/3/4, ivec*, uvec*, bvec* are tvec<T, N>; components are anonymous unions so .x/.r/.s all work as
//     plain members (single-letter swizzles need no rewriting).  Multi-letter swizzles are rewritten by the
//     translator: reads ->  v._sw<0,2>()  (returns a value), writes ->  v._sr<0,2>() = ...  (a proxy).
//   * mat2/3/4 are column-major like GLSL: m[i] is column i; m * v treats v as a column, v * m as a row.
//   * Textures are our frames: linear device memory, uint8 RGBA (sRGB-encoded bytes, read as unorm like a
//     plain GL_RGBA8 texture) or float RGBA.  uv (0,0) is the BOTTOM-left as in GL (rows are flipped on read
//     for top-first frames, like UNPACK_FLIP_Y).  Bilinear, clamp/repeat/mirror.  No mipmaps: lod is ignored.
//   * Derivatives (dFdx/dFdy/fwidth) use warp shuffles across a 2x2 pixel quad; the kernel MUST be launched
//     with blockDim = (16, 8) so lanes (l, l^1) are horizontal neighbours and (l, l^16) vertical neighbours.
//     This reproduces GL's fine derivatives exactly in uniform control flow; in divergent flow (undefined in
//     GL too) a missing partner yields 0.
#pragma once
#ifndef GLSL_COMPAT_CUH
#define GLSL_COMPAT_CUH

#define GLC __host__ __device__ __forceinline__
// Per-pixel loop budget shared by all loops of one shader invocation (translate.py inserts the guards).
#ifndef _G_LOOP_CAP
#define _G_LOOP_CAP 65536
#endif
// Per-thread time budget in SM clock cycles, checked every 64 loop iterations: ~1.2 ms at 2.5 GHz (RTX 4060),
// ~2 ms at 1.5 GHz (P4000).  Legit heavy shaders use well under this per block; a runaway loop is cut off, so
// one launch is bounded to (waves x budget) instead of hanging the GPU.
#ifndef _G_CYCLE_CAP
#define _G_CYCLE_CAP 3000000LL
#endif

namespace glsl {

typedef unsigned int uint;

// ------------------------------------------------------------------------------------------------ traits
template <bool B, class T = void> struct _en_if {};
template <class T> struct _en_if<true, T> { typedef T type; };
template <bool B, class T = void> using _enable_if = typename _en_if<B, T>::type;
template <class A, class B> struct _same { static constexpr bool value = false; };
template <class A> struct _same<A, A> { static constexpr bool value = true; };
template <class T> struct _ident { typedef T type; };
template <class T> using _nd = typename _ident<T>::type;  // non-deduced context
template <class T> struct _is_scalar { static constexpr bool value = false; };
template <> struct _is_scalar<float> { static constexpr bool value = true; };
template <> struct _is_scalar<double> { static constexpr bool value = true; };
template <> struct _is_scalar<int> { static constexpr bool value = true; };
template <> struct _is_scalar<uint> { static constexpr bool value = true; };
template <> struct _is_scalar<bool> { static constexpr bool value = true; };
template <class T> struct _is_intlike { static constexpr bool value = false; };
template <> struct _is_intlike<int> { static constexpr bool value = true; };
template <> struct _is_intlike<uint> { static constexpr bool value = true; };
// two scalars of different types (e.g. max(int, float)): promoted to float like desktop GLSL
template <class A, class B> struct _mixed2 {
    static constexpr bool value = _is_scalar<A>::value && _is_scalar<B>::value && !_same<A, B>::value;
};
template <class A, class B, class C> struct _mixed3 {
    static constexpr bool value = _is_scalar<A>::value && _is_scalar<B>::value && _is_scalar<C>::value &&
                                  !(_same<A, B>::value && _same<B, C>::value);
};

template <class T, int N> struct tvec;
template <int N> struct tmat;

// Robust indexing: GL leaves out-of-range array indices undefined; WebGL (ANGLE) clamps them.  In CUDA an
// out-of-range local-array index is a wild memory access, so we clamp too (folded away for constant indices).
GLC int _clampi(int i, int n) { return i < 0 ? 0 : (i >= n ? n - 1 : i); }

template <class T> struct _ncomp { static constexpr int value = 1; };
template <class T, int N> struct _ncomp<tvec<T, N>> { static constexpr int value = N; };
template <int N> struct _ncomp<tmat<N>> { static constexpr int value = N * N; };
template <class... A> struct _sumc { static constexpr int value = 0; };
template <class A, class... R> struct _sumc<A, R...> { static constexpr int value = _ncomp<A>::value + _sumc<R...>::value; };
template <class T> struct _is_val { static constexpr bool value = _is_scalar<T>::value; };
template <class T, int N> struct _is_val<tvec<T, N>> { static constexpr bool value = true; };
template <int N> struct _is_val<tmat<N>> { static constexpr bool value = true; };
template <class... A> struct _all_val { static constexpr bool value = true; };
template <class A, class... R> struct _all_val<A, R...> { static constexpr bool value = _is_val<A>::value && _all_val<R...>::value; };
template <class... A> struct _first { typedef void type; };
template <class A, class... R> struct _first<A, R...> { typedef A type; };

// flatten constructor arguments into a component list (GLSL constructor semantics)
template <class D, class S> GLC _enable_if<_is_scalar<S>::value> _flat(D* t, int& k, const S& v) { t[k++] = (D)v; }
template <class D, class S, int N> GLC void _flat(D* t, int& k, const tvec<S, N>& v);
template <class D, int N> GLC void _flat(D* t, int& k, const tmat<N>& m);

// --------------------------------------------------------------------------------------------- swizzle ref
template <class T, int N, int... I> struct _swref;
template <class T, int N, int M, int C> struct _wb;

#define GLC_VEC_COMMON(N)                                                                                     \
    GLC T& operator[](int i) { return _ref(i); }                                                              \
    GLC T operator[](int i) const { return _get(i); }                                                         \
    template <int... I> GLC tvec<T, sizeof...(I)> _sw() const { return tvec<T, sizeof...(I)>(_get(I)...); }  \
    template <int... I> GLC _swref<T, N, I...> _sr() { return _swref<T, N, I...>(*this); }                   \
    /* comma-free forms (the translator emits these: a swizzle may sit inside a macro argument); */           \
    /* C packs the component indices 2 bits each, first component in the low bits */                         \
    template <int C> GLC tvec<T, 2> _s2() const { return tvec<T, 2>(_get(C & 3), _get((C >> 2) & 3)); }       \
    template <int C> GLC tvec<T, 3> _s3() const { return tvec<T, 3>(_get(C & 3), _get((C >> 2) & 3), _get((C >> 4) & 3)); } \
    template <int C> GLC tvec<T, 4> _s4() const { return tvec<T, 4>(_get(C & 3), _get((C >> 2) & 3), _get((C >> 4) & 3), _get((C >> 6) & 3)); } \
    template <int C> GLC _wb<T, N, 2, C> _w2() { return _wb<T, N, 2, C>(*this); }                          \
    template <int C> GLC _wb<T, N, 3, C> _w3() { return _wb<T, N, 3, C>(*this); }                          \
    template <int C> GLC _wb<T, N, 4, C> _w4() { return _wb<T, N, 4, C>(*this); }                          \
    template <int C> GLC _swref<T, N, C & 3, (C >> 2) & 3> _r2() { return _swref<T, N, C & 3, (C >> 2) & 3>(*this); } \
    template <int C> GLC _swref<T, N, C & 3, (C >> 2) & 3, (C >> 4) & 3> _r3() { return _swref<T, N, C & 3, (C >> 2) & 3, (C >> 4) & 3>(*this); } \
    template <int C> GLC _swref<T, N, C & 3, (C >> 2) & 3, (C >> 4) & 3, (C >> 6) & 3> _r4() { return _swref<T, N, C & 3, (C >> 2) & 3, (C >> 4) & 3, (C >> 6) & 3>(*this); } \
    GLC int length() const { return N; }                                                                      \
    template <class... A, class = _enable_if<_all_val<A...>::value && ((_sumc<A...>::value >= N) || (sizeof...(A) == 1))>> \
    GLC explicit tvec(const A&... a) {                                                                        \
        constexpr int n = _sumc<A...>::value;                                                                 \
        T tmp[n < N ? N : n];                                                                                 \
        int k = 0;                                                                                            \
        (_flat(tmp, k, a), ...);                                                                              \
        if constexpr (n == 1) { _fill(tmp[0]); } else { _load(tmp); }                                         \
    }                                                                                                         \
    template <class U, class = _enable_if<_same<T, float>::value && _is_intlike<U>::value>>                   \
    GLC tvec(const tvec<U, N>& v) { T tmp[N]; int k = 0; _flat(tmp, k, v); _load(tmp); }

template <class T> struct tvec<T, 2> {
    union { T x, r, s; };
    union { T y, g, t; };
    GLC tvec() : x(T(0)), y(T(0)) {}
    GLC tvec(T a, T b) : x(a), y(b) {}
    GLC T& _ref(int i) { return i == 0 ? x : y; }
    GLC T _get(int i) const { return i == 0 ? x : y; }
    GLC void _fill(T v) { x = y = v; }
    GLC void _load(const T* p) { x = p[0]; y = p[1]; }
    GLC_VEC_COMMON(2)
};
template <class T> struct tvec<T, 3> {
    union { T x, r, s; };
    union { T y, g, t; };
    union { T z, b, p; };
    GLC tvec() : x(T(0)), y(T(0)), z(T(0)) {}
    GLC tvec(T a, T b, T c) : x(a), y(b), z(c) {}
    GLC T& _ref(int i) { return i == 0 ? x : (i == 1 ? y : z); }
    GLC T _get(int i) const { return i == 0 ? x : (i == 1 ? y : z); }
    GLC void _fill(T v) { x = y = z = v; }
    GLC void _load(const T* p) { x = p[0]; y = p[1]; z = p[2]; }
    GLC_VEC_COMMON(3)
};
template <class T> struct tvec<T, 4> {
    union { T x, r, s; };
    union { T y, g, t; };
    union { T z, b, p; };
    union { T w, a, q; };
    GLC tvec() : x(T(0)), y(T(0)), z(T(0)), w(T(0)) {}
    GLC tvec(T a_, T b_, T c_, T d_) : x(a_), y(b_), z(c_), w(d_) {}
    GLC T& _ref(int i) { return i == 0 ? x : (i == 1 ? y : (i == 2 ? z : w)); }
    GLC T _get(int i) const { return i == 0 ? x : (i == 1 ? y : (i == 2 ? z : w)); }
    GLC void _fill(T v) { x = y = z = w = v; }
    GLC void _load(const T* p) { x = p[0]; y = p[1]; z = p[2]; w = p[3]; }
    GLC_VEC_COMMON(4)
};

template <class D, class S, int N> GLC void _flat(D* t, int& k, const tvec<S, N>& v) {
#pragma unroll
    for (int i = 0; i < N; ++i) t[k++] = (D)v[i];
}

typedef tvec<float, 2> vec2;
typedef tvec<float, 3> vec3;
typedef tvec<float, 4> vec4;
typedef tvec<int, 2> ivec2;
typedef tvec<int, 3> ivec3;
typedef tvec<int, 4> ivec4;
typedef tvec<uint, 2> uvec2;
typedef tvec<uint, 3> uvec3;
typedef tvec<uint, 4> uvec4;
typedef tvec<bool, 2> bvec2;
typedef tvec<bool, 3> bvec3;
typedef tvec<bool, 4> bvec4;

// swizzle write proxy: v._sr<0,1>() = e;  v._sr<2,0>() *= 2.0f;
template <class T, int N, int... I> struct _swref {
    typedef tvec<T, sizeof...(I)> V;
    tvec<T, N>& v;
    GLC explicit _swref(tvec<T, N>& v_) : v(v_) {}
    GLC V get() const { return V(v._get(I)...); }
    GLC operator V() const { return get(); }
    GLC _swref& operator=(const V& a) {
        int k = 0;
        ((v._ref(I) = a._get(k++)), ...);
        return *this;
    }
    GLC _swref& operator=(const _swref& o) { return *this = o.get(); }
    template <class R> GLC _swref& operator+=(const R& r) { return *this = get() + r; }
    template <class R> GLC _swref& operator-=(const R& r) { return *this = get() - r; }
    template <class R> GLC _swref& operator*=(const R& r) { return *this = get() * r; }
    template <class R> GLC _swref& operator/=(const R& r) { return *this = get() / r; }
    template <class R> GLC _swref& operator%=(const R& r) { return *this = get() % r; }
    template <class R> GLC _swref& operator^=(const R& r) { return *this = get() ^ r; }
    template <class R> GLC _swref& operator&=(const R& r) { return *this = get() & r; }
    template <class R> GLC _swref& operator|=(const R& r) { return *this = get() | r; }
};

// swizzle passed to an out/inout parameter: pR(p.xz, a).  GLSL copies in and back out; this proxy holds the
// copy, converts to the parameter's lvalue reference, and writes back when the full expression ends.
template <class T, int N, int M, int C> struct _wb {
    tvec<T, N>& src;
    tvec<T, M> v;
    GLC explicit _wb(tvec<T, N>& s) : src(s) { _Pragma("unroll") for (int j = 0; j < M; ++j) v[j] = s[(C >> (2 * j)) & 3]; }
    GLC ~_wb() { _Pragma("unroll") for (int j = 0; j < M; ++j) src[(C >> (2 * j)) & 3] = v[j]; }
    GLC operator tvec<T, M>&() { return v; }
};

// ------------------------------------------------------------------------------------------- vector ops
// float vectors: NON-template overloads, so ints/proxies/ivecs convert implicitly (GLSL 1.20 rules).
#define GLC_FV_BINOP(V, N, OP)                                                                                \
    GLC V operator OP(const V& a, const V& b) { V r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a[i] OP b[i]; return r; } \
    GLC V operator OP(const V& a, float b) { V r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a[i] OP b; return r; }      \
    GLC V operator OP(float a, const V& b) { V r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a OP b[i]; return r; }      \
    GLC V& operator OP##=(V & a, const V& b) { a = a OP b; return a; }                                       \
    GLC V& operator OP##=(V & a, float b) { a = a OP b; return a; }
#define GLC_FV_OPS(V, N)                                                                                      \
    GLC_FV_BINOP(V, N, +) GLC_FV_BINOP(V, N, -) GLC_FV_BINOP(V, N, *) GLC_FV_BINOP(V, N, /)                  \
    GLC V operator-(const V& a) { V r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = -a[i]; return r; } \
    GLC V operator+(const V& a) { return a; }                                                                 \
    GLC bool operator==(const V& a, const V& b) { bool e = true; _Pragma("unroll") for (int i = 0; i < N; ++i) e = e && (a[i] == b[i]); return e; } \
    GLC bool operator!=(const V& a, const V& b) { return !(a == b); }                                         \
    GLC V& operator++(V & a) { a += 1.f; return a; }                                                          \
    GLC V& operator--(V & a) { a -= 1.f; return a; }                                                          \
    GLC V operator++(V & a, int) { V o = a; a += 1.f; return o; }                                             \
    GLC V operator--(V & a, int) { V o = a; a -= 1.f; return o; }
GLC_FV_OPS(vec2, 2)
GLC_FV_OPS(vec3, 3)
GLC_FV_OPS(vec4, 4)

// integer / unsigned vectors: templates restricted to int-like T
#define GLC_IV_BINOP(OP)                                                                                      \
    template <class T, int N, class = _enable_if<_is_intlike<T>::value>>                                     \
    GLC tvec<T, N> operator OP(const tvec<T, N>& a, const tvec<T, N>& b) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a[i] OP b[i]; return r; } \
    template <class T, int N, class = _enable_if<_is_intlike<T>::value>>                                     \
    GLC tvec<T, N> operator OP(const tvec<T, N>& a, _nd<T> b) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a[i] OP b; return r; } \
    template <class T, int N, class = _enable_if<_is_intlike<T>::value>>                                     \
    GLC tvec<T, N> operator OP(_nd<T> a, const tvec<T, N>& b) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a OP b[i]; return r; } \
    template <class T, int N, class = _enable_if<_is_intlike<T>::value>>                                     \
    GLC tvec<T, N>& operator OP##=(tvec<T, N>& a, const tvec<T, N>& b) { a = a OP b; return a; }             \
    template <class T, int N, class = _enable_if<_is_intlike<T>::value>>                                     \
    GLC tvec<T, N>& operator OP##=(tvec<T, N>& a, _nd<T> b) { a = a OP b; return a; }
GLC_IV_BINOP(+) GLC_IV_BINOP(-) GLC_IV_BINOP(*) GLC_IV_BINOP(/) GLC_IV_BINOP(%)
GLC_IV_BINOP(&) GLC_IV_BINOP(|) GLC_IV_BINOP(^) GLC_IV_BINOP(<<) GLC_IV_BINOP(>>)
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> operator-(const tvec<T, N>& a) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = -a[i]; return r; }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> operator~(const tvec<T, N>& a) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = ~a[i]; return r; }
template <class T, int N, class = _enable_if<!_same<T, float>::value>>
GLC bool operator==(const tvec<T, N>& a, const tvec<T, N>& b) { bool e = true; _Pragma("unroll") for (int i = 0; i < N; ++i) e = e && (a[i] == b[i]); return e; }
template <class T, int N, class = _enable_if<!_same<T, float>::value>>
GLC bool operator!=(const tvec<T, N>& a, const tvec<T, N>& b) { return !(a == b); }
// desktop-GLSL style mixed float-vector / int-vector arithmetic (ivec converts implicitly to vec)
#define GLC_MIXED_IV(OP)                                                                                      \
    template <int N> GLC tvec<float, N> operator OP(const tvec<float, N>& a, const tvec<int, N>& b) { return a OP tvec<float, N>(b); } \
    template <int N> GLC tvec<float, N> operator OP(const tvec<int, N>& a, const tvec<float, N>& b) { return tvec<float, N>(a) OP b; } \
    template <int N> GLC tvec<float, N> operator OP(const tvec<int, N>& a, float b) { return tvec<float, N>(a) OP b; }                 \
    template <int N> GLC tvec<float, N> operator OP(float a, const tvec<int, N>& b) { return a OP tvec<float, N>(b); }
GLC_MIXED_IV(+) GLC_MIXED_IV(-) GLC_MIXED_IV(*) GLC_MIXED_IV(/)

// -------------------------------------------------------------------------------------------- matrices
template <int N> struct tmat {
    tvec<float, N> c[N];
    GLC tmat() {}
    GLC tvec<float, N>& operator[](int i) { return c[_clampi(i, N)]; }   // clamped like WebGL/ANGLE
    GLC const tvec<float, N>& operator[](int i) const { return c[_clampi(i, N)]; }
    GLC int length() const { return N; }
    template <class... A, class = _enable_if<_all_val<A...>::value>> GLC explicit tmat(const A&... a) {
        typedef typename _first<A...>::type A0;
        if constexpr (sizeof...(A) == 1 && _is_scalar<A0>::value) {  // diagonal
            float d = 0.f; int k = 0; _flat(&d, k, a...);
#pragma unroll
            for (int i = 0; i < N; ++i)
#pragma unroll
                for (int j = 0; j < N; ++j) c[i][j] = (i == j) ? d : 0.f;
        } else if constexpr (sizeof...(A) == 1 && _ncomp<A0>::value > 1 && !_is_scalar<A0>::value &&
                             (_ncomp<A0>::value == 4 || _ncomp<A0>::value == 9 || _ncomp<A0>::value == 16) &&
                             !_same<A0, tvec<float, 4>>::value) {  // matrix from matrix: resize
            _resize(a...);
        } else {
            constexpr int n = _sumc<A...>::value;
            float tmp[n < N * N ? N * N : n];
            int k = 0;
            (_flat(tmp, k, a), ...);
#pragma unroll
            for (int i = 0; i < N; ++i)
#pragma unroll
                for (int j = 0; j < N; ++j) c[i][j] = tmp[i * N + j];
        }
    }
    template <int M> GLC void _resize(const tmat<M>& m) {
#pragma unroll
        for (int i = 0; i < N; ++i)
#pragma unroll
            for (int j = 0; j < N; ++j) c[i][j] = (i < M && j < M) ? m.c[i < M ? i : 0][j < M ? j : 0] : (i == j ? 1.f : 0.f);
    }
    template <class X> GLC void _resize(const X& v) {  // mat2(vec4)
        float tmp[N * N]; int k = 0; _flat(tmp, k, v);
#pragma unroll
        for (int i = 0; i < N; ++i)
#pragma unroll
            for (int j = 0; j < N; ++j) c[i][j] = tmp[i * N + j];
    }
};
template <class D, int N> GLC void _flat(D* t, int& k, const tmat<N>& m) {
#pragma unroll
    for (int i = 0; i < N; ++i)
#pragma unroll
        for (int j = 0; j < N; ++j) t[k++] = (D)m.c[i][j];
}
typedef tmat<2> mat2;
typedef tmat<3> mat3;
typedef tmat<4> mat4;
typedef tmat<2> mat2x2;
typedef tmat<3> mat3x3;
typedef tmat<4> mat4x4;

template <int N> GLC tvec<float, N> operator*(const tmat<N>& m, const tvec<float, N>& v) {
    tvec<float, N> r = m.c[0] * v[0];
#pragma unroll
    for (int j = 1; j < N; ++j) r += m.c[j] * v[j];
    return r;
}
template <int N> GLC tvec<float, N> operator*(const tvec<float, N>& v, const tmat<N>& m) {
    tvec<float, N> r;
#pragma unroll
    for (int j = 0; j < N; ++j) {
        float s = 0.f;
#pragma unroll
        for (int i = 0; i < N; ++i) s += v[i] * m.c[j][i];
        r[j] = s;
    }
    return r;
}
template <int N> GLC tmat<N> operator*(const tmat<N>& a, const tmat<N>& b) {
    tmat<N> r;
#pragma unroll
    for (int j = 0; j < N; ++j) r.c[j] = a * b.c[j];
    return r;
}
#define GLC_MAT_SOP(OP)                                                                                       \
    template <int N> GLC tmat<N> operator OP(const tmat<N>& a, float s) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = a.c[j] OP s; return r; } \
    template <int N> GLC tmat<N> operator OP(float s, const tmat<N>& a) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = s OP a.c[j]; return r; }
GLC_MAT_SOP(*) GLC_MAT_SOP(/) GLC_MAT_SOP(+) GLC_MAT_SOP(-)
template <int N> GLC tmat<N> operator+(const tmat<N>& a, const tmat<N>& b) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = a.c[j] + b.c[j]; return r; }
template <int N> GLC tmat<N> operator-(const tmat<N>& a, const tmat<N>& b) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = a.c[j] - b.c[j]; return r; }
template <int N> GLC tmat<N> operator/(const tmat<N>& a, const tmat<N>& b) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = a.c[j] / b.c[j]; return r; }
template <int N> GLC tmat<N> operator-(const tmat<N>& a) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = -a.c[j]; return r; }
template <int N> GLC tmat<N>& operator*=(tmat<N>& a, const tmat<N>& b) { a = a * b; return a; }
template <int N> GLC tmat<N>& operator*=(tmat<N>& a, float s) { a = a * s; return a; }
template <int N> GLC tmat<N>& operator+=(tmat<N>& a, const tmat<N>& b) { a = a + b; return a; }
template <int N> GLC tmat<N>& operator-=(tmat<N>& a, const tmat<N>& b) { a = a - b; return a; }
template <int N> GLC tvec<float, N>& operator*=(tvec<float, N>& v, const tmat<N>& m) { v = v * m; return v; }
template <int N> GLC bool operator==(const tmat<N>& a, const tmat<N>& b) { bool e = true; _Pragma("unroll") for (int j = 0; j < N; ++j) e = e && (a.c[j] == b.c[j]); return e; }
template <int N> GLC bool operator!=(const tmat<N>& a, const tmat<N>& b) { return !(a == b); }

// ------------------------------------------------------------------------------- built-in functions
// Scalar float versions call CUDA's fp32 math explicitly (::sinf etc.); --use_fast_math maps them to the
// hardware approximations (__sinf, ex2.approx, rcp/div.approx), which is what NVIDIA's GL driver does too.
#define GLC_CW1(NAME, EXPR)                                                                                  \
    GLC float NAME(float x) { return EXPR; }                                                                  \
    template <int N> GLC tvec<float, N> NAME(const tvec<float, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = NAME(v[i]); return r; }
#define GLC_CW2(NAME, EXPR)                                                                                  \
    GLC float NAME(float x, float y) { return EXPR; }                                                         \
    template <int N> GLC tvec<float, N> NAME(const tvec<float, N>& a, const tvec<float, N>& b) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = NAME(a[i], b[i]); return r; } \
    template <int N> GLC tvec<float, N> NAME(const tvec<float, N>& a, float b) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = NAME(a[i], b); return r; } \
    template <class A, class B, class = _enable_if<_mixed2<A, B>::value>> GLC float NAME(A a, B b) { return NAME((float)a, (float)b); } \
    template <class U, int N, class = _enable_if<_is_intlike<U>::value>> GLC tvec<float, N> NAME(const tvec<float, N>& a, const tvec<U, N>& b) { return NAME(a, tvec<float, N>(b)); } \
    template <class U, int N, class = _enable_if<_is_intlike<U>::value>> GLC tvec<float, N> NAME(const tvec<U, N>& a, const tvec<float, N>& b) { return NAME(tvec<float, N>(a), b); }

GLC_CW1(radians, x * 0.017453292519943295f)
GLC_CW1(degrees, x * 57.29577951308232f)
GLC_CW1(sin, ::sinf(x))
GLC_CW1(cos, ::cosf(x))
GLC_CW1(tan, ::tanf(x))
GLC_CW1(asin, ::asinf(x))
GLC_CW1(acos, ::acosf(x))
GLC_CW1(sinh, ::sinhf(x))
GLC_CW1(cosh, ::coshf(x))
GLC_CW1(tanh, ::tanhf(x))
GLC_CW1(asinh, ::asinhf(x))
GLC_CW1(acosh, ::acoshf(x))
GLC_CW1(atanh, ::atanhf(x))
GLC_CW1(exp, ::expf(x))
GLC_CW1(log, ::logf(x))
GLC_CW1(exp2, ::exp2f(x))
GLC_CW1(log2, ::log2f(x))
GLC_CW1(sqrt, ::sqrtf(x))
GLC_CW1(inversesqrt, ::rsqrtf(x))
GLC_CW1(abs, ::fabsf(x))
GLC_CW1(sign, (x > 0.f) ? 1.f : ((x < 0.f) ? -1.f : 0.f))
GLC_CW1(floor, ::floorf(x))
GLC_CW1(ceil, ::ceilf(x))
GLC_CW1(trunc, ::truncf(x))
GLC_CW1(round, ::rintf(x))      // GLSL leaves .5 implementation-defined; rint is one instruction
GLC_CW1(roundEven, ::rintf(x))
GLC_CW1(fract, x - ::floorf(x))
// GLSL leaves pow(x<0, y) undefined (NaN on most GPUs).  We follow C for integral y (sign-correct) and give 0
// otherwise, so a shader that cubes a signed value does not turn whole rows black.
GLC float _gpow(float x, float y) {
    if (x > 0.f) return ::exp2f(y * ::log2f(x));
    if (x == 0.f) return y == 0.f ? 1.f : (y > 0.f ? 0.f : 1.0e30f);
    if (::floorf(y) != y) return 0.f;
    const float r = ::exp2f(y * ::log2f(-x));
    return (((long long)y) & 1) ? -r : r;
}
GLC_CW2(pow, _gpow(x, y))
GLC_CW2(mod, x - y * ::floorf(x / y))  // GLSL semantics, NOT fmodf
GLC_CW2(min, ::fminf(x, y))
GLC_CW2(max, ::fmaxf(x, y))
GLC_CW2(atan, ::atan2f(x, y))           // atan(y, x)
GLC_CW1(atan, ::atanf(x))               // atan(y_over_x)
GLC_CW2(step, (y < x) ? 0.f : 1.f)      // step(edge, x)
GLC float atan2(float y, float x) { return ::atan2f(y, x); }
template <int N> GLC tvec<float, N> step(float e, const tvec<float, N>& v) { return step(tvec<float, N>(e), v); }
GLC float fma(float a, float b, float c) { return ::fmaf(a, b, c); }

// integer scalar / vector
GLC int abs(int x) { return x < 0 ? -x : x; }
GLC int sign(int x) { return (x > 0) - (x < 0); }
GLC int min(int a, int b) { return a < b ? a : b; }
GLC int max(int a, int b) { return a > b ? a : b; }
GLC uint min(uint a, uint b) { return a < b ? a : b; }
GLC uint max(uint a, uint b) { return a > b ? a : b; }
GLC int clamp(int x, int lo, int hi) { return min(max(x, lo), hi); }
GLC uint clamp(uint x, uint lo, uint hi) { return min(max(x, lo), hi); }
GLC int mod(int x, int y) { return x - y * (int)::floorf((float)x / (float)y); }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> abs(const tvec<T, N>& v) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = abs(v[i]); return r; }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> min(const tvec<T, N>& a, const tvec<T, N>& b) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = min(a[i], b[i]); return r; }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> max(const tvec<T, N>& a, const tvec<T, N>& b) { tvec<T, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = max(a[i], b[i]); return r; }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> min(const tvec<T, N>& a, _nd<T> b) { return min(a, tvec<T, N>(b)); }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> max(const tvec<T, N>& a, _nd<T> b) { return max(a, tvec<T, N>(b)); }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> clamp(const tvec<T, N>& v, _nd<T> lo, _nd<T> hi) { return min(max(v, tvec<T, N>(lo)), tvec<T, N>(hi)); }
template <class T, int N, class = _enable_if<_is_intlike<T>::value>>
GLC tvec<T, N> clamp(const tvec<T, N>& v, const tvec<T, N>& lo, const tvec<T, N>& hi) { return min(max(v, lo), hi); }

// clamp / mix / smoothstep
GLC float clamp(float x, float lo, float hi) { return ::fminf(::fmaxf(x, lo), hi); }
template <int N> GLC tvec<float, N> clamp(const tvec<float, N>& v, float lo, float hi) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = clamp(v[i], lo, hi); return r; }
template <int N> GLC tvec<float, N> clamp(const tvec<float, N>& v, const tvec<float, N>& lo, const tvec<float, N>& hi) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = clamp(v[i], lo[i], hi[i]); return r; }
GLC float mix(float a, float b, float t) { return a + (b - a) * t; }
GLC float mix(float a, float b, bool t) { return t ? b : a; }
template <int N> GLC tvec<float, N> mix(const tvec<float, N>& a, const tvec<float, N>& b, float t) { return a + (b - a) * t; }
template <int N> GLC tvec<float, N> mix(const tvec<float, N>& a, const tvec<float, N>& b, const tvec<float, N>& t) { return a + (b - a) * t; }
template <int N> GLC tvec<float, N> mix(const tvec<float, N>& a, const tvec<float, N>& b, const tvec<bool, N>& t) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = t[i] ? b[i] : a[i]; return r; }
GLC float smoothstep(float e0, float e1, float x) { float t = clamp((x - e0) / (e1 - e0), 0.f, 1.f); return t * t * (3.f - 2.f * t); }
template <int N> GLC tvec<float, N> smoothstep(float e0, float e1, const tvec<float, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = smoothstep(e0, e1, v[i]); return r; }
template <int N> GLC tvec<float, N> smoothstep(const tvec<float, N>& e0, const tvec<float, N>& e1, const tvec<float, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = smoothstep(e0[i], e1[i], v[i]); return r; }
template <class A, class B, class C, class = _enable_if<_mixed3<A, B, C>::value>> GLC float clamp(A x, B lo, C hi) { return clamp((float)x, (float)lo, (float)hi); }
template <class A, class B, class C, class = _enable_if<_mixed3<A, B, C>::value>> GLC float mix(A a, B b, C t) { return mix((float)a, (float)b, (float)t); }
template <class A, class B, class C, class = _enable_if<_mixed3<A, B, C>::value>> GLC float smoothstep(A a, B b, C x) { return smoothstep((float)a, (float)b, (float)x); }

GLC float modf(float x, float& i) { i = ::truncf(x); return x - i; }
GLC bool isnan(float x) { return ::isnan(x); }
GLC bool isinf(float x) { return ::isinf(x); }
template <int N> GLC tvec<bool, N> isnan(const tvec<float, N>& v) { tvec<bool, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = ::isnan(v[i]); return r; }
template <int N> GLC tvec<bool, N> isinf(const tvec<float, N>& v) { tvec<bool, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = ::isinf(v[i]); return r; }
GLC int floatBitsToInt(float x) { return __float_as_int(x); }
GLC uint floatBitsToUint(float x) { return __float_as_uint(x); }
GLC float intBitsToFloat(int x) { return __int_as_float(x); }
GLC float uintBitsToFloat(uint x) { return __uint_as_float(x); }
template <int N> GLC tvec<uint, N> floatBitsToUint(const tvec<float, N>& v) { tvec<uint, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = __float_as_uint(v[i]); return r; }
template <int N> GLC tvec<int, N> floatBitsToInt(const tvec<float, N>& v) { tvec<int, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = __float_as_int(v[i]); return r; }
template <int N> GLC tvec<float, N> uintBitsToFloat(const tvec<uint, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = __uint_as_float(v[i]); return r; }
template <int N> GLC tvec<float, N> intBitsToFloat(const tvec<int, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = __int_as_float(v[i]); return r; }

// geometric
GLC float dot(float a, float b) { return a * b; }
template <int N> GLC float dot(const tvec<float, N>& a, const tvec<float, N>& b) { float s = a[0] * b[0]; _Pragma("unroll") for (int i = 1; i < N; ++i) s += a[i] * b[i]; return s; }
GLC float length(float x) { return ::fabsf(x); }
template <int N> GLC float length(const tvec<float, N>& v) { return ::sqrtf(dot(v, v)); }
GLC float distance(float a, float b) { return ::fabsf(a - b); }
template <int N> GLC float distance(const tvec<float, N>& a, const tvec<float, N>& b) { return length(a - b); }
GLC float normalize(float x) { return x < 0.f ? -1.f : 1.f; }
template <int N> GLC tvec<float, N> normalize(const tvec<float, N>& v) { return v * ::rsqrtf(dot(v, v)); }
GLC vec3 cross(const vec3& a, const vec3& b) { return vec3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x); }
template <int N> GLC tvec<float, N> reflect(const tvec<float, N>& i, const tvec<float, N>& n) { return i - 2.f * dot(n, i) * n; }
GLC float reflect(float i, float n) { return i - 2.f * n * i * n; }
template <int N> GLC tvec<float, N> refract(const tvec<float, N>& i, const tvec<float, N>& n, float eta) {
    float d = dot(n, i), k = 1.f - eta * eta * (1.f - d * d);
    return k < 0.f ? tvec<float, N>(0.f) : eta * i - (eta * d + ::sqrtf(k)) * n;
}
template <int N> GLC tvec<float, N> faceforward(const tvec<float, N>& n, const tvec<float, N>& i, const tvec<float, N>& nref) { return dot(nref, i) < 0.f ? n : -n; }

// matrix functions
template <int N> GLC tmat<N> matrixCompMult(const tmat<N>& a, const tmat<N>& b) { tmat<N> r; _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[j] = a.c[j] * b.c[j]; return r; }
template <int N> GLC tmat<N> transpose(const tmat<N>& m) { tmat<N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) _Pragma("unroll") for (int j = 0; j < N; ++j) r.c[i][j] = m.c[j][i]; return r; }
template <int N> GLC tmat<N> outerProduct(const tvec<float, N>& c, const tvec<float, N>& r) { tmat<N> m; _Pragma("unroll") for (int j = 0; j < N; ++j) m.c[j] = c * r[j]; return m; }
GLC float determinant(const mat2& m) { return m.c[0].x * m.c[1].y - m.c[1].x * m.c[0].y; }
GLC float determinant(const mat3& m) { return dot(m.c[0], cross(m.c[1], m.c[2])); }
GLC float determinant(const mat4& m) {
    float s0 = m.c[0].x * m.c[1].y - m.c[1].x * m.c[0].y, s1 = m.c[0].x * m.c[1].z - m.c[1].x * m.c[0].z;
    float s2 = m.c[0].x * m.c[1].w - m.c[1].x * m.c[0].w, s3 = m.c[0].y * m.c[1].z - m.c[1].y * m.c[0].z;
    float s4 = m.c[0].y * m.c[1].w - m.c[1].y * m.c[0].w, s5 = m.c[0].z * m.c[1].w - m.c[1].z * m.c[0].w;
    float c5 = m.c[2].z * m.c[3].w - m.c[3].z * m.c[2].w, c4 = m.c[2].y * m.c[3].w - m.c[3].y * m.c[2].w;
    float c3 = m.c[2].y * m.c[3].z - m.c[3].y * m.c[2].z, c2 = m.c[2].x * m.c[3].w - m.c[3].x * m.c[2].w;
    float c1 = m.c[2].x * m.c[3].z - m.c[3].x * m.c[2].z, c0 = m.c[2].x * m.c[3].y - m.c[3].x * m.c[2].y;
    return s0 * c5 - s1 * c4 + s2 * c3 + s3 * c2 - s4 * c1 + s5 * c0;
}
GLC mat2 inverse(const mat2& m) { float d = 1.f / determinant(m); return mat2(m.c[1].y * d, -m.c[0].y * d, -m.c[1].x * d, m.c[0].x * d); }
GLC mat3 inverse(const mat3& m) {
    vec3 a = m.c[0], b = m.c[1], c = m.c[2];
    vec3 r0 = cross(b, c), r1 = cross(c, a), r2 = cross(a, b);
    float id = 1.f / dot(a, r0);
    return transpose(mat3(r0 * id, r1 * id, r2 * id));
}
GLC mat4 inverse(const mat4& m) {
    // cofactor expansion (Laplace / 2x2 sub-determinants), column-major
    float a00 = m.c[0].x, a01 = m.c[0].y, a02 = m.c[0].z, a03 = m.c[0].w, a10 = m.c[1].x, a11 = m.c[1].y, a12 = m.c[1].z, a13 = m.c[1].w;
    float a20 = m.c[2].x, a21 = m.c[2].y, a22 = m.c[2].z, a23 = m.c[2].w, a30 = m.c[3].x, a31 = m.c[3].y, a32 = m.c[3].z, a33 = m.c[3].w;
    float b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10, b03 = a01 * a12 - a02 * a11;
    float b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12, b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30;
    float b08 = a20 * a33 - a23 * a30, b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    float id = 1.f / (b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06);
    return mat4((a11 * b11 - a12 * b10 + a13 * b09) * id, (a02 * b10 - a01 * b11 - a03 * b09) * id, (a31 * b05 - a32 * b04 + a33 * b03) * id, (a22 * b04 - a21 * b05 - a23 * b03) * id,
                (a12 * b08 - a10 * b11 - a13 * b07) * id, (a00 * b11 - a02 * b08 + a03 * b07) * id, (a32 * b02 - a30 * b05 - a33 * b01) * id, (a20 * b05 - a22 * b02 + a23 * b01) * id,
                (a10 * b10 - a11 * b08 + a13 * b06) * id, (a01 * b08 - a00 * b10 - a03 * b06) * id, (a30 * b04 - a31 * b02 + a33 * b00) * id, (a21 * b02 - a20 * b04 - a23 * b00) * id,
                (a11 * b07 - a10 * b09 - a12 * b06) * id, (a00 * b09 - a01 * b07 + a02 * b06) * id, (a31 * b01 - a30 * b03 - a32 * b00) * id, (a20 * b03 - a21 * b01 + a22 * b00) * id);
}

// vector relational -> bvec
#define GLC_REL(NAME, OP)                                                                                     \
    template <class T, int N> GLC tvec<bool, N> NAME(const tvec<T, N>& a, const tvec<T, N>& b) { tvec<bool, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = a[i] OP b[i]; return r; }
GLC_REL(lessThan, <) GLC_REL(lessThanEqual, <=) GLC_REL(greaterThan, >) GLC_REL(greaterThanEqual, >=)
GLC_REL(equal, ==) GLC_REL(notEqual, !=)
template <int N> GLC bool any(const tvec<bool, N>& v) { bool e = false; _Pragma("unroll") for (int i = 0; i < N; ++i) e = e || v[i]; return e; }
template <int N> GLC bool all(const tvec<bool, N>& v) { bool e = true; _Pragma("unroll") for (int i = 0; i < N; ++i) e = e && v[i]; return e; }
template <int N> GLC tvec<bool, N> not_(const tvec<bool, N>& v) { tvec<bool, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = !v[i]; return r; }

// --------------------------------------------------------------------------------------- GLSL arrays
// value semantics like GLSL (copied on assignment / by-value parameters); arr.length() works.
template <class T, int N> struct _arr {
    T a[N];
    GLC _arr() {}
    template <class... A, class = _enable_if<(sizeof...(A) == N)>> GLC _arr(const A&... v) : a{T(v)...} {}
    GLC T& operator[](int i) { return a[_clampi(i, N)]; }   // out-of-range index clamped like WebGL/ANGLE
    GLC const T& operator[](int i) const { return a[_clampi(i, N)]; }
    GLC int length() const { return N; }
};

// ------------------------------------------------------------------------------------------ textures
// fmt: 0 = uchar4 (unorm8), 1 = float4.  wrap: 0 clamp-to-edge, 1 repeat, 2 mirrored repeat.
// filter: 0 nearest, 1 linear.  flip: 1 = row 0 of the buffer is the TOP of the image (our frames).
struct sampler2D {
    const void* p;
    int w, h, fmt, wrap, filter, flip;
};
typedef sampler2D samplerExternalOES;
typedef sampler2D sampler2DRect;

GLC int _wrapi(int i, int n, int mode) {
    if (mode == 1) { i %= n; return i < 0 ? i + n : i; }
    if (mode == 2) { int p = 2 * n; i %= p; if (i < 0) i += p; return i < n ? i : p - 1 - i; }
    return i < 0 ? 0 : (i >= n ? n - 1 : i);
}
GLC vec4 _texel(const sampler2D& s, int i, int j) {  // j counts up from the bottom (GL)
    int row = s.flip ? (s.h - 1 - j) : j;
    if (s.fmt == 1) {
        float4 v = __ldg(((const float4*)s.p) + (size_t)row * s.w + i);
        return vec4(v.x, v.y, v.z, v.w);
    }
    uchar4 v = __ldg(((const uchar4*)s.p) + (size_t)row * s.w + i);
    const float k = 1.f / 255.f;
    return vec4(v.x * k, v.y * k, v.z * k, v.w * k);
}
// Texel coordinates are clamped in FLOAT before the int conversion: an inf/NaN uv (e.g. a division by zero at
// progress = 0.5) must not reach integer arithmetic, where INT_MAX + 1 is undefined behaviour that the
// optimiser turns into an out-of-bounds load (seen with gl-transitions SimpleFlip).  fmaxf maps NaN to the bound.
GLC float _tc(float x) { return ::fminf(::fmaxf(x, -1.0e7f), 1.0e7f); }
GLC vec4 texture(const sampler2D& s, const vec2& uv) {
    if (s.p == nullptr) return vec4(0.f, 0.f, 0.f, 1.f);
    float u = _tc(uv.x * s.w), v = _tc(uv.y * s.h);
    if (s.filter == 0) {
        int i = _wrapi((int)::floorf(u), s.w, s.wrap), j = _wrapi((int)::floorf(v), s.h, s.wrap);
        return _texel(s, i, j);
    }
    u -= 0.5f; v -= 0.5f;
    float fu = ::floorf(u), fv = ::floorf(v);
    float tx = u - fu, ty = v - fv;
    int i0 = (int)fu, j0 = (int)fv;
    int i1 = _wrapi(i0 + 1, s.w, s.wrap), j1 = _wrapi(j0 + 1, s.h, s.wrap);
    i0 = _wrapi(i0, s.w, s.wrap); j0 = _wrapi(j0, s.h, s.wrap);
    vec4 a = _texel(s, i0, j0), b = _texel(s, i1, j0), c = _texel(s, i0, j1), d = _texel(s, i1, j1);
    return mix(mix(a, b, tx), mix(c, d, tx), ty);
}
GLC vec4 texture(const sampler2D& s, const vec2& uv, float /*bias*/) { return texture(s, uv); }
GLC vec4 texture2D(const sampler2D& s, const vec2& uv) { return texture(s, uv); }
GLC vec4 texture2D(const sampler2D& s, const vec2& uv, float) { return texture(s, uv); }
GLC vec4 textureLod(const sampler2D& s, const vec2& uv, float) { return texture(s, uv); }
GLC vec4 texture2DLod(const sampler2D& s, const vec2& uv, float) { return texture(s, uv); }
GLC vec4 textureGrad(const sampler2D& s, const vec2& uv, const vec2&, const vec2&) { return texture(s, uv); }
GLC vec4 textureProj(const sampler2D& s, const vec3& p) { return texture(s, vec2(p.x / p.z, p.y / p.z)); }
GLC vec4 texture2DProj(const sampler2D& s, const vec3& p) { return textureProj(s, p); }
GLC vec4 texture2DRect(const sampler2D& s, const vec2& px) { return texture(s, vec2(px.x / s.w, px.y / s.h)); }
GLC vec4 texelFetch(const sampler2D& s, const ivec2& p, int /*lod*/) {
    if (s.p == nullptr) return vec4(0.f, 0.f, 0.f, 1.f);
    return _texel(s, _wrapi(p.x, s.w, 0), _wrapi(p.y, s.h, 0));
}
GLC ivec2 textureSize(const sampler2D& s, int /*lod*/) { return ivec2(s.w, s.h); }

// ------------------------------------------------------------------------------------- derivatives
// 2x2 quads from a (16, 8) block: lane = tx + 16 * (ty & 1).  GL window y points UP, rows point down.
GLC float dFdx(float v) {
    unsigned m = __activemask();
    int lane = threadIdx.x + 16 * (threadIdx.y & 1);
    float o = __shfl_xor_sync(m, v, 1);
    if (!((m >> (lane ^ 1)) & 1u)) return 0.f;
    return (threadIdx.x & 1) ? v - o : o - v;
}
GLC float dFdy(float v) {
    unsigned m = __activemask();
    int lane = threadIdx.x + 16 * (threadIdx.y & 1);
    float o = __shfl_xor_sync(m, v, 16);
    if (!((m >> (lane ^ 16)) & 1u)) return 0.f;
    return (threadIdx.y & 1) ? o - v : v - o;
}
GLC float fwidth(float v) { return ::fabsf(dFdx(v)) + ::fabsf(dFdy(v)); }
template <int N> GLC tvec<float, N> dFdx(const tvec<float, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = dFdx(v[i]); return r; }
template <int N> GLC tvec<float, N> dFdy(const tvec<float, N>& v) { tvec<float, N> r; _Pragma("unroll") for (int i = 0; i < N; ++i) r[i] = dFdy(v[i]); return r; }
template <int N> GLC tvec<float, N> fwidth(const tvec<float, N>& v) { return abs(dFdx(v)) + abs(dFdy(v)); }
#define dFdxFine dFdx
#define dFdyFine dFdy
#define dFdxCoarse dFdx
#define dFdyCoarse dFdy

// ------------------------------------------------------------------------------------------- output
GLC uchar4 _pack_unorm8(const vec4& c) {
    // GL unorm conversion: round(clamp(c, 0, 1) * 255); NaN -> 0 (fmaxf returns the non-NaN operand)
    return make_uchar4((unsigned char)(::fminf(::fmaxf(c.x, 0.f), 1.f) * 255.f + 0.5f),
                       (unsigned char)(::fminf(::fmaxf(c.y, 0.f), 1.f) * 255.f + 0.5f),
                       (unsigned char)(::fminf(::fmaxf(c.z, 0.f), 1.f) * 255.f + 0.5f), 255);
}

}  // namespace glsl
#endif
