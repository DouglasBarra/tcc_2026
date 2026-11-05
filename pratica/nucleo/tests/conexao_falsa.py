"""Conexao falsa para testes OFFLINE: implementa a interface `Conexao` (driver.py) com respostas roteiradas.

Cada resposta e (trecho do SQL, resposta): a primeira cujo trecho aparece no SQL executado vence. A resposta pode ser
(colunas, linhas), um `ResultadoLote` pronto, uma excecao (e lancada) ou uma funcao `f(sql) -> ResultadoLote`.
Nunca abre socket nem toca em SQL Server.
"""
from tcclab.driver import ConjuntoResultado, ResultadoLote


def lote(colunas: list[str], linhas: list[tuple], hash_mc: str | None = None) -> ResultadoLote:
    conj = ConjuntoResultado(colunas=colunas, total_linhas=len(linhas), linhas=list(linhas), hash_multiconjunto=hash_mc)
    return ResultadoLote(conjuntos=[conj], mensagens=[], cliente_ms=0.0)


def lote_hash(hash_mc: str, n: int) -> ResultadoLote:
    """Resultado de uma consulta com `n` linhas e o hash de multiconjunto dado (o que verificar_equivalencia compara)."""
    return ResultadoLote(conjuntos=[ConjuntoResultado(colunas=["x"], total_linhas=n, hash_multiconjunto=hash_mc)], mensagens=[], cliente_ms=0.0)


class ConexaoFalsa:
    def __init__(self, respostas: list[tuple[str, object]] | None = None):
        self.respostas = respostas or []
        self.chamadas: list[str] = []
        self.fechada = False

    def executar_lote(self, sql, *, guardar_linhas=0, hash_linhas=False, hash_multiconjunto=False):
        self.chamadas.append(sql)
        for trecho, resp in self.respostas:
            if trecho in sql:
                if isinstance(resp, Exception):
                    raise resp
                if callable(resp):
                    return resp(sql)
                if isinstance(resp, ResultadoLote):
                    return resp
                colunas, linhas = resp
                return lote(colunas, linhas)
        return ResultadoLote(conjuntos=[], mensagens=[], cliente_ms=0.0)

    def fechar(self) -> None:
        self.fechada = True
