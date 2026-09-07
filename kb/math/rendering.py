"""Mathematical rendering utilities: LaTeX to Unicode translation and SymPy 2D rendering."""

from __future__ import annotations

import re
from pathlib import Path
from tokenize import TokenError
from typing import Any

import sympy as sp
from sympy.parsing.sympy_parser import parse_expr

from ..code.checker import _expression_lines, _sympy_namespace

GREEK_MAP: dict[str, str] = {
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\delta": "δ",
    r"\epsilon": "ε",
    r"\varepsilon": "ε",
    r"\zeta": "ζ",
    r"\eta": "η",
    r"\theta": "θ",
    r"\vartheta": "ϑ",
    r"\iota": "ι",
    r"\kappa": "κ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\xi": "ξ",
    r"\pi": "π",
    r"\varpi": "ϖ",
    r"\rho": "ρ",
    r"\varrho": "ϱ",
    r"\sigma": "σ",
    r"\varsigma": "ς",
    r"\tau": "τ",
    r"\upsilon": "υ",
    r"\phi": "φ",
    r"\varphi": "ϕ",
    r"\chi": "χ",
    r"\psi": "ψ",
    r"\omega": "ω",
    r"\Gamma": "Γ",
    r"\Delta": "Δ",
    r"\Theta": "Θ",
    r"\Lambda": "Λ",
    r"\Xi": "Ξ",
    r"\Pi": "Π",
    r"\Sigma": "Σ",
    r"\Phi": "Φ",
    r"\Psi": "Ψ",
    r"\Omega": "Ω",
}

SUB_MAP: dict[str, str] = {
    "0": "₀",
    "1": "₁",
    "2": "₂",
    "3": "₃",
    "4": "₄",
    "5": "₅",
    "6": "₆",
    "7": "₇",
    "8": "₈",
    "9": "₉",
    "+": "₊",
    "-": "₋",
    "=": "₌",
    "(": "₍",
    ")": "₎",
    "a": "ₐ",
    "e": "ₑ",
    "h": "ₕ",
    "i": "ᵢ",
    "j": "ⱼ",
    "k": "ₖ",
    "l": "ₗ",
    "m": "ₘ",
    "n": "ₙ",
    "o": "ₒ",
    "p": "ₚ",
    "r": "ᵣ",
    "s": "ₛ",
    "t": "ₜ",
    "u": "ᵤ",
    "v": "ᵥ",
    "x": "ₓ",
    # Greek letters with unicode subscript equivalents
    "β": "ᵦ",
    "γ": "ᵧ",
    "ρ": "ᵨ",
    "φ": "ᵩ",
    "χ": "ᵪ",
}

SUP_MAP: dict[str, str] = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "+": "⁺",
    "-": "⁻",
    "=": "⁼",
    "(": "⁽",
    ")": "⁾",
    "a": "ᵃ",
    "b": "ᵇ",
    "c": "ᶜ",
    "d": "ᵈ",
    "e": "ᵉ",
    "f": "ᶠ",
    "g": "ᵍ",
    "h": "ʰ",
    "i": "ⁱ",
    "j": "ʲ",
    "k": "ᵏ",
    "l": "ˡ",
    "m": "ᵐ",
    "n": "ⁿ",
    "o": "ᵒ",
    "p": "ᵖ",
    "r": "ʳ",
    "s": "ˢ",
    "t": "ᵗ",
    "u": "ᵘ",
    "v": "ᵛ",
    "w": "ʷ",
    "x": "ˣ",
    "y": "ʸ",
    "z": "ᶻ",
    "A": "ᴬ",
    "B": "ᴮ",
    "D": "ᴰ",
    "E": "ᴱ",
    "G": "ᴳ",
    "H": "ᴴ",
    "I": "ᴵ",
    "J": "ᴶ",
    "K": "ᴷ",
    "L": "ᴸ",
    "M": "ᴹ",
    "N": "ᴺ",
    "O": "ᴼ",
    "P": "ᴾ",
    "R": "ᴿ",
    "T": "ᵀ",
    "U": "ᵁ",
    "V": "ⱽ",
    "W": "ᵂ",
    "dagger": "†",
}

OP_MAP: list[tuple[str, str]] = [
    # Ordering matters: long keywords and LaTeX commands before shorter sub-tokens
    (r"\left", ""),
    (r"\right", ""),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\pm", "±"),
    (r"\mp", "∓"),
    (r"\leq", "≤"),
    (r"\le", "≤"),
    (r"\geq", "≥"),
    (r"\ge", "≥"),
    (r"\neq", "≠"),
    (r"\simeq", "≃"),
    (r"\approx", "≈"),
    (r"\sim", "∼"),
    (r"\in", "∈"),
    (r"\notin", "∉"),
    (r"\subset", "⊂"),
    (r"\sum", "∑"),
    (r"\prod", "∏"),
    (r"\int", "∫"),
    (r"\partial", "∂"),
    (r"\nabla", "∇"),
    (r"\infty", "∞"),
    (r"\to", "→"),
    (r"\quad", "  "),
    (r"\qquad", "    "),
    (r"\mathcal{N}", "𝒩"),
    (r"\dagger", "†"),
    (r"\star", "⋆"),
    (r"\circ", "∘"),
]


def latex_to_unicode(text: str) -> str:
    """Convert a LaTeX formula string or symbol into a readable Unicode representation."""
    s = text

    # 1. Operators & formatting keywords (e.g. \left, \right stripped first)
    for tex, uni in OP_MAP:
        s = s.replace(tex, uni)

    # 2. Greek symbols
    for tex, uni in GREEK_MAP.items():
        s = re.sub(re.escape(tex) + r"(?![a-zA-Z])", uni, s)

    # 3. Fractions: repeated pass for nested \frac{num}{den} -> (num) / (den)
    while r"\frac{" in s:
        s_new = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1) / (\2)", s)
        if s_new == s:
            break
        s = s_new

    # 4. Square roots: \sqrt{x} -> √(x)
    while r"\sqrt{" in s:
        s_new = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", s)
        if s_new == s:
            break
        s = s_new

    # 5. Matrix environments & floor/ceil
    s = s.replace(r"\begin{bmatrix}", "[").replace(r"\end{bmatrix}", "]")
    s = s.replace(r"\begin{pmatrix}", "(").replace(r"\end{pmatrix}", ")")
    s = s.replace(r"\lfloor", "⌊").replace(r"\rfloor", "⌋")
    s = s.replace(r"\lceil", "⌈").replace(r"\rceil", "⌉")
    s = s.replace(r"\\", " ; ")
    s = s.replace("&", " ")

    # 6. Diacritics
    s = re.sub(r"\\dot\{([a-zA-Zα-ωΑ-Ω])\}", r"\1̇", s)
    s = re.sub(r"\\ddot\{([a-zA-Zα-ωΑ-Ω])\}", r"\1̈", s)
    s = re.sub(r"\\hat\{([a-zA-Zα-ωΑ-Ω])\}", r"\1̂", s)
    s = re.sub(r"\\bar\{([a-zA-Zα-ωΑ-Ω])\}", r"\1̄", s)
    s = re.sub(r"\\tilde\{([a-zA-Zα-ωΑ-Ω])\}", r"\1̃", s)

    # 7. Multi-character Subscripts _{...}
    def replace_sub_brace(m: re.Match[str]) -> str:
        content = m.group(1)
        return "".join(SUB_MAP.get(c, c) for c in content)

    s = re.sub(r"_\{([^{}]+)\}", replace_sub_brace, s)

    # 8. Multi-character Superscripts ^{...}
    def replace_sup_brace(m: re.Match[str]) -> str:
        content = m.group(1)
        if content in ("dagger", "†"):
            return "†"
        return "".join(SUP_MAP.get(c, c) for c in content)

    s = re.sub(r"\^\{([^{}]+)\}", replace_sup_brace, s)

    # 9. Single-character Subscripts _x
    def replace_single_sub(m: re.Match[str]) -> str:
        c = m.group(1)
        return SUB_MAP.get(c, f"_{c}")

    s = re.sub(r"_([a-zA-Z0-9α-ωΑ-Ω])", replace_single_sub, s)

    # 10. Single-character Superscripts ^2
    def replace_single_sup(m: re.Match[str]) -> str:
        c = m.group(1)
        return SUP_MAP.get(c, f"^{c}")

    s = re.sub(r"\^([a-zA-Z0-9†])", replace_single_sup, s)

    # 11. Clean whitespace
    s = re.sub(r" +", " ", s).strip()
    return s


def symbol_name_to_unicode(sym_name: str) -> str:
    """Convert a Python/SymPy identifier or LaTeX symbol name to Unicode."""
    if sym_name.startswith("\\"):
        return latex_to_unicode(sym_name)

    parts = sym_name.split("_", 1)
    base = parts[0]
    greek_tex = "\\" + base
    base_uni = GREEK_MAP.get(greek_tex, base)

    if len(parts) == 1:
        return base_uni

    sub = parts[1]
    sub_parts = sub.split("_")
    sub_converted_parts: list[str] = []
    for sp_item in sub_parts:
        sub_greek = GREEK_MAP.get("\\" + sp_item)
        if sub_greek:
            sub_converted_parts.append(SUB_MAP.get(sub_greek, sub_greek))
        else:
            sub_converted_parts.append("".join(SUB_MAP.get(c, c) for c in sp_item))

    sub_uni = "".join(sub_converted_parts)
    if all(c in SUB_MAP.values() for c in sub_uni):
        return f"{base_uni}{sub_uni}"
    return f"{base_uni}_{sub}"


def render_sympy_2d(path: Path) -> str | None:
    """Render a .sympy snippet file into a 2D unicode string using SymPy.

    Returns None if the file does not exist, is not readable, or cannot be parsed.
    """
    if not path.is_file():
        return None
    try:
        content = path.read_text(encoding="utf-8")
        lines = _expression_lines(content)
        if not lines:
            return None
        ns: dict[str, Any] = _sympy_namespace()
        rendered_lines: list[str] = []
        for line in lines:
            expr = parse_expr(line, local_dict={}, global_dict=ns, evaluate=False)
            rendered_lines.append(sp.pretty(expr, use_unicode=True))
        return "\n\n".join(rendered_lines)
    except (
        sp.SympifyError,
        SyntaxError,
        TokenError,
        TypeError,
        ValueError,
        OSError,
        KeyError,
        AttributeError,
    ):
        return None
