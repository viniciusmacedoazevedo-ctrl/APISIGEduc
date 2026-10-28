"""Hash das senhas de acesso à tela web (scrypt, biblioteca padrão).

Diferente da senha do SIGEduc, a senha de acesso nunca precisa ser lida de
volta, então é guardada só como hash.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_N, _R, _P = 2**14, 8, 1


def gerar_hash(senha: str) -> str:
    sal = os.urandom(16)
    h = hashlib.scrypt(senha.encode(), salt=sal, n=_N, r=_R, p=_P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_N}${_R}${_P}${b64(sal)}${b64(h)}"


def verificar(senha: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, h = guardado.split("$")
        calculado = hashlib.scrypt(
            senha.encode(), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p)
        )
        return hmac.compare_digest(calculado, base64.b64decode(h))
    except (ValueError, TypeError):
        return False
