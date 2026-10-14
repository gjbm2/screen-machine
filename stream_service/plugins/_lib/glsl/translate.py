"""translate.py -- GLSL -> CUDA C++ translator (token based) for Shadertoy, gl-transitions and ISF shaders.

Model
-----
The output is ONE CUDA translation unit:

    glsl_compat.cuh                      vec/mat/sampler types and GLSL built-ins (namespace glsl)
    namespace glsl {
      struct _U { ...uniforms... };      POD; passed BY VALUE as the kernel argument (numpy structured scalar)
      struct _Shader : _U {              one instance per pixel (per CUDA thread)
        <dialect prelude>                gl_FragCoord, getFromColor(), IMG_PIXEL(), ...
        <user GLSL, translated>          globals -> data members, functions -> member functions,
                                         structs -> nested types, #define kept (undef'd afterwards)
      };
    }
    extern "C" __global__ void _g_main(...)   per-dialect entry: builds _Shader, calls mainImage/transition/main

Wrapping the shader in a struct gives GLSL semantics for free: mutable globals become per-pixel state (a
__device__ global would be shared by every thread), forward declarations are unnecessary (members can call
members declared later), and unqualified calls resolve to namespace glsl, never to CUDA's global math.

Source transforms (on tokens, never on raw text): drop #version/#extension/precision/layout and precision
qualifiers; float literals get an 'f' suffix (1.0 -> 1.0f, 1. -> 1.f, 1e-3 -> 1e-3f, 2.0lf -> 2.0f) so
nothing runs in fp64; multi-letter swizzles -> v._sw<0,1>() (read) / v._sr<0,1>() (write, a proxy);
'out'/'inout' params -> references; GLSL arrays (T a[N], T[N] a, T[N](...), T[](...)) -> glsl::_arr<T,N>
(value semantics like GLSL); 'const' scalar globals with literal initialisers -> static constexpr (usable as
array sizes); C++-only keywords used as identifiers are renamed; local variables that shadow a function
name are renamed; 'discard' sets a flag and returns.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPAT_HEADER = (HERE / "glsl_compat.cuh").read_text()

# ------------------------------------------------------------------------------------------- tokenizer

_LEX = re.compile(r"""
 (?P<nl>\n)
|(?P<ws>[ \t\r\f\v]+)
|(?P<cont>\\\r?\n)
|(?P<lc>//[^\n]*)
|(?P<bc>/\*.*?\*/)
|(?P<num>(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?(?:lf|LF|[fFhH])?|\d+[eE][+-]?\d+(?:lf|LF|[fFhH])?|0[xX][0-9a-fA-F]+[uU]?|\d+[uU]?)
|(?P<id>[A-Za-z_]\w*)
|(?P<op><<=|>>=|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||\^\^|\+=|-=|\*=|/=|%=|&=|\|=|\^=|\#\#|[-+*/%<>=!&|^~?:;,.(){}\[\]\#])
|(?P<other>.)
""", re.S | re.X)


@dataclass
class Tok:
    kind: str          # 'id' | 'num' | 'op' | 'other' | 'ppend'
    text: str
    pre: str = ""      # whitespace before the token (comments become spaces / newlines)
    line: int = 0
    pp: bool = False   # inside a preprocessor directive

    def __repr__(self):
        return f"{self.text!r}"


def tokenize(src: str) -> list[Tok]:
    """Significant tokens with their leading whitespace.  Preprocessor directives are tokenized too (so macro
    bodies get the same rewrites) and end with a 'ppend' token; backslash continuations are joined."""
    out: list[Tok] = []
    pre = []
    line = 1
    at_bol = True
    in_pp = False
    for m in _LEX.finditer(src):
        k, t = m.lastgroup, m.group()
        if k == "nl":
            if in_pp:
                out.append(Tok("ppend", "\n", "".join(pre), line, True))
                pre = []
                in_pp = False
            else:
                pre.append("\n")
            line += 1
            at_bol = True
            continue
        if k == "cont":
            line += 1
            pre.append(" ")
            continue
        if k in ("ws",):
            pre.append(t)
            continue
        if k in ("lc", "bc"):
            n = t.count("\n")
            line += n
            pre.append("\n" * n if (n and not in_pp) else " ")
            continue
        if k == "op" and t == "#" and at_bol and not in_pp:
            in_pp = True
        out.append(Tok(k, t, "".join(pre), line, in_pp))
        pre = []
        at_bol = False
    if in_pp:
        out.append(Tok("ppend", "\n", "", line, True))
    return out


def emit(toks: list[Tok]) -> str:
    """Tokens -> text; inserts a space where two word-like tokens would otherwise merge."""
    parts = []
    prev = None
    for t in toks:
        pre = t.pre
        if t.kind == "op" and t.text == "#" and t.pp and (not parts or not parts[-1].endswith("\n")):
            pre = "\n" + pre.lstrip("\n")
        if not pre and prev is not None and _wordy(prev.text) and _wordy(t.text):
            pre = " "
        parts.append(pre + t.text)
        prev = t
    return "".join(parts)


def _wordy(s: str) -> bool:
    return bool(s) and (s[-1].isalnum() or s[-1] == "_" or s[0].isalnum() or s[0] == "_")


def T(text: str, kind: str | None = None, pre: str = "") -> Tok:
    if kind is None:
        kind = "id" if re.match(r"[A-Za-z_]", text) else ("num" if re.match(r"[\d.]", text) else "op")
    return Tok(kind, text, pre)


# ------------------------------------------------------------------------------------------- vocabulary

SCALARS = {"void", "bool", "int", "uint", "float", "double"}
VECS = {f"{p}vec{n}" for p in ("", "i", "u", "b", "d") for n in (2, 3, 4)}
MATS = {f"mat{n}" for n in (2, 3, 4)} | {f"mat{a}x{b}" for a in (2, 3, 4) for b in (2, 3, 4)} | \
       {f"dmat{n}" for n in (2, 3, 4)}
SAMPLERS = {"sampler2D", "samplerCube", "sampler3D", "sampler2DRect", "samplerExternalOES", "sampler2DShadow"}
BUILTIN_TYPES = SCALARS | VECS | MATS | SAMPLERS
TYPE_MAP = {"double": "float", "dvec2": "vec2", "dvec3": "vec3", "dvec4": "vec4", "dmat2": "mat2",
            "dmat3": "mat3", "dmat4": "mat4"}
DROP_QUALIFIERS = {"highp", "mediump", "lowp", "precise", "invariant", "flat", "smooth", "noperspective",
                   "centroid", "coherent", "volatile", "restrict", "readonly", "writeonly", "patch", "sample"}
STORAGE = {"uniform", "varying", "attribute", "in", "out", "inout", "const", "buffer", "shared"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "^=", "|="}
SWZ_SETS = ("xyzw", "rgba", "stpq")

# C++ (and CUDA) words that are legal GLSL identifiers -> renamed
CXX_ONLY = {
    "new", "delete", "operator", "private", "protected", "public", "friend", "virtual", "explicit", "mutable",
    "register", "auto", "char", "signed", "typename", "typeid", "throw", "try", "catch", "wchar_t", "nullptr",
    "constexpr", "decltype", "noexcept", "static_assert", "thread_local", "alignas", "alignof", "char8_t",
    "char16_t", "char32_t", "concept", "requires", "co_await", "co_yield", "co_return", "consteval",
    "constinit", "export", "and", "or", "not", "xor", "bitand", "bitor", "compl", "and_eq", "or_eq", "xor_eq",
    "not_eq", "dynamic_cast", "static_cast", "const_cast", "reinterpret_cast", "this", "class", "template",
    "namespace", "using", "union", "enum", "typedef", "goto", "extern", "static", "inline", "long", "short",
    "unsigned", "sizeof", "asm", "default_", "threadIdx", "blockIdx", "blockDim", "gridDim", "warpSize",
    "float2", "float3", "float4", "int2", "int3", "int4", "uchar4", "dim3", "half", "NULL", "INFINITY", "NAN",
    "tvec", "tmat", "P", "glsl",
}
RENAME_FUNCS = {"not": "not_"}

# GLSL built-in function names provided by glsl_compat.cuh (a user function with one of these names hides
# the whole overload set inside the struct, so we emit forwarding templates for the builtin overloads)
BUILTIN_FUNCS = set("""radians degrees sin cos tan asin acos atan sinh cosh tanh asinh acosh atanh pow exp log
exp2 log2 sqrt inversesqrt abs sign floor ceil trunc round roundEven fract mod modf min max clamp mix step
smoothstep isnan isinf floatBitsToInt floatBitsToUint intBitsToFloat uintBitsToFloat fma length distance dot
cross normalize faceforward reflect refract matrixCompMult outerProduct transpose determinant inverse lessThan
lessThanEqual greaterThan greaterThanEqual equal notEqual any all not_ texture texture2D textureLod
texture2DLod textureGrad textureProj texelFetch textureSize dFdx dFdy fwidth atan2""".split())


class TranslateError(Exception):
    pass


# ------------------------------------------------------------------------------------------- helpers

def _match(toks: list[Tok], i: int) -> int:
    """Index of the bracket closing toks[i] ('(', '[' or '{')."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    o, c = toks[i].text, pairs[toks[i].text]
    d = 0
    for j in range(i, len(toks)):
        if toks[j].kind == "op":
            if toks[j].text == o:
                d += 1
            elif toks[j].text == c:
                d -= 1
                if d == 0:
                    return j
    raise TranslateError(f"unbalanced '{o}' at line {toks[i].line}")


def _split_commas(toks: list[Tok]) -> list[list[Tok]]:
    parts, cur, d = [], [], 0
    for t in toks:
        if t.kind == "op" and t.text in "([{":
            d += 1
        elif t.kind == "op" and t.text in ")]}":
            d -= 1
        if d == 0 and t.kind == "op" and t.text == ",":
            parts.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        parts.append(cur)
    return parts


def _swizzle(name: str) -> list[int] | None:
    if not (2 <= len(name) <= 4):
        return None
    for s in SWZ_SETS:
        if all(ch in s for ch in name):
            return [s.index(ch) for ch in name]
    return None


def _suffix_float(text: str) -> str:
    if text.startswith(("0x", "0X")):
        return text
    if text.endswith(("lf", "LF")):
        return text[:-2] + "f"
    if text[-1] in "hH":
        return text[:-1] + "f"
    if text[-1] in "fF":
        return text
    if "." in text or "e" in text or "E" in text:
        return text + "f"
    return text  # integer literal (incl. 1u)


@dataclass
class Uniform:
    name: str
    type: str          # GLSL type (bool kept as 'bool'; stored as int in _U)
    array: int | None = None
    default: object = None
    meta: dict = field(default_factory=dict)


@dataclass
class Translation:
    dialect: str
    cuda: str                      # full CUDA source (header included)
    entry: str                     # kernel name
    uniforms: list[Uniform]        # every _U field in order (dialect built-ins first)
    params: list[Uniform]          # user-facing parameters (subset of uniforms) with defaults
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------- translator

class Translator:
    """Translates one or more GLSL sources into the body of `struct _Shader : _U`."""

    def __init__(self, reserved_members: set[str] | None = None):
        self.structs: dict[str, list[tuple[str, str]]] = {}     # name -> [(type, field)]
        self.struct_fields: set[str] = set()
        self.functions: dict[str, list[list[str]]] = {}         # name -> [param qualifier lists]
        self.constexpr_names: set[str] = set()
        self.macro_consts: set[str] = set()
        self.macros: set[str] = set()
        self.uniforms: list[Uniform] = []
        self.globals: list[str] = []
        self.warnings: list[str] = []
        self.reserved = reserved_members or set()
        self.renames: dict[str, str] = {}          # declarations and expressions
        self.expr_renames: dict[str, str] = {}     # expressions only (const global arrays -> member function)
        self.global_names: set[str] = set()

    # ---------------------------------------------------------------- top level
    def translate(self, src: str, main_rename: str | None = None, skip_globals: set[str] | None = None) -> str:
        toks = tokenize(src)
        items = self._split_items(toks)
        self.global_names |= set(skip_globals or ())
        self._prescan(items)
        out = []
        for it in items:
            out.append(self._item(it, main_rename, skip_globals or set()))
        return "".join(out)

    def _split_items(self, toks: list[Tok]) -> list[list[Tok]]:
        items, cur, depth = [], [], 0
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.pp and not cur and depth == 0:
                j = i
                while toks[j].kind != "ppend":
                    j += 1
                items.append(toks[i:j + 1])
                i = j + 1
                continue
            cur.append(t)
            if t.kind == "op" and not t.pp:
                if t.text in "({[":
                    depth += 1
                elif t.text in ")}]":
                    depth -= 1
                if depth == 0 and t.text == ";":
                    items.append(cur)
                    cur = []
                elif depth == 0 and t.text == "}" and self._is_func_def(cur):
                    items.append(cur)
                    cur = []
            i += 1
        if any(not t.pp for t in cur):
            raise TranslateError(f"unterminated declaration at line {cur[0].line}: {emit(cur[:12])}")
        items.extend([cur] if cur else [])
        return items

    @staticmethod
    def _sig(toks):
        return [t for t in toks if not t.pp]

    def _is_func_def(self, cur: list[Tok]) -> bool:
        s = self._sig(cur)
        if not s or s[0].text == "struct":
            return False
        # first '{' at depth 0 preceded by ')'
        d = 0
        for k, t in enumerate(s):
            if t.kind != "op":
                continue
            if t.text == "{" and d == 0:
                return k > 0 and s[k - 1].text == ")"
            if t.text in "([":
                d += 1
            elif t.text in ")]":
                d -= 1
            elif t.text == "=" and d == 0:
                return False
        return False

    def _prescan(self, items):
        for it in items:
            s = self._sig(it)
            if not s:
                # preprocessor item: remember macros and numeric object-like macros
                txt = [t for t in it if t.kind != "ppend"]
                if len(txt) >= 3 and txt[1].text == "define":
                    name = txt[2].text
                    self.macros.add(name)
                    body = txt[3:]
                    is_fn = bool(body) and body[0].text == "(" and not body[0].pre
                    if not is_fn and body and all(t.kind == "num" or t.text in "()+-*/." for t in body):
                        self.macro_consts.add(name)
                continue
            # macros defined inside function bodies
            for k, t in enumerate(it):
                if t.pp and t.text == "define" and k + 1 < len(it):
                    self.macros.add(it[k + 1].text)
            s = self._strip_layout(s)
            if s and s[0].text == "struct" and len(s) > 1:
                name = s[1].text
                self.structs[name] = []
                lb = next(k for k, t in enumerate(s) if t.text == "{")
                body = s[lb + 1:_match(s, lb)]
                for decl in self._split_semis(body):
                    d = [t for t in decl if t.text not in DROP_QUALIFIERS]
                    if len(d) >= 2:
                        for part in _split_commas(d[1:]):
                            if part and part[0].kind == "id":
                                self.structs[name].append((d[0].text, part[0].text))
                                self.struct_fields.add(part[0].text)
                continue
            if self._is_func_def(s) or self._is_proto(s):
                name, params = self._func_header(s)
                quals = []
                for p in params:
                    quals.append([t.text for t in p if t.text in ("out", "inout")])
                self.functions.setdefault(name, []).append(quals)
                continue
            # global variable / uniform names (for scope-correct shadowing of locals)
            k = 0
            while k < len(s) and (s[k].text in STORAGE or s[k].text in DROP_QUALIFIERS):
                k += 1
            if k < len(s) and (s[k].text in BUILTIN_TYPES or s[k].text in self.structs):
                ty, rest = self._type_tokens([t for t in s[k:] if t.text != ";"])
                for part in _split_commas(rest):
                    if part and part[0].kind == "id":
                        self.global_names.add(part[0].text)

    @staticmethod
    def _split_semis(toks):
        parts, cur, d = [], [], 0
        for t in toks:
            if t.text in "([{":
                d += 1
            elif t.text in ")]}":
                d -= 1
            if d == 0 and t.text == ";":
                parts.append(cur)
                cur = []
            else:
                cur.append(t)
        if cur:
            parts.append(cur)
        return parts

    @staticmethod
    def _strip_layout(s):
        out, k = [], 0
        while k < len(s):
            if s[k].text == "layout" and k + 1 < len(s) and s[k + 1].text == "(":
                k = _match(s, k + 1) + 1
                continue
            out.append(s[k])
            k += 1
        return out

    def _is_proto(self, s) -> bool:
        if not s or s[-1].text != ";":
            return False
        d = 0
        for k, t in enumerate(s):
            if t.text == "=" and d == 0:
                return False
            if t.text == "(":
                if d == 0:
                    return k >= 2 and s[k - 1].kind == "id" and s[k - 1].text not in BUILTIN_TYPES \
                        and s[k - 1].text not in self.structs and (s[k - 2].kind == "id" or s[k - 2].text == "]")
                d += 1
            elif t.text == ")":
                d -= 1
        return False

    def _func_header(self, s):
        lp = next(k for k, t in enumerate(s) if t.text == "(")
        name = s[lp - 1].text
        rp = _match(s, lp)
        params = [p for p in _split_commas(s[lp + 1:rp])]
        if len(params) == 1 and len(params[0]) == 1 and params[0][0].text == "void":
            params = []
        return name, params

    # ---------------------------------------------------------------- items
    def _item(self, it: list[Tok], main_rename, skip_globals) -> str:
        s = self._sig(it)
        if not s:
            return self._pp(it)
        lead = it[0].pre if it else ""
        s = self._strip_layout(s)
        s = [t for t in s if not (t.kind == "id" and t.text in DROP_QUALIFIERS)]
        if not s:
            return ""
        if s[0].text == "precision":
            return ""
        if s[0].text == ";":
            return ""
        if s[0].text == "struct":
            return self._struct(s, lead)
        if self._is_func_def(s):
            return self._function(s, it, main_rename, lead)
        if self._is_proto(s):
            return ""  # members can be called before their definition
        return self._global(s, lead, skip_globals)

    def _pp(self, it: list[Tok]) -> str:
        txt = [t for t in it if t.kind != "ppend"]
        if len(txt) >= 2 and txt[1].text in ("version", "extension", "pragma", "line"):
            return ""
        if len(txt) >= 2 and txt[1].text == "define":
            body = self._rewrite_expr(txt[3:]) if len(txt) > 3 else []
            # keep '(' attached for function-like macros
            return "\n" + emit(txt[:3] + body) + "\n"
        return "\n" + emit(txt) + "\n"

    def _struct(self, s, lead) -> str:
        name = s[1].text
        lb = next(k for k, t in enumerate(s) if t.text == "{")
        rb = _match(s, lb)
        fields = []
        for decl in self._split_semis(s[lb + 1:rb]):
            d = [t for t in decl if t.text not in DROP_QUALIFIERS]
            if not d:
                continue
            ty, rest = self._type_tokens(d)
            for part in _split_commas(rest):
                nm, arr, _ = self._declarator(part, ty)
                fields.append(f"    {self._decl_type(ty, arr)} {self._ident(nm)};")
        out = f"{lead}struct {name} {{\n" + "\n".join(fields) + "\n};\n"
        tail = s[rb + 1:]
        if tail and tail[0].text != ";":  # struct S {...} s1, s2;
            out += self._global([T(name)] + tail, "", set())
        return out

    def _type_tokens(self, d):
        """Split [type tokens] [rest]; handles 'T[N] name'."""
        ty = [d[0]]
        k = 1
        if k < len(d) and d[k].text == "[":
            e = _match(d, k)
            ty = d[:e + 1]
            k = e + 1
        return ty, d[k:]

    def _declarator(self, part, ty):
        """name [N] = init  ->  (name, array_size_text|None|'', init_tokens|None)"""
        nm = part[0].text
        arr = None
        k = 1
        if len(ty) > 1:  # T[N] name
            arr = emit(self._rewrite_expr(ty[2:-1])).strip()
        if k < len(part) and part[k].text == "[":
            e = _match(part, k)
            arr = emit(self._rewrite_expr(part[k + 1:e])).strip()
            k = e + 1
        init = None
        if k < len(part) and part[k].text == "=":
            init = part[k + 1:]
        if arr == "" and init is not None:  # T a[] = T[](...)
            arr = str(self._count_array_init(init))
        return nm, arr, init

    def _count_array_init(self, init):
        k = next((i for i, t in enumerate(init) if t.text == "("), None)
        if k is None:
            if init and init[0].text == "{":
                return len(_split_commas(init[1:_match(init, 0)]))
            raise TranslateError("cannot size unsized array")
        return len(_split_commas(init[k + 1:_match(init, k)]))

    def _ctype(self, ty: str) -> str:
        return TYPE_MAP.get(ty, ty)

    def _decl_type(self, ty_toks, arr) -> str:
        base = self._ctype(ty_toks[0].text)
        if arr is not None:
            return f"_arr<{base}, {arr}>"
        return base

    def _ident(self, name: str) -> str:
        if name in self.renames:
            return self.renames[name]
        if name in CXX_ONLY:
            return name + "_"
        return name

    # ---------------------------------------------------------------- globals
    def _global(self, s, lead, skip_globals) -> str:
        quals = []
        k = 0
        while k < len(s) and s[k].text in STORAGE:
            quals.append(s[k].text)
            k += 1
        d = s[k:]
        if d and d[-1].text == ";":
            d = d[:-1]
        if not d:
            return ""
        if d[0].text not in BUILTIN_TYPES and d[0].text not in self.structs:
            # not a declaration we understand (e.g. a macro invocation at file scope): pass through
            return lead + emit(self._rewrite_expr(s)) + "\n"
        ty, rest = self._type_tokens(d)
        out = []
        for part in _split_commas(rest):
            if not part:
                continue
            nm, arr, init = self._declarator(part, ty)
            if nm in skip_globals:
                continue
            if "uniform" in quals:
                self.uniforms.append(Uniform(nm, self._ctype(ty[0].text), int(arr) if arr and arr.isdigit() else None))
                continue
            cname = self._ident(nm)
            self.globals.append(nm)
            tname = self._decl_type(ty, arr)
            if arr is not None and "const" in quals and init is not None:
                # A dynamically indexed array member would force the whole per-pixel struct into local memory;
                # as a by-value member function the array is a local temporary the compiler folds / selects.
                init_s = emit(self._rewrite_expr(init)).strip()
                fn = f"_gca_{nm}"
                self.expr_renames[nm] = fn + "()"
                out.append(f"    __device__ {tname} {fn}() const {{ return {init_s}; }}")
                continue
            if init is not None:
                init_s = emit(self._rewrite_expr(init)).strip()
                if "const" in quals and arr is None and ty[0].text in ("int", "uint", "float", "bool") \
                        and self._literal_expr(init):
                    self.constexpr_names.add(nm)
                    out.append(f"    static constexpr {tname} {cname} = {init_s};")
                else:
                    c = "const " if "const" in quals else ""
                    out.append(f"    {c}{tname} {cname} = {init_s};")
            else:
                out.append(f"    {tname} {cname}{{}};")
        return (lead if out else "") + "\n".join(out) + ("\n" if out else "")

    def _literal_expr(self, init) -> bool:
        for t in init:
            if t.kind == "num":
                continue
            if t.kind == "op" and t.text in "()+-*/%<<>>|&^~":
                continue
            if t.kind == "id" and (t.text in self.constexpr_names or t.text in self.macro_consts
                                   or t.text in ("true", "false")):
                continue
            if t.kind == "id" and t.text in ("int", "float", "uint", "bool"):
                continue
            return False
        return True

    # ---------------------------------------------------------------- functions
    def _function(self, s, it, main_rename, lead) -> str:
        lp = next(k for k, t in enumerate(s) if t.text == "(")
        rp = _match(s, lp)
        head = [t for t in s[:lp - 1] if t.text not in STORAGE]
        name = s[lp - 1].text
        if name == "main" and main_rename:
            name = main_rename
        ret = self._ctype(head[0].text) if head else "void"
        if len(head) > 1 and head[1].text == "[":
            ret = f"_arr<{ret}, {emit(head[2:-1]).strip()}>"
        params = []
        local_names = set()
        for p in _split_commas(s[lp + 1:rp]):
            p = [t for t in p if t.text not in DROP_QUALIFIERS]
            if len(p) == 1 and p[0].text == "void":
                continue
            q = [t.text for t in p if t.text in ("in", "out", "inout", "const")]
            p = [t for t in p if t.text not in ("in", "out", "inout", "const")]
            ty, rest = self._type_tokens(p)
            nm, arr, _ = self._declarator(rest, ty) if rest else (f"_unnamed{len(params)}", None, None)
            if len(ty) > 1 and not rest:
                arr = emit(ty[2:-1]).strip()
            tname = self._decl_type(ty, arr)
            ref = "&" if ("out" in q or "inout" in q) else ""
            const = "const " if "const" in q and not ref else ""
            local_names.add(nm)
            params.append(f"{const}{tname}{ref} {self._ident(nm)}")
        lb = rp + 1
        body = s[lb:]
        # pp tokens inside the body are kept verbatim: use the original token list for the body
        start = next(i for i, x in enumerate(it) if x is s[lb])
        body_full = it[start:]
        body_out = self._rewrite_body(body_full, ret, local_names)
        return f"{lead}    __device__ {ret} {self._ident(name)}({', '.join(params)}) {emit(body_out)}\n"

    # ---------------------------------------------------------------- statements / expressions
    def _rewrite_body(self, toks, ret, params) -> list[Tok]:
        toks = self._shadow_renames(toks)
        toks = self._loop_guard(toks)
        toks = self._rewrite_decls(toks)
        return self._rewrite_expr(toks, ret=ret)

    @staticmethod
    def _loop_guard(toks: list[Tok]) -> list[Tok]:
        """Every loop body starts with ``if (++_g_iters > LOOP_CAP) break;``: one per-pixel budget for the
        whole shader, so a loop that never ends in CUDA (float conditions, NaNs) cannot hang the render thread.
        Bodies without braces are wrapped up to their ';'.  do { } while (c); keeps its trailing while."""
        guard = [Tok("other", " if (++_g_iters > _G_LOOP_CAP || ((_g_iters & 63) == 0 && clock64() - _g_t0 > "
                              "_G_CYCLE_CAP && (_g_iters = _G_LOOP_CAP + 1))) break"), Tok("op", ";")]   # real ';':
        # _rewrite_decls must still see the next statement start
        do_whiles = set()
        for k, t in enumerate(toks):
            if t.text == "do" and t.kind == "id" and k + 1 < len(toks) and toks[k + 1].text == "{" and not t.pp:
                e = _match(toks, k + 1)
                if e + 1 < len(toks) and toks[e + 1].text == "while":
                    do_whiles.add(e + 1)
        out: list[Tok] = []
        k, n = 0, len(toks)
        pending_close: list[int] = []       # token indices after which to insert a '}' (wrapped bodies)
        while k < n:
            t = toks[k]
            out.append(t)
            if t.kind == "id" and not t.pp and (t.text in ("for", "while") and k not in do_whiles) and \
                    k + 1 < n and toks[k + 1].text == "(":
                e = _match(toks, k + 1)
                out.extend(toks[k + 1:e + 1])
                k = e + 1
                if k < n and toks[k].text == "{":
                    out.append(toks[k])
                    out.extend(guard)
                    k += 1
                elif k < n and toks[k].text != ";":
                    # single statement: find its ';' at depth 0 (a nested loop/if header is skipped first)
                    j, d = k, 0
                    while j < n:
                        x = toks[j].text
                        if x in "([{":
                            d += 1
                        elif x in ")]}":
                            d -= 1
                        elif x == ";" and d == 0:
                            break
                        j += 1
                    if j < n and not any(tk.text in ("{", "}") or tk.pp for tk in toks[k:j]):
                        out.append(Tok("op", "{", " "))
                        out.extend(guard)
                        out.extend(toks[k:j + 1])
                        out.append(Tok("op", "}", " "))
                        k = j + 1
                continue
            if t.text == "do" and t.kind == "id" and not t.pp and k + 1 < n and toks[k + 1].text == "{":
                out.append(toks[k + 1])
                out.extend(guard)
                k += 2
                continue
            k += 1
        return out

    def _shadow_renames(self, toks):
        """GLSL scoping on top of C++: a local declared with the name of a global, uniform or function is
        renamed from the END of its declarator to the end of its block.  This reproduces GLSL's rule that a
        declaration's scope starts after its initializer (`vec4 img = IMG_PIXEL(img, p);` reads the uniform),
        and lets a local hide a function name as GLSL allows."""
        outer = set(self.functions) | BUILTIN_FUNCS | self.global_names | set(self.expr_renames)
        out = list(toks)
        scopes: list[dict[str, str]] = [{}]
        pending: list[tuple[int, str, str]] = []   # (activate at index, name, new)
        prev_sig = "{"
        k = 0
        n = len(out)
        paren_for = False
        while k < n:
            t = out[k]
            for item in [p for p in pending if p[0] == k]:
                scopes[-1][item[1]] = item[2]
                pending.remove(item)
            if t.pp:
                k += 1
                continue
            if t.text == "{":
                scopes.append({})
            elif t.text == "}":
                if len(scopes) > 1:
                    scopes.pop()
            stmt_start = prev_sig in ("{", "}", ";") or (prev_sig == "(" and paren_for)
            paren_for = t.text == "(" and k > 0 and out[k - 1].text == "for"
            if paren_for:
                scopes.append({})   # for-init scope, popped at the matching ')'... approximated: popped at '}'/';'
                scopes.pop()
            if stmt_start:
                j = k
                while j < n and (out[j].text == "const" or out[j].text in DROP_QUALIFIERS):
                    j += 1
                if j + 1 < n and self._is_type(out[j]) and (out[j + 1].kind == "id" or out[j + 1].text == "["):
                    j += 1
                    if out[j].text == "[":
                        j = _match(out, j) + 1
                    # declarators until ';' at depth 0 (or ')' closing a for-init)
                    d = 0
                    first = True
                    while j < n:
                        if first or (out[j].text == "," and d == 0):
                            if not first:
                                j += 1
                            first = False
                            if j < n and out[j].kind == "id" and out[j].text in outer:
                                name = out[j].text
                                new = f"{name}_l"
                                out[j] = Tok("id", new, out[j].pre, out[j].line, out[j].pp)
                                # find end of this declarator
                                e, dd = j + 1, 0
                                while e < n:
                                    x = out[e].text
                                    if x in "([{":
                                        dd += 1
                                    elif x in ")]}":
                                        if dd == 0:
                                            break
                                        dd -= 1
                                    elif dd == 0 and x in (",", ";"):
                                        break
                                    e += 1
                                pending.append((e, name, new))
                        x = out[j].text if j < n else ";"
                        if x in "([{":
                            d += 1
                        elif x in ")]}":
                            if d == 0:
                                break
                            d -= 1
                        elif x == ";" and d == 0:
                            break
                        j += 1
            if t.kind == "id" and not t.text.endswith("_l"):
                prv = out[k - 1] if k > 0 else None
                nxt = out[k + 1] if k + 1 < n else None
                if not (prv is not None and prv.text == "."):
                    for sc in reversed(scopes):
                        if t.text in sc:
                            is_call = nxt is not None and nxt.text == "(" and t.text in (set(self.functions) | BUILTIN_FUNCS)
                            if not is_call:
                                out[k] = Tok("id", sc[t.text], t.pre, t.line, t.pp)
                            break
            prev_sig = out[k].text
            k += 1
        return out

    def _is_type(self, t: Tok) -> bool:
        return t.kind == "id" and (t.text in BUILTIN_TYPES or t.text in self.structs)

    def _rewrite_decls(self, toks) -> list[Tok]:
        """Declarations at statement starts: arrays -> _arr<T,N>; multi-declarator array statements are split."""
        out = []
        k = 0
        prev_sig = "{"
        paren_for = 0
        while k < len(toks):
            t = toks[k]
            if t.pp:
                out.append(t)
                k += 1
                continue
            stmt_start = prev_sig in ("{", "}", ";") or (prev_sig == "(" and paren_for)
            paren_for = 1 if (t.text == "(" and out and out[-1].text == "for") else 0
            if stmt_start:
                j = k
                quals = []
                while j < len(toks) and (toks[j].text in ("const",) or toks[j].text in DROP_QUALIFIERS):
                    if toks[j].text == "const":
                        quals.append(toks[j])
                    j += 1
                if j < len(toks) and self._is_type(toks[j]) and j + 1 < len(toks) and \
                        (toks[j + 1].kind == "id" or toks[j + 1].text == "["):
                    # find end of declaration (';' at depth 0)
                    e = j
                    d = 0
                    while e < len(toks):
                        x = toks[e]
                        if x.text in "([{":
                            d += 1
                        elif x.text in ")]}":
                            d -= 1
                        if d == 0 and x.text == ";":
                            break
                        e += 1
                    decl = [x for x in toks[j:e] if not x.pp]
                    has_arr = any(x.text == "[" for x in decl[:3]) or self._decl_has_array(decl)
                    if has_arr:
                        ty, rest = self._type_tokens(decl)
                        parts = _split_commas(rest)
                        stmts = []
                        for part in parts:
                            nm, arr, init = self._declarator(part, ty)
                            tname = self._decl_type(ty, arr)
                            q = "const " if quals else ""
                            if init is not None:
                                stmts.append(f"{q}{tname} {self._ident(nm)} = {emit(self._rewrite_expr(init)).strip()}")
                            else:
                                stmts.append(f"{q}{tname} {self._ident(nm)}")
                        txt = "; ".join(stmts)
                        out.append(Tok("other", txt, t.pre, t.line))
                        k = e  # the ';' is emitted next
                        prev_sig = "x"
                        continue
            out.append(t)
            prev_sig = t.text
            k += 1
        return out

    def _decl_has_array(self, decl):
        d = 0
        for k, x in enumerate(decl):
            if x.text in "([{":
                if x.text == "[" and d == 0 and k > 0 and decl[k - 1].kind == "id":
                    # name[ ... ] in declarator position (not inside an initializer)
                    eq = next((i for i, y in enumerate(decl) if y.text == "="), len(decl))
                    if k < eq:
                        return True
                d += 1
            elif x.text in ")]}":
                d -= 1
        return False

    def _rewrite_expr(self, toks, ret: str | None = None) -> list[Tok]:
        out: list[Tok] = []
        k = 0
        n = len(toks)
        while k < n:
            t = toks[k]
            if t.kind == "num":
                out.append(Tok("num", _suffix_float(t.text), t.pre, t.line, t.pp))
            elif t.kind == "id":
                nxt = toks[k + 1] if k + 1 < n else None
                prv = out[-1] if out else None
                if t.text in DROP_QUALIFIERS:
                    if out or k + 1 < n:
                        # keep whitespace
                        if k + 1 < n:
                            toks[k + 1] = Tok(toks[k + 1].kind, toks[k + 1].text, t.pre + toks[k + 1].pre,
                                              toks[k + 1].line, toks[k + 1].pp)
                    k += 1
                    continue
                if t.text == "discard":
                    r = "return;" if (ret in (None, "void")) else f"return {ret}();"
                    out.append(Tok("other", f"{{ _g_discard = true; {r} }}", t.pre, t.line, t.pp))
                    if nxt is not None and nxt.text == ";":
                        k += 2
                        continue
                    k += 1
                    continue
                if self._is_type(t) and nxt is not None and nxt.text == "[" and (prv is None or prv.text != "."):
                    e = _match(toks, k + 1)
                    if e + 1 < n and toks[e + 1].text == "(":
                        size = emit(self._rewrite_expr(toks[k + 2:e])).strip()
                        if not size:
                            size = str(len(_split_commas(toks[e + 2:_match(toks, e + 1)])))
                        out.append(Tok("other", f"_arr<{self._ctype(t.text)}, {size}>", t.pre, t.line, t.pp))
                        k = e + 1
                        continue
                if prv is not None and prv.text == "." and prv.kind == "op":
                    sw = _swizzle(t.text) if t.text not in self.struct_fields else None
                    if sw is not None:
                        write = nxt is not None and (nxt.text in ASSIGN_OPS or nxt.text in ("++", "--"))
                        fn = f"_r{len(sw)}" if write else f"_s{len(sw)}"
                        code = sum(c << (2 * j) for j, c in enumerate(sw))
                        out.append(Tok("other", f"{fn}<{code}>()", "", t.line, t.pp))
                        k += 1
                        continue
                    out.append(t)  # member access: .x / .length / struct field
                    k += 1
                    continue
                if nxt is not None and nxt.text == "(" and t.text in self.functions and \
                        not (prv is not None and prv.text == "."):
                    outs = {i for ql in self.functions[t.text] for i, q in enumerate(ql) if q}
                    if outs:   # swizzles passed to out/inout params become copy-in/copy-out proxies
                        e = _match(toks, k + 1)
                        parts = []
                        for i, arg in enumerate(_split_commas(toks[k + 2:e])):
                            a_s = emit(self._rewrite_expr(list(arg)))
                            if i in outs:
                                a_s = re.sub(r"\._s([234])<(\d+)>\(\)(\s*)$", r"._w\1<\2>()\3", a_s)
                            parts.append(a_s)
                        out.append(Tok("other", f"{self._ident(t.text)}({','.join(parts)})", t.pre, t.line, t.pp))
                        k = e + 1
                        continue
                name = RENAME_FUNCS.get(t.text) if (nxt is not None and nxt.text == "(") else None
                if name is None and t.text in self.expr_renames and not (prv is not None and self._is_type(prv)):
                    name = self.expr_renames[t.text]
                if name is None:
                    name = self._ident(t.text)
                out.append(Tok("id", self._ctype(name) if name in TYPE_MAP else name, t.pre, t.line, t.pp))
            elif t.kind == "op" and t.text == "^^":
                out.append(Tok("op", "!=", t.pre, t.line, t.pp))
            else:
                out.append(t)
            k += 1
        return out

    # ---------------------------------------------------------------- forwarders
    def forwarders(self) -> str:
        """User functions named like a GLSL built-in hide the built-in overloads inside the struct: forward
        the remaining overloads explicitly (a user's exact-match overload still wins)."""
        out = []
        for name in sorted(set(self.functions) & BUILTIN_FUNCS):
            out.append(f"    template <class... _A> __device__ auto {name}(const _A&... _a) -> "
                       f"decltype(::glsl::{name}(_a...)) {{ return ::glsl::{name}(_a...); }}")
        return "\n".join(out) + ("\n" if out else "")

    def undefs(self) -> str:
        return "".join(f"#undef {m}\n" for m in sorted(self.macros))


# ------------------------------------------------------------------------------------------- _U struct

def _ufield(u: Uniform) -> str:
    ty = {"bool": "int"}.get(u.type, u.type)
    if u.array:
        return f"    _arr<{ty}, {u.array}> {u.name};"
    return f"    {ty} {u.name};"


def _u_struct(uniforms: list[Uniform]) -> str:
    return "struct _U {\n" + "\n".join(_ufield(u) for u in uniforms) + "\n    int _g_pad_;\n};\n"


KERNEL_HEAD = r"""
extern "C" __global__ void __launch_bounds__(128) _g_main(const glsl::_U _gU, void* __restrict__ _gOut, const int _gW, const int _gH, const int _gFmt) {
    // blockDim must be (16, 8): see derivatives in glsl_compat.cuh
    const int _gx = blockIdx.x * 16 + threadIdx.x, _gy = blockIdx.y * 8 + threadIdx.y;
    const int _gxc = _gx < _gW ? _gx : _gW - 1, _gyc = _gy < _gH ? _gy : _gH - 1;
    const float _gfx = _gxc + 0.5f, _gfy = (_gH - 1 - _gyc) + 0.5f;   // GL window coords: y up
    glsl::_Shader _gS(_gU);
    _gS._g_t0 = clock64();
    _gS.gl_FragCoord = glsl::vec4(_gfx, _gfy, 0.5f, 1.0f);
"""
KERNEL_TAIL = r"""
    if (_gx < _gW && _gy < _gH && !_gS._g_discard) {
        if (_gFmt == 1) { ((float4*)_gOut)[_gy * _gW + _gx] = make_float4(_gc.x, _gc.y, _gc.z, _gc.w); }
        else { ((uchar4*)_gOut)[_gy * _gW + _gx] = glsl::_pack_unorm8(_gc); }
    }
}
"""

COMMON_PRELUDE = """    __device__ _Shader(const _U& _gu) : _U(_gu) {}
    vec4 gl_FragCoord;
    vec4 gl_FragColor;
    vec4 gl_Position;
    bool _g_discard = false;
    int _g_iters = 0;
    long long _g_t0 = 0;
"""


def _assemble(uniforms, prelude, tr: Translator, body: str, kernel_body: str) -> str:
    return (COMPAT_HEADER + "\nnamespace glsl {\n" + _u_struct(uniforms) +
            "struct _Shader : _U {\n" + COMMON_PRELUDE + prelude + tr.forwarders() +
            "    // ---- translated GLSL ----\n" + body + "\n};\n}  // namespace glsl\n" + tr.undefs() +
            KERNEL_HEAD + kernel_body + KERNEL_TAIL)


# ------------------------------------------------------------------------------------------- defaults

def parse_glsl_value(ty: str, s: str):
    s = s.strip().rstrip(";").strip()
    if ty == "bool":
        return s == "true"
    m = re.match(r"^(?:[iub]?vec\d|mat\d|float|int)\s*\((.*)\)$", s, re.S)
    if m:
        vals = [v.strip() for v in m.group(1).split(",")]
        nums = [float(v.rstrip("fF")) if re.match(r"^[-+]?[\d.]", v) else (1.0 if v == "true" else 0.0) for v in vals]
        n = {"vec2": 2, "vec3": 3, "vec4": 4, "ivec2": 2, "ivec3": 3, "ivec4": 4, "bvec2": 2, "bvec3": 3, "bvec4": 4}.get(ty, 1)
        if len(nums) == 1 and n > 1:
            nums = nums * n
        if ty.startswith("i"):
            nums = [int(v) for v in nums]
        return nums if n > 1 else nums[0]
    try:
        v = float(s.rstrip("fF"))
        return int(v) if ty == "int" else v
    except ValueError:
        return None


# ------------------------------------------------------------------------------------------- dialects

def translate_gl_transition(src: str, name: str = "transition") -> Translation:
    """gl-transitions spec v1: vec4 transition(vec2 uv); getFromColor/getToColor; progress, ratio."""
    params = _gl_transition_params(src)
    tr = Translator()
    body = tr.translate(src)
    builtins = [Uniform("progress", "float"), Uniform("ratio", "float"), Uniform("from", "sampler2D"),
                Uniform("to", "sampler2D")]
    uni = builtins + tr.uniforms
    for u in tr.uniforms:
        p = params.get(u.name)
        if p is not None:
            u.default, u.meta = p["default"], p
    prelude = """    __device__ vec4 getFromColor(vec2 uv) { return texture(from, uv); }
    __device__ vec4 getToColor(vec2 uv) { return texture(to, uv); }
"""
    kernel = "    const glsl::vec4 _gc = _gS.transition(glsl::vec2(_gfx / _gW, _gfy / _gH));\n"
    meta = _gl_transition_meta(src)
    meta["textures"] = [u.name for u in tr.uniforms if u.type == "sampler2D"]
    return Translation("gl-transition", _assemble(uni, prelude, tr, body, kernel), "_g_main", uni,
                       [u for u in tr.uniforms if u.type != "sampler2D"], meta, tr.warnings)


_UNIFORM_STATEMENT = re.compile(r"uniform\s+(bool|int|float|vec[234]|ivec[234]|mat[234]|sampler2D)\s+([^;\n]+);([^\n]*)")


def _gl_transition_params(src: str) -> dict:
    """Mirror of gl-transitions scripts/catalog/parse-transition.mjs: `// = value` and `/* = value */` defaults,
    plus the @range/@color/@param hint line directly above a uniform."""
    out = {}
    lines = src.split("\n")
    for m in _UNIFORM_STATEMENT.finditer(src):
        ty, decls, rest = m.group(1), m.group(2), m.group(3)
        if ty == "sampler2D":
            continue
        block = []

        def keep(mm):
            block.append(mm.group(1))
            return f"@{len(block) - 1}"
        names = re.sub(r"/\*\s*=\s*(.*?)\s*\*/", keep, decls)
        line_default = re.match(r"^\s*//\s*=\s*(.+?)(?:\s*;.*)?$", rest)
        lineno = src[:m.start()].count("\n")
        hint = lines[lineno - 1].strip() if lineno > 0 else ""
        hints = {}
        hm = re.match(r"//\s*@range\(\s*([^,]+),\s*([^,)]+)(?:,\s*([^)]+))?\)\s*(.*)", hint)
        if hm:
            hints = {"min": float(hm.group(1)), "max": float(hm.group(2)),
                     "step": float(hm.group(3)) if hm.group(3) else None, "description": hm.group(4)}
        elif re.match(r"//\s*@color", hint):
            hints = {"color": True, "description": hint.split("@color", 1)[1].strip()}
        elif re.match(r"//\s*@param", hint):
            hints = {"description": hint.split("@param", 1)[1].strip()}
        for d in names.split(","):
            dm = re.match(r"^\s*(\w+)\s*(?:@(\d+))?\s*$", d)
            if not dm:
                continue
            dv = block[int(dm.group(2))] if dm.group(2) is not None else (line_default.group(1) if line_default else None)
            if dv is None:
                continue
            out[dm.group(1)] = {"type": ty, "default": parse_glsl_value(ty, dv), **hints}
    return out


def _gl_transition_meta(src: str) -> dict:
    meta = {}
    for key in ("Author", "License", "Description", "Tags"):
        m = re.search(rf"//\s*{key}\s*:\s*(.+)", src, re.I)
        if m:
            meta[key.lower()] = m.group(1).strip()
    meta.setdefault("license", "MIT")  # repo LICENSE: "When no license is specified, ... covered by this MIT license"
    return meta


SHADERTOY_UNIFORMS = [
    Uniform("iResolution", "vec3"), Uniform("iTime", "float"), Uniform("iTimeDelta", "float"),
    Uniform("iFrameRate", "float"), Uniform("iFrame", "int"), Uniform("iChannelTime", "float", 4),
    Uniform("iChannelResolution", "vec3", 4), Uniform("iMouse", "vec4"), Uniform("iDate", "vec4"),
    Uniform("iSampleRate", "float"),
    Uniform("iChannel0", "sampler2D"), Uniform("iChannel1", "sampler2D"), Uniform("iChannel2", "sampler2D"),
    Uniform("iChannel3", "sampler2D"),
]


def translate_shadertoy(src: str, name: str = "shadertoy") -> Translation:
    """Shadertoy 'Image' pass: void mainImage(out vec4 fragColor, in vec2 fragCoord)."""
    tr = Translator()
    body = tr.translate(src)
    uni = SHADERTOY_UNIFORMS + tr.uniforms
    kernel = ("    glsl::vec4 _gc(0.f, 0.f, 0.f, 1.f);\n"
              "    _gS.mainImage(_gc, glsl::vec2(_gfx, _gfy));\n")
    return Translation("shadertoy", _assemble(uni, "", tr, body, kernel), "_g_main", uni, tr.uniforms, {}, tr.warnings)


ISF_BUILTINS = [Uniform("TIME", "float"), Uniform("TIMEDELTA", "float"), Uniform("FRAMEINDEX", "int"),
                Uniform("DATE", "vec4"), Uniform("RENDERSIZE", "vec2"), Uniform("PASSINDEX", "int")]
ISF_TYPE = {"float": "float", "bool": "bool", "event": "bool", "long": "int", "color": "vec4",
            "point2D": "vec2", "image": "sampler2D", "audio": "sampler2D", "audioFFT": "sampler2D"}


def parse_isf(fs_src: str) -> tuple[dict, str]:
    m = re.search(r"/\*\s*(\{.*?\})\s*\*/", fs_src, re.S)
    if not m:
        raise TranslateError("ISF: no JSON header")
    meta = json.loads(m.group(1))
    return meta, fs_src[m.end():]


def translate_isf(fs_src: str, vs_src: str | None = None, name: str = "isf") -> Translation:
    """ISF 2.0: JSON header (INPUTS, PASSES, IMPORTED) + GLSL main() writing gl_FragColor."""
    meta, code = parse_isf(fs_src)
    inputs = []
    for inp in meta.get("INPUTS", []):
        ty = ISF_TYPE.get(inp.get("TYPE"))
        if ty is None:
            raise TranslateError(f"ISF input type {inp.get('TYPE')!r} unsupported")
        inputs.append(Uniform(inp["NAME"], ty, None, inp.get("DEFAULT"), inp))
    targets = []
    for p in meta.get("PASSES", []) or []:
        if p.get("TARGET"):
            targets.append(Uniform(p["TARGET"], "sampler2D", None, None, p))
    imported = [Uniform(k, "sampler2D", None, None, v) for k, v in (meta.get("IMPORTED") or {}).items()] \
        if isinstance(meta.get("IMPORTED"), dict) else \
        [Uniform(v["NAME"], "sampler2D", None, None, v) for v in (meta.get("IMPORTED") or [])]
    tr = Translator()
    declared = {u.name for u in inputs + targets + imported}
    body = ""
    if vs_src:
        vs_code = vs_src
        body += tr.translate(vs_code, main_rename="_vs_main", skip_globals=declared)
        vs_globals = set(tr.globals)
        body += tr.translate(code, skip_globals=declared | vs_globals)
    else:
        body += tr.translate(code, skip_globals=declared)
    images = [u for u in inputs + targets + imported if u.type == "sampler2D"]
    uni = ISF_BUILTINS + inputs + targets + imported + tr.uniforms
    pre = ["    vec2 isf_FragNormCoord;",
           "    __device__ vec4 IMG_NORM_PIXEL(const sampler2D& s, vec2 uv) { return texture(s, uv); }",
           "    __device__ vec4 IMG_PIXEL(const sampler2D& s, vec2 px) { return texture(s, vec2(px.x / s.w, px.y / s.h)); }",
           "    __device__ vec4 IMG_THIS_PIXEL(const sampler2D& s) { return IMG_PIXEL(s, vec2(gl_FragCoord.x, gl_FragCoord.y)); }",
           "    __device__ vec4 IMG_THIS_NORM_PIXEL(const sampler2D& s) { return texture(s, isf_FragNormCoord); }",
           "    __device__ vec2 IMG_SIZE(const sampler2D& s) { return vec2((float)s.w, (float)s.h); }",
           "    __device__ vec4 IMG_NORM_PIXEL_2D(const sampler2D& s, vec2, vec2 uv) { return texture(s, uv); }",
           "    __device__ vec4 IMG_PIXEL_2D(const sampler2D& s, vec2, vec2 px) { return IMG_PIXEL(s, px); }",
           "    __device__ void isf_vertShaderInit() { gl_Position = vec4(isf_FragNormCoord * 2.f - 1.f, 0.f, 1.f); }",
           "    __device__ void vv_vertShaderInit() { isf_vertShaderInit(); }",
           "#define vv_FragNormCoord isf_FragNormCoord"]
    for im in images:
        pre.append(f"    vec2 _{im.name}_imgSize = vec2((float){im.name}.w, (float){im.name}.h);")
        pre.append(f"    vec4 _{im.name}_imgRect = vec4(0.f, 0.f, (float){im.name}.w, (float){im.name}.h);")
        pre.append(f"    bool _{im.name}_flip = false;")
    tr.macros.add("vv_FragNormCoord")
    kernel = ("    _gS.isf_FragNormCoord = glsl::vec2(_gfx / _gW, _gfy / _gH);\n" +
              ("    _gS._vs_main();\n" if vs_src else "") +
              "    _gS.main();\n    const glsl::vec4 _gc = _gS.gl_FragColor;\n")
    cuda = _assemble(uni, "\n".join(pre) + "\n", tr, body, kernel)
    kind = "transition" if {"startImage", "endImage", "progress"} <= {u.name for u in inputs} else \
        ("filter" if "inputImage" in {u.name for u in inputs} else "generator")
    return Translation("isf", cuda, "_g_main", uni, inputs, {"isf": meta, "kind": kind,
                                                               "passes": meta.get("PASSES") or [{}]}, tr.warnings)


def translate_file(path: str | Path) -> Translation:
    path = Path(path)
    src = path.read_text(errors="replace")
    if path.suffix == ".glsl" and "transition" in src and ("getFromColor" in src or "getToColor" in src):
        return translate_gl_transition(src, path.stem)
    if path.suffix == ".fs":
        vs = path.with_suffix(".vs")
        return translate_isf(src, vs.read_text(errors="replace") if vs.exists() else None, path.stem)
    return translate_shadertoy(src, path.stem)


if __name__ == "__main__":
    import sys
    tr = translate_file(sys.argv[1])
    print(tr.cuda[len(COMPAT_HEADER):])
